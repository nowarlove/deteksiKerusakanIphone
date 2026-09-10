"""Business workflows for diagnosis, feedback, and model lifecycle."""

import copy
import json
import os
from datetime import datetime, timezone
from time import perf_counter

try:
    from ..core.feature_schema import DEFAULT_FEATURES, validate_features
    from ..core.model_validator import validate_knowledge_base
except ImportError:
    from core.feature_schema import DEFAULT_FEATURES, validate_features
    from core.model_validator import validate_knowledge_base


class DiagnosisService:
    def __init__(self, engine, store, expert_db, knowledge_base, software_keywords):
        self.engine = engine
        self.store = store
        self.expert_db = expert_db
        self.knowledge_base = knowledge_base
        self.software_keywords = software_keywords

    def is_software_complaint(self, text):
        lowered = str(text or '').lower()
        return any(keyword in lowered for keyword in self.software_keywords)

    def knowledge_base_version(self):
        return str(self.knowledge_base.get('metadata', {}).get('version', 'legacy'))

    def apply_dynamic_price(self, expert_advice, damage_class, phone_type):
        advice = copy.deepcopy(expert_advice) if expert_advice else expert_advice
        if not advice or not hasattr(self.store, 'resolve_price'):
            return advice
        try:
            price = self.store.resolve_price(damage_class, phone_type)
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

    def build_clinical_knowledge(self, result, phone_type):
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
            advice = self.apply_dynamic_price(self.expert_db.get('kerusakan', {}).get(damage), damage, phone_type)
            if advice:
                probability = distribution.get(damage, {})
                knowledge.append({
                    'damage': damage,
                    'probability': probability.get('probability', 0),
                    'percentage': probability.get('percentage', 0),
                    'advice': advice,
                })
        return knowledge

    def confidence_policy(self, result):
        candidates = result.get('all_diagnoses', [])
        top_probability = float(result.get('top_probability', 0))
        next_probability = float(candidates[1].get('probability', 0)) if len(candidates) > 1 else 0.0
        margin = max(0.0, top_probability - next_probability)
        uncertainty = float(result.get('uncertainty_theta', 0) or 0)
        conflicts = result.get('dev_log', {}).get('conflict_steps', [])
        conflict = max((float(item.get('conflict_k', 0)) for item in conflicts), default=0.0)
        if result.get('used_fallback'):
            status = 'evidence_insufficient'
            label = 'Evidence tidak cukup'
        elif uncertainty >= 0.2 or conflict >= 0.5 or margin < 0.1:
            status = 'technician_review'
            label = 'Perlu pemeriksaan teknisi'
        elif top_probability < 0.5:
            status = 'early_indication'
            label = 'Indikasi awal'
        else:
            status = 'preliminary_indication'
            label = 'Indikasi awal dengan dukungan evidence'
        return {
            'status': status,
            'label': label,
            'policy_version': 'confidence-v1',
            'top_probability': top_probability,
            'top_two_margin': margin,
            'uncertainty_theta': uncertainty,
            'maximum_conflict': conflict,
            'requires_technician': status in {'evidence_insufficient', 'technician_review'},
        }

    def extract_features(self, tipe_hp, keluhan, extractor):
        if self.is_software_complaint(keluhan):
            return {
                'success': True,
                'software_detected': True,
                'requires_confirmation': False,
                'features': DEFAULT_FEATURES.copy(),
                'abnormal_features': [],
            }
        features, ai_advice, error_msg, extraction_meta = extractor(keluhan, tipe_hp)
        if error_msg:
            return {
                'success': False,
                'error': error_msg,
                'extraction': extraction_meta,
                'status_code': 503,
            }
        abnormal = [
            {'name': name, 'value': value}
            for name, value in features.items()
            if value != DEFAULT_FEATURES[name]
        ]
        return {
            'success': True,
            'software_detected': False,
            'requires_confirmation': True,
            'features': features,
            'abnormal_features': abnormal,
            'ai_advice': ai_advice,
            'extraction': extraction_meta,
        }

    def _software_result(self, nama, tipe_hp, keluhan):
        empty_features = DEFAULT_FEATURES.copy()
        expert_advice = {
            'kode': 'SFT-001',
            'nama_lengkap': 'Masalah / Kerusakan Software',
            'kategori': 'Software / iOS',
            'deskripsi_umum': 'Masalah yang berkaitan dengan sistem operasi iOS, kegagalan booting (bootloop), lupa kata sandi (iCloud/Passcode), atau kesalahan firmware.',
            'gejala_utama': ['Stuck logo Apple (bootloop)', 'Lupa pola, kata sandi, atau akun iCloud (Activation Lock)', 'Sering restart sendiri saat memuat aplikasi', 'Error saat update iOS via OTA atau iTunes'],
            'penyebab_umum': ['Kegagalan update sistem iOS', 'Memori internal terlalu penuh (stuck logo)', 'Kesalahan pengguna (lupa sandi/iCloud)'],
            'langkah_diagnosis': ['1. Coba paksa restart iPhone (Force Restart) sesuai tipe', '2. Hubungkan ke komputer dengan kabel data original', '3. Masuk ke mode Recovery atau DFU mode'],
            'prosedur_perbaikan': ['Lakukan restore iOS menggunakan iTunes atau 3uTools', 'Update iOS ke versi terbaru yang kompatibel', 'Bypass atau reset password jika sah milik pelanggan'],
            'estimasi_biaya': {'rentang': 'Rp 50.000 - Rp 150.000', 'rincian': {'Restore iOS / Flashing': 'Rp 50.000 - Rp 100.000', 'Bypass / Reset Akun (jasa)': 'Rp 100.000 - Rp 150.000'}},
            'tingkat_kesulitan': 'Mudah',
            'waktu_perbaikan': '15 menit - 1 jam',
        }
        return {
            'success': True,
            'customer': {'nama': nama, 'tipe_hp': tipe_hp},
            'input': {'raw': keluhan},
            'features': empty_features,
            'top_diagnosis': 'Masalah / Kerusakan Software',
            'top_probability': 1.0,
            'top_percentage': 100.0,
            'all_diagnoses': [{'damage': 'Masalah / Kerusakan Software', 'probability': 1.0, 'percentage': 100.0}],
            'used_fallback': False,
            'active_rules_count': 0,
            'is_software': True,
            'model_scope': 'software_keyword_filter_outside_ds_pso',
            'expert_advice': expert_advice,
            'dev_log': {'active_rules': [], 'combination_steps': [], 'final_mass_combined': {}, 'pignistic_probabilities': {'Masalah / Kerusakan Software': 1.0}, 'fallback_used': False, 'software_detected': True},
        }

    def diagnose(self, nama, tipe_hp, keluhan, submitted_features, extractor):
        started_at = perf_counter()
        if self.is_software_complaint(keluhan):
            response = self._software_result(nama, tipe_hp, keluhan)
            features = response['features']
            extraction_meta = {'provider': 'software_keyword_filter'}
            ai_advice = None
        elif submitted_features is not None:
            features, rejected = validate_features(submitted_features)
            if rejected:
                return {'success': False, 'error': 'Fitur terstruktur tidak valid.', 'rejected': rejected, 'status_code': 400}
            response = None
            extraction_meta = {'provider': 'qwen_confirmed', 'fallback': False, 'validated': True}
            ai_advice = 'Fitur hasil interpretasi Qwen telah dikonfirmasi pengguna sebelum inferensi DS-PSO.'
        else:
            features, ai_advice, error_msg, extraction_meta = extractor(keluhan, tipe_hp)
            if error_msg:
                return {'success': False, 'error': error_msg, 'extraction': extraction_meta, 'status_code': 503}
            response = None

        if response is None:
            result = self.engine.run_inference(features)
            is_general = result.get('used_fallback', False)
            top_diagnosis = 'Gejala Umum / Perlu Pemeriksaan' if is_general else result.get('top_diagnosis')
            expert_advice = None if is_general else self.apply_dynamic_price(self.expert_db.get('kerusakan', {}).get(top_diagnosis), top_diagnosis, tipe_hp)
            response = {
                'success': True,
                'customer': {'nama': nama, 'tipe_hp': tipe_hp},
                'input': {'raw': keluhan},
                'features': features,
                'ai_advice': ai_advice,
                'extraction': extraction_meta,
                'expert_advice': expert_advice,
                'clinical_knowledge': [] if is_general else self.build_clinical_knowledge(result, tipe_hp),
                'is_software': False,
                'model_scope': 'hardware_ds_pso',
                'is_general': is_general,
                **result,
                'top_diagnosis': top_diagnosis,
            }
            response['confidence'] = self.confidence_policy(result)
        duration_ms = round((perf_counter() - started_at) * 1000)
        response['duration_ms'] = duration_ms
        response['knowledge_base_version'] = self.knowledge_base_version()
        response['persistence'] = self.store.save_diagnosis({
            'customer_name': nama,
            'phone_type': tipe_hp,
            'complaint': keluhan,
            'features': features,
            'features_confirmed': submitted_features is not None,
            'top_diagnosis': response['top_diagnosis'],
            'top_probability': response['top_probability'],
            'all_diagnoses': response['all_diagnoses'],
            'ds_metrics': {
                **response['dev_log'],
                'confidence': response.get('confidence'),
            },
            'is_software': response['is_software'],
            'extractor_provider': extraction_meta.get('provider'),
            'extractor_model': extraction_meta.get('model'),
            'duration_ms': duration_ms,
            'knowledge_base_version': self.knowledge_base_version(),
        })
        return response


