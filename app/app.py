"""
NST Phone Repair — Sistem Pakar Diagnosis Kerusakan iPhone
Flask Web Application Backend
"""
import json
import os
import sys
import copy
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4
from flask import Flask, render_template, request, jsonify, send_from_directory

# Menambahkan direktori app ke sys.path agar import lokal berjalan di Vercel
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from core.env_loader import load_env_file
load_env_file()

from core.llm_extractor import extract_features_llm
from core.ds_engine import DSEngine
from core.feature_schema import DEFAULT_FEATURES, validate_features
from core.model_validator import validate_knowledge_base
from core.rate_limiter import RateLimiter
from core.repositories import create_repository
from services.diagnosis_service import DiagnosisService, FeedbackService, ModelService

MAX_INPUT_LENGTH = 2000
SOFTWARE_KEYWORDS = (
    'lupa pola', 'flash ulang', 'flashing', 'diflash', 'software', 'sofware',
    'instal ulang', 'icloud', 'stuck logo', 'bypass', 'downgrade', 'bootloop',
    'restore ios', 'error itunes', 'itunes error', 'lupa sandi', 'lupa password',
    'stuck apple', 'logo apple aja'
)

# ---------------------------------------------------------------
# Path Setup
# ---------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DEFAULT_KB_PATH = os.path.join(DATA_DIR, 'optimal_knowledge_base.json')
MODEL_POINTER_PATH = os.path.join(DATA_DIR, 'active_model.json')


def selected_knowledge_base_path():
    if not os.path.exists(MODEL_POINTER_PATH):
        return DEFAULT_KB_PATH
    try:
        with open(MODEL_POINTER_PATH, 'r', encoding='utf-8') as pointer_file:
            relative = json.load(pointer_file).get('path', '')
        candidate = os.path.abspath(os.path.join(BASE_DIR, relative))
        allowed = os.path.abspath(os.path.join(DATA_DIR, 'models')) + os.sep
        return candidate if candidate.startswith(allowed) and os.path.isfile(candidate) else DEFAULT_KB_PATH
    except (ValueError, OSError):
        return DEFAULT_KB_PATH


KB_PATH = selected_knowledge_base_path()
EXPERT_DB_PATH = os.path.join(DATA_DIR, 'expertDiagnosesAndFix.json')

# ---------------------------------------------------------------
# Load Knowledge Base & Expert DB saat startup
# ---------------------------------------------------------------
with open(KB_PATH, 'r', encoding='utf-8') as f:
    KNOWLEDGE_BASE = json.load(f)

with open(EXPERT_DB_PATH, 'r', encoding='utf-8') as f:
    EXPERT_DB = json.load(f)

engine = validate_knowledge_base(KNOWLEDGE_BASE)
store = create_repository()
rate_limiter = RateLimiter(store)
diagnosis_service = DiagnosisService(engine, store, EXPERT_DB, KNOWLEDGE_BASE, SOFTWARE_KEYWORDS)
feedback_service = FeedbackService(store, engine)
model_service = ModelService(engine, BASE_DIR, MODEL_POINTER_PATH)


