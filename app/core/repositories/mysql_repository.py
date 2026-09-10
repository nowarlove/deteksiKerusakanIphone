"""Repository MySQL/MariaDB untuk XAMPP dan admin panel."""

import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:  # dependency opsional ketika backend bukan mysql
    pymysql = None
    DictCursor = None


def _json_default(value):
    """Convert common database values safely for JSON audit columns."""
    if isinstance(value, Decimal):
        # Preserve exact monetary/probability values rather than rounding to float.
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def _decode_rows(rows, fields):
    for row in rows:
        for field in fields:
            value = row.get(field)
            if isinstance(value, str):
                try:
                    row[field] = json.loads(value)
                except ValueError:
                    pass
    return rows


class MySQLRepository:
    backend_name = "mysql"

    def __init__(self):
        self.host = os.environ.get("MYSQL_HOST", "127.0.0.1")
        self.port = int(os.environ.get("MYSQL_PORT", "3306"))
        self.database = os.environ.get("MYSQL_DATABASE", "iphone_diagnosis")
        self.user = os.environ.get("MYSQL_USER", "")
        self.password = os.environ.get("MYSQL_PASSWORD", "")
        self.timeout = int(os.environ.get("MYSQL_CONNECT_TIMEOUT_SECONDS", "5"))

    @property
    def configured(self):
        return bool(pymysql and self.host and self.database and self.user)

    def connect(self):
        if not self.configured:
            raise RuntimeError("MySQL belum dikonfigurasi atau PyMySQL belum terpasang.")
        return pymysql.connect(
            host=self.host, port=self.port, user=self.user, password=self.password,
            database=self.database, charset="utf8mb4", cursorclass=DictCursor,
            connect_timeout=self.timeout, autocommit=False,
        )

    def _execute(self, sql, params=(), fetch=None):
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                result = cursor.fetchone() if fetch == "one" else cursor.fetchall() if fetch == "all" else cursor.lastrowid
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ping(self):
        try:
            row = self._execute("select 1 as ok", fetch="one")
            return bool(row and row["ok"] == 1)
        except Exception:
            return False

    def save_diagnosis(self, result):
        case_number = datetime.now(timezone.utc).strftime("NST-%Y%m%d-") + uuid.uuid4().hex[:8].upper()
        connection = None
        try:
            connection = self.connect()
            with connection.cursor() as cursor:
                cursor.execute(
                    """insert into diagnosis_history
                    (case_number,customer_name,phone_type,complaint,features_json,features_confirmed,
                     top_diagnosis,top_probability,all_diagnoses_json,ds_metrics_json,is_software,
                     extractor_provider,extractor_model,duration_ms,model_version)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (case_number, str(result.get("customer_name", ""))[:120], str(result.get("phone_type", ""))[:120],
                     str(result.get("complaint", ""))[:2000], _json(result.get("features", {})),
                     bool(result.get("features_confirmed", False)), str(result.get("top_diagnosis", ""))[:200],
                     result.get("top_probability"), _json(result.get("all_diagnoses", [])),
                     _json(result.get("ds_metrics", {})), bool(result.get("is_software", False)),
                     str(result.get("extractor_provider", ""))[:80], str(result.get("extractor_model", ""))[:120],
                     result.get("duration_ms"), str(result.get("knowledge_base_version", "legacy"))[:120]),
                )
                record_id = cursor.lastrowid
                cursor.execute("insert into case_verifications(diagnosis_id) values (%s)", (record_id,))
            connection.commit()
            return {"success": True, "persistent": True, "id": record_id, "case_number": case_number}
        except Exception as exc:
            if connection:
                connection.rollback()
            return {"success": False, "persistent": False, "error": type(exc).__name__}
        finally:
            if connection:
                connection.close()

    def save_feedback(self, entry):
        return {"success": False, "persistent": False, "reason": "use_case_verification"}

    def check_rate_limit(self, identifier, limit, window_seconds):
        window_epoch = int(datetime.now(timezone.utc).timestamp() // window_seconds * window_seconds)
        window = datetime.fromtimestamp(window_epoch, timezone.utc).replace(tzinfo=None)
        try:
            self._execute(
                """insert into api_rate_limits(identifier,window_start,request_count) values(%s,%s,1)
                on duplicate key update request_count=request_count+1""", (identifier, window),
            )
            row = self._execute(
                "select request_count from api_rate_limits where identifier=%s and window_start=%s",
                (identifier, window), fetch="one",
            )
            return row["request_count"] <= limit
        except Exception:
            return None

    def get_admin_by_username(self, username):
        return self._execute("select * from admin_users where username=%s", (username,), fetch="one")

    def create_admin(self, username, password_hash, role="admin"):
        return self._execute(
            "insert into admin_users(username,password_hash,role) values(%s,%s,%s)",
            (username, password_hash, role),
        )

    def record_login(self, admin_id, success):
        if success:
            self._execute("update admin_users set failed_login_count=0,locked_until=null,last_login_at=utc_timestamp() where id=%s", (admin_id,))
        else:
            self._execute(
                """update admin_users set failed_login_count=failed_login_count+1,
                locked_until=case when failed_login_count+1>=5 then date_add(utc_timestamp(),interval 15 minute) else locked_until end
                where id=%s""", (admin_id,),
            )

    def dashboard_stats(self):
        return {
            "diagnoses": self._execute("select count(*) total from diagnosis_history", fetch="one")["total"],
            "today": self._execute("select count(*) total from diagnosis_history where date(created_at)=utc_date()", fetch="one")["total"],
            "pending": self._execute("select count(*) total from case_verifications where verification_status='pending'", fetch="one")["total"],
            "verified": self._execute("select count(*) total from case_verifications where verification_status='verified'", fetch="one")["total"],
            "jobs": self._execute("select count(*) total from training_jobs where status in ('queued','running')", fetch="one")["total"],
            "classes": self._execute("select top_diagnosis,count(*) total from diagnosis_history group by top_diagnosis order by total desc limit 10", fetch="all"),
        }

    def list_diagnoses(self, query="", status="", page=1, per_page=25):
        where, params = [], []
        if query:
            where.append("(d.case_number like %s or d.customer_name like %s or d.phone_type like %s or d.top_diagnosis like %s)")
            params.extend([f"%{query}%"] * 4)
        if status:
            where.append("v.verification_status=%s")
            params.append(status)
        clause = " where " + " and ".join(where) if where else ""
        total = self._execute(f"select count(*) total from diagnosis_history d left join case_verifications v on v.diagnosis_id=d.id{clause}", tuple(params), fetch="one")["total"]
        params.extend([per_page, (page - 1) * per_page])
        rows = self._execute(
            f"""select d.*,v.verification_status,v.actual_diagnosis from diagnosis_history d
            left join case_verifications v on v.diagnosis_id=d.id{clause}
            order by d.created_at desc limit %s offset %s""", tuple(params), fetch="all",
        )
        return _decode_rows(rows, ("features_json", "all_diagnoses_json", "ds_metrics_json")), total

    def get_diagnosis(self, diagnosis_id):
        row = self._execute(
            """select d.*,v.verification_status,v.actual_diagnosis,v.expert_notes,v.verified_at,
            a.username verified_by_name from diagnosis_history d left join case_verifications v on v.diagnosis_id=d.id
            left join admin_users a on a.id=v.verified_by where d.id=%s""", (diagnosis_id,), fetch="one",
        )
        return _decode_rows([row], ("features_json", "all_diagnoses_json", "ds_metrics_json"))[0] if row else None

    def verify_case(self, diagnosis_id, status, actual, notes, admin_id):
        before = self.get_diagnosis(diagnosis_id)
        self._execute(
            """update case_verifications set verification_status=%s,actual_diagnosis=%s,expert_notes=%s,
            verified_by=%s,verified_at=utc_timestamp() where diagnosis_id=%s""",
            (status, actual or None, notes or None, admin_id, diagnosis_id),
        )
        return before, self.get_diagnosis(diagnosis_id)

    def list_prices(self):
        return self._execute("select * from repair_prices order by damage_class,phone_model_pattern,service_name", fetch="all")

    def get_price(self, price_id):
        return self._execute("select * from repair_prices where id=%s", (price_id,), fetch="one")

    def save_price(self, data, admin_id, price_id=None):
        values = (data["damage_class"], data["phone_model_pattern"], data["service_name"],
                  data["minimum_price"], data["maximum_price"], data.get("currency", "IDR"),
                  data.get("notes"), bool(data.get("is_active", True)), admin_id)
        if price_id:
            self._execute(
                """update repair_prices set damage_class=%s,phone_model_pattern=%s,service_name=%s,
                minimum_price=%s,maximum_price=%s,currency=%s,notes=%s,is_active=%s,updated_by=%s where id=%s""",
                values + (price_id,),
            )
            return price_id
        return self._execute(
            """insert into repair_prices(damage_class,phone_model_pattern,service_name,minimum_price,
            maximum_price,currency,notes,is_active,updated_by) values(%s,%s,%s,%s,%s,%s,%s,%s,%s)""", values,
        )

    def delete_price(self, price_id):
        return self._execute("delete from repair_prices where id=%s", (price_id,))

    def resolve_price(self, damage_class, phone_type):
        return self._execute(
            """select * from repair_prices where damage_class=%s and is_active=1
            and (phone_model_pattern='*' or %s like concat('%%',phone_model_pattern,'%%'))
            and effective_from<=utc_timestamp() and (effective_until is null or effective_until>utc_timestamp())
            order by (phone_model_pattern<>'*') desc,length(phone_model_pattern) desc,updated_at desc limit 1""",
            (damage_class, phone_type), fetch="one",
        )

    def create_training_job(self, job_type, parameters, admin_id):
        return self._execute(
            "insert into training_jobs(job_type,parameters_json,started_by) values(%s,%s,%s)",
            (job_type, _json(parameters), admin_id),
        )

    def update_training_job(self, job_id, **fields):
        allowed = {"status", "progress", "log_path", "artifact_path", "metrics_json", "started_at", "finished_at", "error_message"}
        items = [(key, value) for key, value in fields.items() if key in allowed]
        if not items:
            return
        values = [_json(value) if key == "metrics_json" and not isinstance(value, str) else value for key, value in items]
        self._execute("update training_jobs set " + ",".join(f"{key}=%s" for key, _ in items) + " where id=%s", tuple(values + [job_id]))

    def list_training_jobs(self, limit=50):
        rows = self._execute("select j.*,a.username started_by_name from training_jobs j left join admin_users a on a.id=j.started_by order by j.created_at desc limit %s", (limit,), fetch="all")
        return _decode_rows(rows, ("parameters_json", "metrics_json"))

    def get_training_job(self, job_id):
        rows = self._execute("select * from training_jobs where id=%s", (job_id,), fetch="all")
        decoded = _decode_rows(rows, ("parameters_json", "metrics_json"))
        return decoded[0] if decoded else None

    def verified_training_rows(self):
        rows = self._execute(
            """select d.phone_type,d.complaint,d.features_json,v.actual_diagnosis from diagnosis_history d
            join case_verifications v on v.diagnosis_id=d.id
            where v.verification_status='verified' and v.actual_diagnosis is not null order by v.verified_at""",
            fetch="all",
        )
        return _decode_rows(rows, ("features_json",))

    def register_dataset(self, version, source, row_count, path, sha256, admin_id, status="validated"):
        return self._execute(
            """insert into training_datasets(version,source,row_count,file_path,sha256,status,created_by)
            values(%s,%s,%s,%s,%s,%s,%s)""",
            (version, source, row_count, path, sha256, status, admin_id),
        )

    def list_datasets(self):
        return self._execute("select * from training_datasets order by created_at desc", fetch="all")

    def register_model(self, version, path, dataset_hash, metrics, metadata, admin_id):
        return self._execute(
            """insert into model_versions(version,knowledge_base_path,dataset_hash,metrics_json,metadata_json,approved_by)
            values(%s,%s,%s,%s,%s,%s)""", (version, path, dataset_hash, _json(metrics), _json(metadata), admin_id),
        )

    def list_models(self):
        rows = self._execute("select m.*,a.username approved_by_name from model_versions m left join admin_users a on a.id=m.approved_by order by m.created_at desc", fetch="all")
        return _decode_rows(rows, ("metrics_json", "metadata_json"))

    def get_model(self, model_id):
        rows = self._execute("select * from model_versions where id=%s", (model_id,), fetch="all")
        decoded = _decode_rows(rows, ("metrics_json", "metadata_json"))
        return decoded[0] if decoded else None

    def activate_model(self, model_id, admin_id):
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("update model_versions set is_active=0")
                cursor.execute("update model_versions set is_active=1,approved_by=%s,approved_at=utc_timestamp() where id=%s", (admin_id, model_id))
                if cursor.rowcount != 1:
                    raise ValueError("Model tidak ditemukan.")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def audit(self, admin_id, action, entity_type, entity_id, before, after, ip_hash):
        return self._execute(
            "insert into audit_logs(admin_id,action,entity_type,entity_id,before_json,after_json,ip_hash) values(%s,%s,%s,%s,%s,%s,%s)",
            (admin_id, action, entity_type, str(entity_id), _json(before), _json(after), ip_hash),
        )