class FeedbackService:
    def __init__(self, store, engine):
        self.store = store
        self.engine = engine

    def submit(self, nama, tipe_hp, keluhan, kerusakan):
        if kerusakan not in self.engine.all_damages:
            return {'success': False, 'error': f'Kelas kerusakan {kerusakan} tidak terdaftar.', 'status_code': 400}
        entry = {
            'nama': nama,
            'tipe_hp': tipe_hp,
            'keluhan': keluhan,
            'kerusakan_sebenarnya': kerusakan,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'status': 'pending_expert_verification',
        }
        persistence = self.store.save_feedback(entry) if self.store.configured else {'success': False, 'persistent': False, 'reason': 'database_not_configured'}
        persistent = persistence.get('persistent', False)
        return {
            'success': True,
            'message': 'Feedback masuk antrean verifikasi pakar dan belum mengubah model.' if persistent else 'Feedback belum tersimpan karena database tidak tersedia; silakan coba kembali.',
            'status': entry['status'],
            'persistent': persistent,
            'storage': getattr(self.store, 'backend_name', 'unavailable') if persistent else 'unavailable',
            'status_code': 202,
        }


class ModelService:
    def __init__(self, engine, base_dir, pointer_path):
        self.engine = engine
        self.base_dir = base_dir
        self.pointer_path = pointer_path

    def reload(self, model_path):
        with open(model_path, 'r', encoding='utf-8') as model_file:
            candidate = json.load(model_file)
        fresh = validate_knowledge_base(candidate)
        self.engine.__dict__.clear()
        self.engine.__dict__.update(fresh.__dict__)
        relative = os.path.relpath(model_path, self.base_dir).replace('\\', '/')
        with open(self.pointer_path, 'w', encoding='utf-8') as pointer_file:
            json.dump({'path': relative, 'activated_at': datetime.now(timezone.utc).isoformat()}, pointer_file, indent=2)
        return candidate