def get_json_object():
    """Ambil JSON object tanpa membiarkan array/tipe lain memicu error 500."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def clean_text_field(data, name, max_length=MAX_INPUT_LENGTH):
    value = data.get(name, "")
    if not isinstance(value, str):
        return None
    return value.strip()[:max_length]


def is_software_complaint(text):
    lowered = str(text or '').lower()
    return any(keyword in lowered for keyword in SOFTWARE_KEYWORDS)

# ---------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------
app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static')
)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024
configured_session_secret = os.environ.get('ADMIN_SESSION_SECRET') or os.environ.get('FLASK_SECRET_KEY')
if not configured_session_secret or configured_session_secret.startswith('CHANGE_'):
    configured_session_secret = os.urandom(32)
app.config['SECRET_KEY'] = configured_session_secret
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '').lower() in {'1', 'true', 'yes'}


@app.before_request
def assign_request_id():
    request.request_id = request.headers.get('X-Request-ID', '').strip()[:120] or uuid4().hex


def reload_model(model_path):
    """Validasi dan aktifkan model tanpa memutus reference blueprint admin."""
    global KNOWLEDGE_BASE
    KNOWLEDGE_BASE = model_service.reload(model_path)
    diagnosis_service.knowledge_base = KNOWLEDGE_BASE


if __package__:
    # Impor normal saat aplikasi dimuat sebagai package (app.app).
    from .admin import create_admin_blueprint
else:
    # Kompatibilitas dengan loader Vercel yang memuat app/app.py langsung.
    from admin import create_admin_blueprint
app.register_blueprint(create_admin_blueprint(store, engine, BASE_DIR, reload_model))


def client_identifier():
    forwarded = request.headers.get('X-Forwarded-For', '')
    return forwarded.split(',')[0].strip() if forwarded else (request.remote_addr or 'unknown')


def rate_limit_response():
    allowed, source = rate_limiter.allow(client_identifier())
    if allowed:
        return None, source
    response = jsonify({'success': False, 'error': 'Terlalu banyak permintaan. Silakan coba kembali nanti.'})
    response.headers['Retry-After'] = str(rate_limiter.window)
    return (response, 429), source


def knowledge_base_version():
    return str(KNOWLEDGE_BASE.get('metadata', {}).get('version', 'legacy'))


def apply_dynamic_price(expert_advice, damage_class, phone_type):
    advice = copy.deepcopy(expert_advice) if expert_advice else expert_advice
    if not advice or not hasattr(store, 'resolve_price'):
        return advice
    try:
        price = store.resolve_price(damage_class, phone_type)
    except Exception:
        price = None
    if not price:
        return advice
    minimum = int(price['minimum_price'])
    maximum = int(price['maximum_price'])
    advice['estimasi_biaya'] = {
        'rentang': f"Rp {minimum:,.0f} - Rp {maximum:,.0f}".replace(',', '.'),
        'rincian': {price['service_name']: f"Rp {minimum:,.0f} - Rp {maximum:,.0f}".replace(',', '.')},
        'sumber': 'database_admin',
        'catatan': price.get('notes') or '',
    }
    return advice


def build_clinical_knowledge(result, phone_type):
    """Satu bahasan klinis untuk diagnosis utama setiap evidence aktif."""
    selected = []
    for rule in result.get('dev_log', {}).get('active_rules', []):
        hypotheses = [
            hypothesis for hypothesis in rule.get('hypotheses', [])
            if float(hypothesis.get('optimal_belief', 0)) > 0
        ]
        if not hypotheses:
            continue
        damage = max(hypotheses, key=lambda item: float(item.get('optimal_belief', 0))).get('damage')
        if damage and damage not in selected:
            selected.append(damage)

    distribution = {item['damage']: item for item in result.get('all_diagnoses', [])}
    selected.sort(key=lambda damage: distribution.get(damage, {}).get('probability', 0), reverse=True)
    knowledge = []
    for damage in selected:
        advice = apply_dynamic_price(EXPERT_DB.get('kerusakan', {}).get(damage), damage, phone_type)
        if advice:
            probability = distribution.get(damage, {})
            knowledge.append({
                'damage': damage,
                'probability': probability.get('probability', 0),
                'percentage': probability.get('percentage', 0),
                'advice': advice,
            })
    return knowledge


@app.route('/')
@app.route('/api')
@app.route('/api/index')
def index():
    return render_template(
        'index.html',
        admin_available=getattr(store, 'backend_name', '') in {'mysql', 'supabase'} and store.configured,
    )


@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory(os.path.join(APP_DIR, 'static'), filename)


@app.route('/extract-features', methods=['POST'])
@app.route('/api/extract-features', methods=['POST'])
@app.route('/api/index/extract-features', methods=['POST'])
def extract_features():
    """Tafsirkan keluhan dahulu; pengguna wajib mengonfirmasi sebelum DS-PSO."""
    limited, limiter_source = rate_limit_response()
    if limited:
        return limited

    data = get_json_object()
    if not data:
        return jsonify({'success': False, 'error': 'Data tidak valid.'}), 400
    tipe_hp = clean_text_field(data, 'tipe_hp', 120)
    keluhan = clean_text_field(data, 'keluhan')
    if None in (tipe_hp, keluhan):
        return jsonify({'success': False, 'error': 'Semua field harus berupa teks.'}), 400
    if not tipe_hp or not keluhan:
        return jsonify({'success': False, 'error': 'Tipe ponsel dan keluhan wajib diisi.'}), 400

    result = diagnosis_service.extract_features(tipe_hp, keluhan, extract_features_llm)
    result['rate_limiter'] = limiter_source
    status_code = result.pop('status_code', 200)
    return jsonify(result), status_code

    if is_software_complaint(keluhan):
        return jsonify({
            'success': True,
            'software_detected': True,
            'requires_confirmation': False,
            'features': DEFAULT_FEATURES.copy(),
            'abnormal_features': [],
            'rate_limiter': limiter_source,
        })

    features, ai_advice, error_msg, extraction_meta = extract_features_llm(keluhan, tipe_hp)
    if error_msg:
        return jsonify({'success': False, 'error': error_msg, 'extraction': extraction_meta}), 503
    abnormal = [
        {'name': name, 'value': value}
        for name, value in features.items()
        if value != DEFAULT_FEATURES[name]
    ]
    return jsonify({
        'success': True,
        'software_detected': False,
        'requires_confirmation': True,
        'features': features,
        'abnormal_features': abnormal,
        'ai_advice': ai_advice,
        'extraction': extraction_meta,
        'rate_limiter': limiter_source,
    })


@app.route('/diagnosa', methods=['POST'])
@app.route('/api/diagnosa', methods=['POST'])
@app.route('/api/index/diagnosa', methods=['POST'])
def diagnosa():
    """
    Menerima JSON: {"nama": "...", "tipe_hp": "...", "keluhan": "..."}
    Mengembalikan JSON hasil diagnosis lengkap + dev_log.
    """
    started_at = perf_counter()
    limited, limiter_source = rate_limit_response()
    if limited:
        return limited

    data = get_json_object()
    if not data:
        return jsonify({'success': False, 'error': 'Data tidak valid.'}), 400

    nama = clean_text_field(data, 'nama', 120)
    tipe_hp = clean_text_field(data, 'tipe_hp', 120)
    keluhan_raw = clean_text_field(data, 'keluhan')

    if None in (nama, tipe_hp, keluhan_raw):
        return jsonify({'success': False, 'error': 'Semua field harus berupa teks.'}), 400

    if not nama:
        return jsonify({'success': False, 'error': 'Nama pelanggan harus diisi.'}), 400
    if not tipe_hp:
        return jsonify({'success': False, 'error': 'Tipe ponsel harus diisi.'}), 400
    if not keluhan_raw:
        return jsonify({'success': False, 'error': 'Keluhan tidak boleh kosong.'}), 400

    result = diagnosis_service.diagnose(
        nama, tipe_hp, keluhan_raw, data.get('features'), extract_features_llm
    )
    result['rate_limiter'] = limiter_source
    status_code = result.pop('status_code', 200)
    return jsonify(result), status_code

    # ---------------------------------------------------------------
    # Pintu Gerbang Filter Software (Software Gate)
    # ---------------------------------------------------------------
    is_software = is_software_complaint(keluhan_raw)

    if is_software:
        # Default empty features
        empty_features = DEFAULT_FEATURES.copy()
        response_data = {
            'success': True,
            'customer': {
                'nama': nama,
                'tipe_hp': tipe_hp,
            },
            'input': {
                'raw': keluhan_raw,
            },
            'features': empty_features,
            'top_diagnosis': 'Masalah / Kerusakan Software',
            'top_probability': 1.0,
            'top_percentage': 100.0,
            'all_diagnoses': [
                {'damage': 'Masalah / Kerusakan Software', 'probability': 1.0, 'percentage': 100.0}
            ],
            'used_fallback': False,
            'active_rules_count': 0,
            'is_software': True,
            'model_scope': 'software_keyword_filter_outside_ds_pso',
            'expert_advice': {
                'kode': 'SFT-001',
                'nama_lengkap': 'Masalah / Kerusakan Software',
                'kategori': 'Software / iOS',
                'deskripsi_umum': 'Masalah yang berkaitan dengan sistem operasi iOS, kegagalan booting (bootloop), lupa kata sandi (iCloud/Passcode), atau kesalahan firmware.',
                'gejala_utama': [
                    'Stuck logo Apple (bootloop)',
                    'Lupa pola, kata sandi, atau akun iCloud (Activation Lock)',
                    'Sering restart sendiri saat memuat aplikasi',
                    'Error saat update iOS via OTA atau iTunes'
                ],
                'penyebab_umum': [
                    'Kegagalan update sistem iOS',
                    'Memori internal terlalu penuh (stuck logo)',
                    'Kesalahan pengguna (lupa sandi/iCloud)'
                ],
                'langkah_diagnosis': [
                    '1. Coba paksa restart iPhone (Force Restart) sesuai tipe',
                    '2. Hubungkan ke komputer dengan kabel data original',
                    '3. Masuk ke mode Recovery atau DFU mode'
                ],
                'prosedur_perbaikan': [
                    'Lakukan restore iOS menggunakan iTunes atau 3uTools',
                    'Update iOS ke versi terbaru yang kompatibel',
                    'Bypass atau reset password jika sah milik pelanggan'
                ],
                'estimasi_biaya': {
                    'rentang': 'Rp 50.000 - Rp 150.000',
                    'rincian': {
                        'Restore iOS / Flashing': 'Rp 50.000 - Rp 100.000',
                        'Bypass / Reset Akun (jasa)': 'Rp 100.000 - Rp 150.000'
                    }
                },
                'tingkat_kesulitan': 'Mudah',
                'waktu_perbaikan': '15 menit - 1 jam'
            },
            'dev_log': {
                'active_rules': [],
                'combination_steps': [],
                'final_mass_combined': {},
                'pignistic_probabilities': {'Masalah / Kerusakan Software': 1.0},
                'fallback_used': False,
                'software_detected': True
            }
        }
        duration_ms = round((perf_counter() - started_at) * 1000)
        response_data['duration_ms'] = duration_ms
        response_data['rate_limiter'] = limiter_source
        response_data['persistence'] = store.save_diagnosis({
            'customer_name': nama,
            'phone_type': tipe_hp,
            'complaint': keluhan_raw,
            'features': empty_features,
            'features_confirmed': False,
            'top_diagnosis': response_data['top_diagnosis'],
            'top_probability': response_data['top_probability'],
            'all_diagnoses': response_data['all_diagnoses'],
            'ds_metrics': response_data['dev_log'],
            'is_software': True,
            'extractor_provider': 'software_keyword_filter',
            'duration_ms': duration_ms,
            'knowledge_base_version': knowledge_base_version(),
        })
        app.logger.info(
            'diagnosis_completed scope=software duration_ms=%s persisted=%s',
            duration_ms, response_data['persistence']['persistent']
        )
        return jsonify(response_data)

    # Mode penelitian dapat mengirim fitur terstruktur dan melewati NLP sepenuhnya.
    submitted_features = data.get('features')
    if submitted_features is not None:
        features, rejected = validate_features(submitted_features)
        if rejected:
            return jsonify({'success': False, 'error': 'Fitur terstruktur tidak valid.', 'rejected': rejected}), 400
        ai_advice = 'Fitur hasil interpretasi Qwen telah dikonfirmasi pengguna sebelum inferensi DS-PSO.'
        extraction_meta = {'provider': 'qwen_confirmed', 'fallback': False, 'validated': True}
    else:
        features, ai_advice, error_msg, extraction_meta = extract_features_llm(keluhan_raw, tipe_hp)
        if error_msg:
            return jsonify({'success': False, 'error': error_msg, 'extraction': extraction_meta}), 503
    result = engine.run_inference(features)

    is_general = result.get('used_fallback', False)
    if is_general:
        top_diagnosis = "Gejala Umum / Perlu Pemeriksaan"
        expert_advice = None
    else:
        top_diagnosis = result.get('top_diagnosis')
        expert_advice = apply_dynamic_price(
            EXPERT_DB.get('kerusakan', {}).get(top_diagnosis, None), top_diagnosis, tipe_hp
        )
    clinical_knowledge = [] if is_general else build_clinical_knowledge(result, tipe_hp)

    response_data = {
        'success': True,
        'customer': {
            'nama': nama,
            'tipe_hp': tipe_hp,
        },
        'input': {
            'raw': keluhan_raw,
        },
        'features': features,
        'ai_advice': ai_advice,
        'extraction': extraction_meta,
        'expert_advice': expert_advice,
        'clinical_knowledge': clinical_knowledge,
        'is_software': False,
        'model_scope': 'hardware_ds_pso',
        'is_general': is_general,
        **result,
        'top_diagnosis': top_diagnosis,
    }
    duration_ms = round((perf_counter() - started_at) * 1000)
    response_data['duration_ms'] = duration_ms
    response_data['rate_limiter'] = limiter_source
    response_data['knowledge_base_version'] = knowledge_base_version()
    response_data['persistence'] = store.save_diagnosis({
        'customer_name': nama,
        'phone_type': tipe_hp,
        'complaint': keluhan_raw,
        'features': features,
        'features_confirmed': submitted_features is not None,
        'top_diagnosis': top_diagnosis,
        'top_probability': result.get('top_probability'),
        'all_diagnoses': result.get('all_diagnoses', []),
        'ds_metrics': {
            'belief': result.get('belief', {}),
            'plausibility': result.get('plausibility', {}),
            'uncertainty_theta': result.get('uncertainty_theta'),
            'total_conflict': result.get('total_conflict'),
            'active_rules': result.get('dev_log', {}).get('active_rules', []),
        },
        'is_software': False,
        'extractor_provider': extraction_meta.get('provider'),
        'extractor_model': extraction_meta.get('model'),
        'duration_ms': duration_ms,
        'knowledge_base_version': knowledge_base_version(),
    })
    app.logger.info(
        'diagnosis_completed scope=hardware provider=%s duration_ms=%s persisted=%s',
        extraction_meta.get('provider'), duration_ms, response_data['persistence']['persistent']
    )
    return jsonify(response_data)


@app.route('/health')
@app.route('/api/health')
def health():
    database_health = {
        'backend': getattr(store, 'backend_name', 'none'),
        'configured': store.configured,
    }
    if hasattr(store, 'ping'):
        database_health['reachable'] = store.ping()
    return jsonify({
        'status': 'ok',
        'request_id': request.request_id,
        'knowledge_base': {
            'version': knowledge_base_version(),
            'rules': len(engine.rules),
            'classes': len(engine.all_damages),
            'prior_normalized': engine.prior_was_normalized,
        },
        'database': database_health,
        'nlp_provider_chain': os.environ.get(
            'NLP_PROVIDER_CHAIN', os.environ.get('NLP_PROVIDER', 'qwen')
        ),
    })


@app.route('/ready')
@app.route('/api/ready')
def readiness():
    reachable = True
    if hasattr(store, 'ping') and store.configured:
        reachable = bool(store.ping())
    configured = bool(getattr(store, 'configured', False))
    ready = (not configured) or reachable
    return jsonify({
        'status': 'ready' if ready else 'not_ready',
        'request_id': request.request_id,
        'database': {
            'backend': getattr(store, 'backend_name', 'none'),
            'configured': configured,
            'reachable': reachable,
        },
    }), 200 if ready else 503


@app.route('/model-info')
@app.route('/api/model-info')
@app.route('/api/index/model-info')
def model_info():
    """Mengembalikan ringkasan model; aturan mentah hanya untuk mode pengembangan."""
    response = {
        'class_priors': engine.priors,
        'total_rules': len(KNOWLEDGE_BASE['rules']),
        'total_classes': len(engine.priors),
        'prior_was_normalized': engine.prior_was_normalized,
        'original_prior_total': engine.original_prior_total,
        'metadata': KNOWLEDGE_BASE.get('metadata', {}),
    }
    if os.environ.get('MODEL_INFO_INCLUDE_RULES', '').lower() in {'1', 'true', 'yes'}:
        response['rules'] = KNOWLEDGE_BASE['rules']
    return jsonify(response)


@app.route('/feedback', methods=['POST'])
@app.route('/api/feedback', methods=['POST'])
@app.route('/api/index/feedback', methods=['POST'])
def feedback():
    """
    Menerima JSON berisi data kasus baru:
    {"nama": "...", "tipe_hp": "...", "keluhan": "...", "kerusakan_sebenarnya": "..."}
    Memasukkan kasus ke antrean verifikasi; tidak memutakhirkan model secara online.
    """
    limited, _ = rate_limit_response()
    if limited:
        return limited

    data = get_json_object()
    if not data:
        return jsonify({'success': False, 'error': 'Data tidak valid.'}), 400

    nama = clean_text_field(data, 'nama', 120)
    tipe_hp = clean_text_field(data, 'tipe_hp', 120)
    keluhan = clean_text_field(data, 'keluhan')
    kerusakan = clean_text_field(data, 'kerusakan_sebenarnya', 200)

    if None in (nama, tipe_hp, keluhan, kerusakan):
        return jsonify({'success': False, 'error': 'Semua field harus berupa teks.'}), 400

    if not all([nama, tipe_hp, keluhan, kerusakan]):
        return jsonify({'success': False, 'error': 'Semua field (nama, tipe_hp, keluhan, kerusakan_sebenarnya) wajib diisi.'}), 400

    result = feedback_service.submit(nama, tipe_hp, keluhan, kerusakan)
    status_code = result.pop('status_code', 200)
    return jsonify(result), status_code

    # Feedback tidak boleh langsung mengubah prior/belief sebelum diverifikasi pakar.
    if kerusakan not in engine.all_damages:
        return jsonify({'success': False, 'error': f'Kelas kerusakan {kerusakan} tidak terdaftar.'}), 400
    entry = {
        'nama': nama,
        'tipe_hp': tipe_hp,
        'keluhan': keluhan,
        'kerusakan_sebenarnya': kerusakan,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'status': 'pending_expert_verification',
    }
    persistence = store.save_feedback(entry) if store.configured else {
        'success': False, 'persistent': False, 'reason': 'database_not_configured'
    }
    persistent = persistence.get('persistent', False)
    return jsonify({
        'success': True,
        'message': (
            'Feedback masuk antrean verifikasi pakar dan belum mengubah model.'
            if persistent else
            'Feedback belum tersimpan karena database tidak tersedia; silakan coba kembali.'
        ),
        'status': entry['status'],
        'persistent': persistent,
        'storage': getattr(store, 'backend_name', 'unavailable') if persistent else 'unavailable',
    }), 202


@app.after_request
def add_header(response):
    response.headers['X-Request-ID'] = getattr(request, 'request_id', uuid4().hex)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


if __name__ == '__main__':
    print("=" * 55)
    print("  NST Phone Repair — Sistem Pakar Kerusakan iPhone")
    print("  Buka browser: http://localhost:5000")
    print("=" * 55)
    app.run(debug=True, port=5000)

# Trigger auto-reload: JSON updated

