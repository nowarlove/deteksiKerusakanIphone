"""Repository Supabase/PostgREST untuk API publik dan panel admin."""

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from datetime import datetime, timezone


class SupabaseStore:
    backend_name = "supabase"

    def __init__(self):
        self.url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        self.key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        self.anon_key = os.environ.get("SUPABASE_ANON_KEY", "")
        self.timeout = float(os.environ.get("SUPABASE_TIMEOUT_SECONDS", "5"))
        self.store_details = os.environ.get(
            "SUPABASE_STORE_DIAGNOSIS_DETAILS", "true"
        ).lower() in {"1", "true", "yes"}

    @property
    def configured(self):
        return bool(self.url and self.key)

    def _rest(self, method, path, payload=None, prefer="return=representation", headers=False):
        if not self.configured:
            return (None, {}) if headers else None
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}/rest/v1/{path.lstrip('/')}", data=data, method=method,
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Prefer": prefer,
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8")
            result = json.loads(raw) if raw else None
            return (result, dict(response.headers)) if headers else result

    # Dipertahankan untuk kompatibilitas integrasi lama dan test.
    def _request(self, path, payload, prefer="return=minimal"):
        return self._rest("POST", path, payload, prefer)

    @staticmethod
    def _query(**items):
        values = [(key, value) for key, value in items.items() if value is not None]
        return "?" + urllib.parse.urlencode(values, safe="(),.*:") if values else ""

    def _select(self, table, select="*", **filters):
        return self._rest("GET", table + self._query(select=select, **filters)) or []

    def _one(self, table, select="*", **filters):
        rows = self._select(table, select, limit=1, **filters)
        return rows[0] if rows else None

    def _count(self, table, **filters):
        _, headers = self._rest(
            "GET", table + self._query(select="id", limit=1, **filters),
            prefer="count=exact", headers=True,
        )
        content_range = headers.get("Content-Range", headers.get("content-range", "0/0"))
        try:
            return int(content_range.rsplit("/", 1)[1])
        except (IndexError, ValueError):
            return 0

    def insert(self, table, record):
        try:
            response = self._request(table, record, "return=representation")
            row = response[0] if isinstance(response, list) and response else {}
            return {"success": True, "persistent": True, **row}
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            return {
                "success": False, "persistent": False, "error": type(exc).__name__,
                "status_code": getattr(exc, "code", None),
            }

    def ping(self):
        try:
            self._select("admin_profiles", "id", limit=1)
            return True
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
            return False

    def save_diagnosis(self, result):
        case_number = datetime.now(timezone.utc).strftime("NST-%Y%m%d-") + uuid.uuid4().hex[:8].upper()
        record = {
            "case_number": case_number,
            "phone_type": str(result.get("phone_type", ""))[:120],
            "features": result.get("features", {}),
            "features_confirmed": bool(result.get("features_confirmed", False)),
            "top_diagnosis": str(result.get("top_diagnosis", ""))[:200],
            "top_probability": result.get("top_probability"),
            "all_diagnoses": result.get("all_diagnoses", []),
            "ds_metrics": result.get("ds_metrics", {}),
            "is_software": bool(result.get("is_software", False)),
            "extractor_provider": str(result.get("extractor_provider", ""))[:80],
            "extractor_model": str(result.get("extractor_model", ""))[:120],
            "duration_ms": result.get("duration_ms"),
            "knowledge_base_version": str(result.get("knowledge_base_version", "legacy"))[:120],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if self.store_details:
            record.update({
                "customer_name": str(result.get("customer_name", ""))[:120],
                "complaint": str(result.get("complaint", ""))[:2000],
            })
        saved = self.insert("diagnosis_history", record)
        if saved.get("success"):
            saved["case_number"] = case_number
        return saved

    def save_feedback(self, entry):
        return self.insert("feedback_cases", {
            "nama": str(entry.get("nama", ""))[:120],
            "tipe_hp": str(entry.get("tipe_hp", ""))[:120],
            "keluhan": str(entry.get("keluhan", ""))[:2000],
            "kerusakan_sebenarnya": str(entry.get("kerusakan_sebenarnya", ""))[:200],
            "status": entry.get("status", "pending_expert_verification"),
            "created_at": entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
        })

    def check_rate_limit(self, identifier, limit, window_seconds):
        try:
            response = self._request(
                "rpc/check_rate_limit",
                {"p_identifier": identifier, "p_limit": limit, "p_window_seconds": window_seconds},
                "return=representation",
            )
            return bool(response)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
            return None

    # ---------- Supabase Auth ----------
    def authenticate_admin(self, email, password):
        api_key = self.anon_key or self.key
        if not api_key or not email or not password:
            return None
        request = urllib.request.Request(
            f"{self.url}/auth/v1/token?grant_type=password",
            data=json.dumps({"email": email, "password": password}).encode("utf-8"),
            method="POST", headers={"apikey": api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                auth_data = json.loads(response.read().decode("utf-8"))
            user_id = auth_data.get("user", {}).get("id")
            profile = self._one("admin_profiles", id=f"eq.{user_id}") if user_id else None
            if not profile or not profile.get("is_active"):
                return None
            profile["email"] = auth_data.get("user", {}).get("email", email)
            profile["expires_in"] = int(auth_data.get("expires_in", 3600))
            return profile
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
            return None

    def record_login(self, admin_id, success):
        if success:
            self._rest(
                "PATCH", "admin_profiles" + self._query(id=f"eq.{admin_id}"),
                {"last_login_at": datetime.now(timezone.utc).isoformat()}, "return=minimal",
            )

    # ---------- Normalisasi panel admin ----------
    @staticmethod
    def _diagnosis(row):
        row = dict(row or {})
        verification = row.pop("case_verifications", None)
        if isinstance(verification, list):
            verification = verification[0] if verification else {}
        row.update(verification or {})
        row.setdefault("verification_status", "pending")
        row["case_number"] = row.get("case_number") or str(row.get("id", ""))
        row["customer_name"] = row.get("customer_name") or ""
        row["complaint"] = row.get("complaint") or ""
        row["features_json"] = row.get("features") or {}
        row["all_diagnoses_json"] = row.get("all_diagnoses") or []
        row["ds_metrics_json"] = row.get("ds_metrics") or {}
        row["model_version"] = row.get("knowledge_base_version", "legacy")
        return row

    @staticmethod
    def _job(row):
        row = dict(row or {})
        row["parameters_json"] = row.get("parameters") or {}
        row["metrics_json"] = row.get("metrics") or {}
        return row

    @staticmethod
    def _model(row):
        row = dict(row or {})
        row["metrics_json"] = row.get("metrics") or {}
        row["metadata_json"] = row.get("metadata") or {}
        return row

    def dashboard_stats(self):
        today = datetime.now(timezone.utc).date().isoformat()
        diagnoses = self._select("diagnosis_history", "top_diagnosis", limit=5000)
        classes = Counter(row.get("top_diagnosis") for row in diagnoses if row.get("top_diagnosis"))
        return {
            "diagnoses": self._count("diagnosis_history"),
            "today": self._count("diagnosis_history", created_at=f"gte.{today}T00:00:00Z"),
            "pending": self._count("case_verifications", verification_status="eq.pending"),
            "verified": self._count("case_verifications", verification_status="eq.verified"),
            "jobs": self._count("training_jobs", status="in.(queued,running)"),
            "classes": [{"top_diagnosis": name, "total": total} for name, total in classes.most_common(10)],
        }

    def list_diagnoses(self, query="", status="", page=1, per_page=25):
        filters = {"order": "created_at.desc", "limit": per_page, "offset": (page - 1) * per_page}
        if query:
            safe = re.sub(r"[^\w @.+-]", "", query, flags=re.UNICODE)
            filters["or"] = (
                f"(case_number.ilike.*{safe}*,customer_name.ilike.*{safe}*,"
                f"phone_type.ilike.*{safe}*,top_diagnosis.ilike.*{safe}*)"
            )
        if status:
            filters["case_verifications.verification_status"] = f"eq.{status}"
            filters["case_verifications"] = "not.is.null"
        select = "*,case_verifications(verification_status,actual_diagnosis)"
        rows, headers = self._rest(
            "GET", "diagnosis_history" + self._query(select=select, **filters),
            prefer="count=exact", headers=True,
        )
        content_range = headers.get("Content-Range", headers.get("content-range", "0/0"))
        try:
            total = int(content_range.rsplit("/", 1)[1])
        except (IndexError, ValueError):
            total = len(rows or [])
        return [self._diagnosis(row) for row in (rows or [])], total

    def get_diagnosis(self, diagnosis_id):
        select = "*,case_verifications(verification_status,actual_diagnosis,expert_notes,verified_by,verified_at)"
        row = self._one("diagnosis_history", select, id=f"eq.{diagnosis_id}")
        item = self._diagnosis(row) if row else None
        if item and item.get("verified_by"):
            profile = self._one("admin_profiles", "username", id=f"eq.{item['verified_by']}")
            item["verified_by_name"] = (profile or {}).get("username")
        return item

    def verify_case(self, diagnosis_id, status, actual, notes, admin_id):
        before = self.get_diagnosis(diagnosis_id)
        payload = {
            "diagnosis_id": str(diagnosis_id), "verification_status": status,
            "actual_diagnosis": actual or None, "expert_notes": notes or None,
            "verified_by": str(admin_id), "verified_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._rest(
            "POST", "case_verifications" + self._query(on_conflict="diagnosis_id"), payload,
            "resolution=merge-duplicates,return=minimal",
        )
        return before, self.get_diagnosis(diagnosis_id)

    def list_prices(self):
        return self._select("repair_prices", order="damage_class.asc,phone_model_pattern.asc,service_name.asc")

    def get_price(self, price_id):
        return self._one("repair_prices", id=f"eq.{price_id}") if price_id else None

    def save_price(self, data, admin_id, price_id=None):
        payload = {**data, "updated_by": str(admin_id), "updated_at": datetime.now(timezone.utc).isoformat()}
        if price_id:
            rows = self._rest("PATCH", "repair_prices" + self._query(id=f"eq.{price_id}"), payload, "return=representation") or []
            return rows[0]["id"] if rows else price_id
        rows = self._rest("POST", "repair_prices", payload, "return=representation") or []
        return rows[0]["id"]

    def delete_price(self, price_id):
        return self._rest("DELETE", "repair_prices" + self._query(id=f"eq.{price_id}"), prefer="return=minimal")

    def resolve_price(self, damage_class, phone_type):
        now = datetime.now(timezone.utc).isoformat()
        rows = self._select(
            "repair_prices", damage_class=f"eq.{damage_class}", is_active="eq.true",
            effective_from=f"lte.{now}", order="updated_at.desc", limit=200,
        )
        matches = [row for row in rows if not row.get("effective_until") or row["effective_until"] > now]
        matches = [row for row in matches if row.get("phone_model_pattern") == "*" or row.get("phone_model_pattern", "").lower() in phone_type.lower()]
        matches.sort(key=lambda row: (row.get("phone_model_pattern") != "*", len(row.get("phone_model_pattern", ""))), reverse=True)
        return matches[0] if matches else None

    def create_training_job(self, job_type, parameters, admin_id):
        rows = self._rest("POST", "training_jobs", {
            "job_type": job_type, "parameters": parameters, "started_by": str(admin_id),
        }, "return=representation") or []
        return rows[0]["id"]

    def update_training_job(self, job_id, **fields):
        mapping = {"parameters_json": "parameters", "metrics_json": "metrics"}
        payload = {mapping.get(key, key): value for key, value in fields.items()}
        return self._rest("PATCH", "training_jobs" + self._query(id=f"eq.{job_id}"), payload, "return=minimal")

    def list_training_jobs(self, limit=50):
        return [self._job(row) for row in self._select("training_jobs", order="created_at.desc", limit=limit)]

    def get_training_job(self, job_id):
        row = self._one("training_jobs", id=f"eq.{job_id}")
        return self._job(row) if row else None

    def verified_training_rows(self):
        verifications = self._select(
            "case_verifications", "diagnosis_id,actual_diagnosis,verified_at",
            verification_status="eq.verified", actual_diagnosis="not.is.null", order="verified_at.asc",
        )
        rows = []
        for verification in verifications:
            diagnosis = self._one("diagnosis_history", "phone_type,complaint,features", id=f"eq.{verification['diagnosis_id']}")
            if diagnosis:
                rows.append({
                    "phone_type": diagnosis.get("phone_type", ""),
                    "complaint": diagnosis.get("complaint", ""),
                    "features_json": diagnosis.get("features") or {},
                    "actual_diagnosis": verification["actual_diagnosis"],
                })
        return rows

    def register_dataset(self, version, source, row_count, path, sha256, admin_id, status="validated"):
        rows = self._rest("POST", "training_datasets", {
            "version": version, "source": source, "row_count": row_count, "file_path": path,
            "sha256": sha256, "status": status, "created_by": str(admin_id),
        }, "return=representation") or []
        return rows[0]["id"]

    def list_datasets(self):
        return self._select("training_datasets", order="created_at.desc")

    def register_model(self, version, path, dataset_hash, metrics, metadata, admin_id):
        rows = self._rest("POST", "model_versions", {
            "version": version, "knowledge_base_path": path, "dataset_hash": dataset_hash,
            "metrics": metrics or {}, "metadata": metadata or {}, "approved_by": str(admin_id),
        }, "return=representation") or []
        return rows[0]["id"]

    def list_models(self):
        return [self._model(row) for row in self._select("model_versions", order="created_at.desc")]

    def get_model(self, model_id):
        row = self._one("model_versions", id=f"eq.{model_id}")
        return self._model(row) if row else None

    def activate_model(self, model_id, admin_id):
        return self._request(
            "rpc/activate_model_version",
            {"p_model_id": str(model_id), "p_admin_id": str(admin_id)},
            "return=minimal",
        )

    def audit(self, admin_id, action, entity_type, entity_id, before, after, ip_hash):
        return self.insert("audit_logs", {
            "admin_id": str(admin_id) if admin_id else None, "action": action,
            "entity_type": entity_type, "entity_id": str(entity_id),
            "before_data": before or {}, "after_data": after or {}, "ip_hash": ip_hash,
        })


def hash_identifier(value):
    salt = os.environ.get("RATE_LIMIT_SALT", "development-only-change-me")
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()
