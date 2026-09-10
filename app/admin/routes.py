import csv
import hashlib
import json
import math
import os
import shutil
import time
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

try:
    from ..core.feature_schema import FEATURE_NAMES
except ImportError:
    # Saat app/app.py dimuat langsung oleh runtime serverless.
    from core.feature_schema import FEATURE_NAMES
from .auth import admin_required, csrf_token, hash_ip, login_required, validate_csrf
from .training_service import TrainingService


def create_admin_blueprint(repository, engine, root_dir, reload_model):
    bp = Blueprint("admin", __name__, url_prefix="/admin", template_folder="../templates")
    training = TrainingService(repository, root_dir)

    def require_admin_backend():
        backend = getattr(repository, "backend_name", "")
        if backend not in {"mysql", "supabase"}:
            abort(503, "Backend panel admin belum dikonfigurasi.")
        if hasattr(repository, "ping") and not repository.ping():
            abort(503, f"{backend.title()} tidak dapat diakses. Periksa environment variable dan migration database.")

    def require_local_filesystem():
        if os.environ.get("VERCEL") == "1":
            abort(409, "Operasi model/training dijalankan pada worker lokal, bukan fungsi Vercel.")

    def audit(action, entity_type, entity_id, before=None, after=None):
        repository.audit(
            session.get("admin_id"), action, entity_type, entity_id, before or {}, after or {},
            hash_ip(request.remote_addr or "unknown"),
        )

    @bp.app_context_processor
    def inject_admin_helpers():
        return {"csrf_token": csrf_token, "admin_session": session}

    @bp.route("/login", methods=["GET", "POST"])
    def login():
        require_admin_backend()
        if request.method == "POST":
            validate_csrf()
            username = request.form.get("username", "").strip()[:120]
            password = request.form.get("password", "")
            if getattr(repository, "backend_name", "") == "supabase":
                user = repository.authenticate_admin(username, password)
                authenticated = bool(user)
            else:
                user = repository.get_admin_by_username(username)
                locked = bool(user and user.get("locked_until") and user["locked_until"] > datetime.utcnow())
                authenticated = bool(
                    user and user.get("is_active") and not locked
                    and check_password_hash(user["password_hash"], password)
                )
            if authenticated:
                repository.record_login(user["id"], True)
                session.clear()
                session["admin_id"] = user["id"]
                session["admin_username"] = user["username"]
                session["admin_role"] = user["role"]
                session["admin_expires_at"] = int(time.time()) + min(int(user.get("expires_in", 3600)), 8 * 3600)
                csrf_token()
                repository.audit(
                    user["id"], "login", "admin_user", user["id"], {}, {"success": True},
                    hash_ip(request.remote_addr or "unknown"),
                )
                return redirect(url_for("admin.dashboard"))
            if user and getattr(repository, "backend_name", "") == "mysql":
                repository.record_login(user["id"], False)
            flash("Akun terkunci sementara atau kredensial salah.", "error")
        return render_template(
            "admin/login.html",
            identity_label="Email Supabase" if getattr(repository, "backend_name", "") == "supabase" else "Username",
        )

    @bp.post("/logout")
    @login_required
    def logout():
        validate_csrf()
        session.clear()
        return redirect(url_for("admin.login"))

    @bp.get("/")
    @login_required
    def dashboard():
        require_admin_backend()
        return render_template("admin/dashboard.html", stats=repository.dashboard_stats())

    @bp.get("/diagnoses")
    @login_required
    def diagnoses():
        query = request.args.get("q", "").strip()[:120]
        status = request.args.get("status", "").strip()
        page = max(1, request.args.get("page", 1, type=int))
        rows, total = repository.list_diagnoses(query, status, page)
        return render_template("admin/diagnoses.html", rows=rows, total=total, page=page,
                               pages=max(1, math.ceil(total / 25)), query=query, status=status)

    @bp.route("/diagnoses/<diagnosis_id>", methods=["GET", "POST"])
    @login_required
    def diagnosis_detail(diagnosis_id):
        item = repository.get_diagnosis(diagnosis_id)
        if not item:
            abort(404)
        if request.method == "POST":
            validate_csrf()
            status = request.form.get("status", "")
            actual = request.form.get("actual_diagnosis", "").strip()[:200]
            notes = request.form.get("expert_notes", "").strip()[:2000]
            if status not in {"pending", "verified", "rejected", "needs_revision"}:
                abort(400, "Status verifikasi tidak valid.")
            if status == "verified" and actual not in engine.all_damages:
                abort(400, "Diagnosis aktual harus menggunakan kelas model.")
            before, after = repository.verify_case(diagnosis_id, status, actual, notes, session["admin_id"])
            audit("verify_case", "diagnosis", diagnosis_id, before, after)
            flash("Verifikasi kasus disimpan.", "success")
            return redirect(url_for("admin.diagnosis_detail", diagnosis_id=diagnosis_id))
        return render_template("admin/diagnosis_detail.html", item=item, damage_classes=engine.all_damages)

    @bp.route("/prices", methods=["GET", "POST"])
    @admin_required
    def prices():
        if request.method == "POST":
            validate_csrf()
            price_id = request.form.get("price_id", "").strip() or None
            minimum = request.form.get("minimum_price", type=float)
            maximum = request.form.get("maximum_price", type=float)
            if minimum is None or maximum is None or minimum < 0 or maximum < minimum:
                abort(400, "Rentang harga tidak valid.")
            data = {
                "damage_class": request.form.get("damage_class", "").strip()[:200],
                "phone_model_pattern": request.form.get("phone_model_pattern", "*").strip()[:120] or "*",
                "service_name": request.form.get("service_name", "").strip()[:200],
                "minimum_price": minimum, "maximum_price": maximum, "currency": "IDR",
                "notes": request.form.get("notes", "").strip()[:2000],
                "is_active": request.form.get("is_active") == "on",
            }
            if data["damage_class"] not in engine.all_damages or not data["service_name"]:
                abort(400, "Kelas atau nama layanan tidak valid.")
            before = repository.get_price(price_id) if price_id else {}
            saved_id = repository.save_price(data, session["admin_id"], price_id)
            audit("save_price", "repair_price", saved_id, before, repository.get_price(saved_id))
            flash("Harga berhasil disimpan.", "success")
            return redirect(url_for("admin.prices"))
        edit_id = request.args.get("edit", "").strip() or None
        return render_template("admin/prices.html", prices=repository.list_prices(),
                               damage_classes=engine.all_damages,
                               edit_price=repository.get_price(edit_id) if edit_id else None)

    @bp.post("/prices/<price_id>/delete")
    @admin_required
    def delete_price(price_id):
        validate_csrf()
        before = repository.get_price(price_id)
        repository.delete_price(price_id)
        audit("delete_price", "repair_price", price_id, before, {})
        flash("Harga dihapus.", "success")
        return redirect(url_for("admin.prices"))

    @bp.post("/datasets/build")
    @admin_required
    def build_dataset():
        validate_csrf()
        require_local_filesystem()
        if not training.available:
            abort(409, "Pembuatan dataset hanya tersedia pada worker lokal.")
        verified = repository.verified_training_rows()
        if not verified:
            flash("Belum ada kasus verified untuk ditambahkan.", "error")
            return redirect(url_for("admin.training_page"))
        base_path = os.path.join(root_dir, "data", "dataLatih", "dataLatih-2026-08-29-20-08.csv")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output_dir = os.path.join(root_dir, "data", "staging")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"dataLatih-verified-{timestamp}.csv")
        with open(base_path, "r", encoding="utf-8-sig", newline="") as source:
            base_rows = list(csv.DictReader(source))
        fields = list(base_rows[0])
        additions = []
        for row in verified:
            features = row["features_json"]
            additions.append({
                "Tipe HP": row["phone_type"], **{name: features[name] for name in FEATURE_NAMES},
                "Raw_Text": row["complaint"], "Kerusakan": row["actual_diagnosis"],
            })
        with open(output_path, "w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=fields)
            writer.writeheader(); writer.writerows(base_rows + additions)
        with open(output_path, "rb") as dataset_file:
            digest = hashlib.sha256(dataset_file.read()).hexdigest()
        relative = os.path.relpath(output_path, root_dir)
        repository.register_dataset(timestamp, "base_train_plus_verified", len(base_rows) + len(additions), relative, digest, session["admin_id"])
        audit("build_dataset", "training_dataset", timestamp, {}, {"path": relative, "rows": len(base_rows) + len(additions)})
        flash(f"Dataset {timestamp} dibuat dengan {len(additions)} kasus verified.", "success")
        return redirect(url_for("admin.training_page"))

    @bp.route("/training", methods=["GET", "POST"])
    @admin_required
    def training_page():
        if request.method == "POST":
            validate_csrf()
            job_type = request.form.get("job_type", "")
            params = {
                "particles": request.form.get("particles", 30, type=int),
                "iterations": request.form.get("iterations", 100, type=int),
                "seeds": request.form.get("seeds", "42,43,44"),
                "train_path": request.form.get("train_path", ""),
            }
            try:
                job_id = training.enqueue(job_type, params, session["admin_id"])
                audit("enqueue_training", "training_job", job_id, {}, params)
                flash(f"Training job #{job_id} masuk antrean.", "success")
            except (ValueError, RuntimeError) as exc:
                flash(str(exc), "error")
            return redirect(url_for("admin.training_page"))
        return render_template("admin/training.html", jobs=repository.list_training_jobs(),
                               datasets=repository.list_datasets(), worker_available=training.available)

    @bp.get("/training/<job_id>/log")
    @admin_required
    def training_log(job_id):
        job = repository.get_training_job(job_id)
        if not job or not job.get("log_path"):
            abort(404)
        path = os.path.abspath(job["log_path"])
        allowed = os.path.abspath(training.output_root) + os.sep
        if not path.startswith(allowed) or not os.path.isfile(path):
            abort(404)
        with open(path, "r", encoding="utf-8", errors="replace") as log_file:
            content = log_file.read()[-50000:]
        return render_template("admin/log.html", job=job, content=content)

    @bp.get("/models")
    @admin_required
    def models():
        return render_template(
            "admin/models.html", models=repository.list_models(), jobs=repository.list_training_jobs(),
            local_model_actions=os.environ.get("VERCEL") != "1",
        )

    @bp.post("/models/register-current")
    @admin_required
    def register_current_model():
        validate_csrf()
        require_local_filesystem()
        source = os.path.join(root_dir, "data", "optimal_knowledge_base.json")
        with open(source, "r", encoding="utf-8") as source_file:
            current = json.load(source_file)
        version = "baseline-original"
        try:
            model_id = repository.register_model(
                version, os.path.relpath(source, root_dir), current.get("metadata", {}).get("train_sha256"),
                current.get("metadata", {}).get("optimized_test_metrics", {}), current.get("metadata", {}), session["admin_id"],
            )
            audit("register_model", "model_version", model_id, {}, {"version": version})
            flash("Model baseline terdaftar untuk opsi rollback.", "success")
        except Exception:
            flash("Model baseline sudah terdaftar.", "error")
        return redirect(url_for("admin.models"))

    @bp.post("/models/register/<job_id>")
    @admin_required
    def register_model(job_id):
        validate_csrf()
        require_local_filesystem()
        job = repository.get_training_job(job_id)
        if not job or job.get("status") != "succeeded" or not job.get("artifact_path"):
            abort(400, "Job belum memiliki kandidat model.")
        source = os.path.abspath(job["artifact_path"])
        allowed = os.path.abspath(training.output_root) + os.sep
        if not source.startswith(allowed) or not os.path.isfile(source):
            abort(400, "Path artefak tidak valid.")
        version = datetime.now(timezone.utc).strftime("model-%Y%m%d-%H%M%S")
        model_dir = os.path.join(root_dir, "data", "models")
        os.makedirs(model_dir, exist_ok=True)
        destination = os.path.join(model_dir, f"{version}.json")
        shutil.copy2(source, destination)
        with open(destination, "r", encoding="utf-8") as model_file:
            candidate = json.load(model_file)
        # Validasi engine sebelum mendaftarkan artefak.
        engine.__class__(candidate)
        metadata = candidate.get("metadata", {})
        active_evidence = {(rule["symptom_col"], rule["symptom_val"]) for rule in engine.rules}
        candidate_evidence = {(rule["symptom_col"], rule["symptom_val"]) for rule in candidate["rules"]}
        if candidate_evidence != active_evidence:
            abort(400, "Schema evidence kandidat tidak kompatibel dengan model aktif.")
        baseline = metadata.get("baseline_test_metrics", {})
        optimized = metadata.get("optimized_test_metrics", {})
        for metric in ("accuracy", "macro_f1"):
            if metric not in baseline or metric not in optimized or optimized[metric] < baseline[metric]:
                abort(400, f"Kandidat tidak melewati baseline untuk metrik {metric}.")
        metadata["version"] = version
        candidate["metadata"] = metadata
        with open(destination, "w", encoding="utf-8") as model_file:
            json.dump(candidate, model_file, ensure_ascii=False, indent=2)
        metrics = metadata.get("optimized_test_metrics", job.get("metrics_json") or {})
        model_id = repository.register_model(version, os.path.relpath(destination, root_dir), metadata.get("train_sha256"), metrics, metadata, session["admin_id"])
        audit("register_model", "model_version", model_id, {}, {"version": version})
        flash(f"Kandidat {version} terdaftar. Aktivasi dilakukan terpisah.", "success")
        return redirect(url_for("admin.models"))

    @bp.post("/models/<model_id>/activate")
    @admin_required
    def activate_model(model_id):
        validate_csrf()
        require_local_filesystem()
        model = repository.get_model(model_id)
        if not model:
            abort(404)
        repository.activate_model(model_id, session["admin_id"])
        absolute = os.path.abspath(os.path.join(root_dir, model["knowledge_base_path"]))
        models_allowed = os.path.abspath(os.path.join(root_dir, "data", "models")) + os.sep
        baseline_allowed = os.path.abspath(os.path.join(root_dir, "data", "optimal_knowledge_base.json"))
        if not absolute.startswith(models_allowed) and absolute != baseline_allowed:
            abort(400, "Model berada di lokasi yang tidak diizinkan.")
        reload_model(absolute)
        audit("activate_model", "model_version", model_id, {}, {"version": model["version"]})
        flash(f"Model {model['version']} aktif. Ini juga berfungsi sebagai rollback.", "success")
        return redirect(url_for("admin.models"))

    return bp
