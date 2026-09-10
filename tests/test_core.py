import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, Mock, patch
from flask import Flask

from app.app import app, engine
from app.core.feature_schema import (
    DEFAULT_FEATURES, DEPLOYMENT_SCHEMA, FEATURE_NAMES, FEATURE_OPTIONS, INITIAL_BELIEF,
    OPTIMAL_KNOWLEDGE_BASE, TRAIN_ROWS, TEST_ROWS, dataset_prompt_context, validate_features,
)
from app.core.llm_extractor import extract_features_llm
from app.core.ds_engine import DSEngine
from app.core.model_validator import validate_knowledge_base
from app.core.rate_limiter import RateLimiter
from app.core.supabase_store import SupabaseStore
from app.core.repositories.mysql_repository import MySQLRepository, _json
from app.admin.training_service import TrainingService
from app.admin.routes import create_admin_blueprint


class FeatureTests(unittest.TestCase):
    def test_mysql_audit_json_supports_database_values(self):
        payload = json.loads(_json({
            "price": Decimal("150000.00"),
            "created_at": datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
        }))
        self.assertEqual(payload["price"], "150000.00")
        self.assertEqual(payload["created_at"], "2026-09-05T12:00:00+00:00")

    def test_schema_is_derived_from_research_datasets(self):
        self.assertEqual(len(FEATURE_OPTIONS), 13)
        expected_size = DEPLOYMENT_SCHEMA["dataset_size"]
        if TRAIN_ROWS and TEST_ROWS:
            self.assertEqual(len(TRAIN_ROWS), expected_size["train"])
            self.assertEqual(len(TEST_ROWS), expected_size["test"])
        else:
            # GitHub/Vercel tidak membawa dataset mentah; schema agregat tetap wajib lengkap.
            self.assertEqual(expected_size, {"train": 736, "test": 185})
        self.assertEqual(tuple(FEATURE_OPTIONS), FEATURE_NAMES)

    def test_belief_and_optimal_rules_have_identical_evidence(self):
        initial = {(r["symptom_col"], r["symptom_val"]) for r in INITIAL_BELIEF["rules"]}
        optimal = {(r["symptom_col"], r["symptom_val"]) for r in OPTIMAL_KNOWLEDGE_BASE["rules"]}
        self.assertEqual(initial, optimal)
        self.assertEqual(len(optimal), 18)
        self.assertIn(("Wifi", "Sinyal Lemah/Tidak Stabil"), optimal)
        self.assertIn(("Wifi", "Hilang Total/Tidak Dapat Diaktifkan"), optimal)

    def test_qwen_context_uses_aligned_aggregate_schema(self):
        context = dataset_prompt_context()
        self.assertEqual(context["defaults"], DEFAULT_FEATURES)
        self.assertIn(
            {"feature": "Touchscreen", "value": "Tidak bisa disentuh"},
            context["ds_pso_evidence"],
        )
        self.assertNotIn("dataset_examples", context)

    def test_invalid_provider_value_is_rejected(self):
        features, rejected = validate_features({"LCD": "Pasti Rusak"})
        self.assertEqual(features["LCD"], DEFAULT_FEATURES["LCD"])
        self.assertEqual(len(rejected), 1)

    @patch("app.core.llm_extractor._request_json")
    @patch.dict(os.environ, {
        "NLP_PROVIDER_CHAIN": "qwen",
        "QWEN_API_KEY": "test-key-not-real",
        "QWEN_MODEL": "qwen-plus",
        "QWEN_BASE_URL": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    }, clear=False)
    def test_qwen_json_output_is_validated(self, request_json):
        provider_features = DEFAULT_FEATURES.copy()
        provider_features["Speaker"] = "Rusak"
        request_json.return_value = {
            "choices": [{"message": {"content": json.dumps({
                "features": provider_features,
                "ai_advice": "Periksa speaker secara fisik.",
            })}}]
        }
        features, advice, error, meta = extract_features_llm("speaker pecah", "iPhone 12")
        self.assertIsNone(error)
        self.assertEqual(features["Speaker"], "Rusak")
        self.assertEqual(meta["provider"], "qwen")
        self.assertTrue(meta["validated"])
        self.assertIn("speaker", advice.lower())
        args, kwargs = request_json.call_args
        self.assertEqual(args[0], "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions")
        self.assertEqual(args[2]["Authorization"], "Bearer test-key-not-real")

    @patch("app.core.llm_extractor._request_json")
    @patch.dict(os.environ, {
        "NLP_PROVIDER_CHAIN": "qwen", "QWEN_API_KEY": "test-key-not-real",
    }, clear=False)
    def test_qwen_flat_json_output_is_supported(self, request_json):
        provider_output = DEFAULT_FEATURES.copy()
        provider_output["Touchscreen"] = "Tidak bisa disentuh"
        provider_output["ai_advice"] = "Periksa respons layar sentuh."
        request_json.return_value = {
            "choices": [{"message": {"content": json.dumps(provider_output)}}]
        }
        features, _, error, meta = extract_features_llm("layar tidak merespons sentuhan", "iPhone 11")
        self.assertIsNone(error)
        self.assertEqual(features["Touchscreen"], "Tidak bisa disentuh")
        self.assertEqual(meta["provider"], "qwen")

    @patch("app.core.llm_extractor._request_json")
    @patch.dict(os.environ, {
        "NLP_PROVIDER_CHAIN": "sumopod,qwen",
        "SUMOPOD_API_KEY": "test-key-not-real",
        "SUMOPOD_MODEL": "glm-5",
        "SUMOPOD_BASE_URL": "https://ai.sumopod.com/v1",
    }, clear=False)
    def test_sumopod_is_used_as_primary_openai_compatible_provider(self, request_json):
        provider_features = DEFAULT_FEATURES.copy()
        provider_features["Wifi"] = "Hilang Total/Tidak Dapat Diaktifkan"
        line_output = "\n".join(
            [f"{name}: {value}" for name, value in provider_features.items()]
            + ["ai_advice: Periksa IC WTR."]
        )
        request_json.return_value = {
            "choices": [{"message": {"content": line_output}}]
        }
        features, _, error, meta = extract_features_llm("wifi tidak bisa diaktifkan", "iPhone 11")
        self.assertIsNone(error)
        self.assertEqual(features["Wifi"], "Hilang Total/Tidak Dapat Diaktifkan")
        self.assertEqual(meta["provider"], "sumopod")
        self.assertFalse(meta["fallback"])
        self.assertEqual(request_json.call_args.args[0], "https://ai.sumopod.com/v1/chat/completions")
        self.assertEqual(len(request_json.call_args.args[1]["messages"]), 1)
        self.assertEqual(request_json.call_args.args[1]["messages"][0]["role"], "user")
        self.assertNotIn("response_format", request_json.call_args.args[1])

    @patch.dict(os.environ, {
        "NLP_PROVIDER_CHAIN": "qwen",
        "NLP_LOCAL_FALLBACK": "false",
    }, clear=False)
    def test_qwen_failure_does_not_guess_locally(self):
        def failed(*_):
            raise RuntimeError("provider unavailable")

        with patch.dict("app.core.llm_extractor.PROVIDERS", {"qwen": failed}, clear=True):
            features, _, error, meta = extract_features_llm("speaker pecah", "iPhone 12")

        self.assertIsNone(features)
        self.assertIn("Provider NLP gagal", error)
        self.assertIsNone(meta["provider"])
        self.assertFalse(meta["fallback"])
        self.assertEqual(len(meta["attempts"]), 1)


class DSEngineTests(unittest.TestCase):
    def test_runtime_priors_are_normalized(self):
        self.assertAlmostEqual(sum(engine.priors.values()), 1.0, places=9)

    def test_fallback_probability_is_normalized(self):
        result = engine.run_inference(DEFAULT_FEATURES)
        self.assertAlmostEqual(sum(x["probability"] for x in result["all_diagnoses"]), 1.0, places=3)

    def test_active_evidence_metrics_are_bounded(self):
        features = DEFAULT_FEATURES.copy()
        features["LCD"] = "Gelap"
        result = engine.run_inference(features)
        self.assertGreater(result["active_rules_count"], 0)
        self.assertTrue(0 <= result["uncertainty_theta"] <= 1)
        self.assertAlmostEqual(sum(result["dev_log"]["pignistic_probabilities"].values()), 1.0, places=3)
        top = result["top_diagnosis"]
        self.assertLessEqual(result["belief"][top], result["plausibility"][top])

    def test_high_uncertainty_evidence_is_exposed(self):
        features = DEFAULT_FEATURES.copy()
        features["Tombol"] = "Tidak Berfungsi"
        result = engine.run_inference(features)
        self.assertGreaterEqual(result["uncertainty_theta"], 0.2)

    def test_two_evidences_produce_two_distinct_clinical_topics(self):
        from app.app import build_clinical_knowledge
        features = DEFAULT_FEATURES.copy()
        features["Touchscreen"] = "Tidak bisa disentuh"
        features["Konektor Cas"] = "Keluar masuk"
        result = engine.run_inference(features)
        topics = build_clinical_knowledge(result, "iPhone 11")
        self.assertEqual([item["damage"] for item in topics], ["LCD Rusak", "Port Pengisian Rusak"])

    def test_manual_two_evidence_combination(self):
        theta = engine.Theta
        first = {frozenset([engine.all_damages[0]]): 0.6, theta: 0.4}
        second = {frozenset([engine.all_damages[0]]): 0.5, theta: 0.5}
        combined, conflict = engine._combine_two(first, second)
        self.assertAlmostEqual(conflict, 0.0)
        self.assertAlmostEqual(combined[frozenset([engine.all_damages[0]])], 0.8)
        self.assertAlmostEqual(combined[theta], 0.2)

    def test_total_conflict_is_explicit(self):
        first = {frozenset([engine.all_damages[0]]): 1.0}
        second = {frozenset([engine.all_damages[1]]): 1.0}
        combined, conflict = engine._combine_two(first, second)
        self.assertEqual(conflict, 1.0)
        self.assertEqual(combined, {engine.Theta: 1.0})


class ModelValidationTests(unittest.TestCase):
    def test_production_model_matches_runtime_contract(self):
        validated = validate_knowledge_base(OPTIMAL_KNOWLEDGE_BASE)
        self.assertEqual(validated.all_damages, engine.all_damages)

    def test_model_with_unknown_class_is_rejected(self):
        candidate = json.loads(json.dumps(OPTIMAL_KNOWLEDGE_BASE))
        candidate["class_priors"]["Unknown Damage"] = 0.01
        with self.assertRaisesRegex(ValueError, "Kelas model"):
            validate_knowledge_base(candidate)

    def test_model_with_changed_evidence_is_rejected(self):
        candidate = json.loads(json.dumps(OPTIMAL_KNOWLEDGE_BASE))
        candidate["rules"][0]["symptom_val"] = "Normal"
        with self.assertRaisesRegex(ValueError, "Pasangan evidence"):
            validate_knowledge_base(candidate)

    def test_model_with_invalid_metadata_is_rejected(self):
        candidate = json.loads(json.dumps(OPTIMAL_KNOWLEDGE_BASE))
        candidate["metadata"] = "invalid"
        with self.assertRaisesRegex(ValueError, "Metadata"):
            validate_knowledge_base(candidate)

    def test_corrupt_model_pointer_falls_back_to_default(self):
        app_module = __import__("app.app", fromlist=["selected_knowledge_base_path"])
        with tempfile.TemporaryDirectory() as temp_dir:
            pointer_path = os.path.join(temp_dir, "active_model.json")
            with open(pointer_path, "w", encoding="utf-8") as pointer_file:
                pointer_file.write("not-json")
            with patch.object(app_module, "MODEL_POINTER_PATH", pointer_path):
                self.assertEqual(
                    app_module.selected_knowledge_base_path(), app_module.DEFAULT_KB_PATH
                )


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    @patch("app.app.extract_features_llm")
    def test_hardware_diagnosis_uses_qwen_features(self, extractor):
        features = DEFAULT_FEATURES.copy()
        features["LCD"] = "Gelap"
        extractor.return_value = (features, "Periksa layar.", None, {"provider": "qwen"})
        with patch.dict(os.environ, {"NLP_PROVIDER": "qwen"}, clear=False):
            response = self.client.post("/diagnosa", json={
                "nama": "Budi", "tipe_hp": "iPhone 12", "keluhan": "layar gelap"
            })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        self.assertEqual(response.get_json()["extraction"]["provider"], "qwen")

    @patch("app.app.extract_features_llm")
    def test_extract_then_confirm_features_before_diagnosis(self, extractor):
        features = DEFAULT_FEATURES.copy()
        features["Touchscreen"] = "Tidak bisa disentuh"
        extractor.return_value = (
            features, "Konfirmasi kondisi layar sentuh.", None,
            {"provider": "qwen", "model": "qwen-plus", "validated": True},
        )
        extraction = self.client.post("/extract-features", json={
            "tipe_hp": "iPhone 14", "keluhan": "layar susah disentuh",
        })
        payload = extraction.get_json()
        self.assertEqual(extraction.status_code, 200)
        self.assertTrue(payload["requires_confirmation"])
        self.assertEqual(payload["abnormal_features"][0]["name"], "Touchscreen")

        diagnosis = self.client.post("/diagnosa", json={
            "nama": "Budi", "tipe_hp": "iPhone 14", "keluhan": "layar susah disentuh",
            "features": payload["features"],
        })
        self.assertEqual(diagnosis.status_code, 200)
        self.assertEqual(diagnosis.get_json()["extraction"]["provider"], "qwen_confirmed")
        self.assertGreater(diagnosis.get_json()["active_rules_count"], 0)

    def test_software_skips_feature_confirmation(self):
        response = self.client.post("/extract-features", json={
            "tipe_hp": "iPhone 12", "keluhan": "stuck logo apple",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["software_detected"])
        self.assertFalse(response.get_json()["requires_confirmation"])

    def test_non_object_json_is_rejected(self):
        response = self.client.post("/diagnosa", json=["invalid"])
        self.assertEqual(response.status_code, 400)

    def test_non_string_field_is_rejected(self):
        response = self.client.post("/diagnosa", json={"nama": 1, "tipe_hp": "iPhone", "keluhan": "layar gelap"})
        self.assertEqual(response.status_code, 400)

    def test_manual_structured_features_bypass_nlp(self):
        features = DEFAULT_FEATURES.copy()
        features["LCD"] = "Gelap"
        response = self.client.post("/diagnosa", json={
            "nama": "Peneliti", "tipe_hp": "iPhone 12", "keluhan": "input terstruktur",
            "features": features,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["extraction"]["provider"], "qwen_confirmed")

    def test_feedback_does_not_change_priors(self):
        before = engine.priors.copy()
        damage = engine.all_damages[0]
        response = self.client.post("/feedback", json={
            "nama": "Budi", "tipe_hp": "iPhone 12", "keluhan": "uji",
            "kerusakan_sebenarnya": damage,
        })
        self.assertEqual(response.status_code, 202)
        self.assertEqual(before, engine.priors)
        self.assertFalse(response.get_json()["persistent"])

    def test_health_and_security_headers(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")
        self.assertTrue(response.headers.get('X-Request-ID'))
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_readiness_returns_database_state(self):
        response = self.client.get('/api/ready', headers={'X-Request-ID': 'test-request'})
        self.assertIn(response.status_code, (200, 503))
        self.assertEqual(response.get_json()['request_id'], 'test-request')

    def test_model_info_hides_rules_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MODEL_INFO_INCLUDE_RULES", None)
            response = self.client.get("/model-info")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("rules", response.get_json())

    def test_model_info_can_include_rules_in_development(self):
        with patch.dict(os.environ, {"MODEL_INFO_INCLUDE_RULES": "true"}, clear=False):
            response = self.client.get("/model-info")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["rules"]), len(engine.rules))


class ProductionIntegrationTests(unittest.TestCase):
    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
    }, clear=False)
    def test_feedback_is_mapped_to_supabase_schema(self):
        store = SupabaseStore()
        with patch.object(store, "_request", return_value=None) as request_call:
            result = store.save_feedback({
                "nama": "Budi", "tipe_hp": "iPhone 12", "keluhan": "layar gelap",
                "kerusakan_sebenarnya": "LCD Rusak", "status": "pending_expert_verification",
                "timestamp": "2026-08-29T00:00:00+00:00",
            })
        self.assertTrue(result["persistent"])
        table, payload = request_call.call_args.args[:2]
        self.assertEqual(table, "feedback_cases")
        self.assertEqual(payload["created_at"], "2026-08-29T00:00:00+00:00")
        self.assertNotIn("timestamp", payload)

    def test_rate_limiter_prefers_distributed_store(self):
        fake_store = Mock()
        fake_store.backend_name = "supabase"
        fake_store.check_rate_limit.return_value = True
        limiter = RateLimiter(fake_store)
        allowed, source = limiter.allow("127.0.0.1")
        self.assertTrue(allowed)
        self.assertEqual(source, "supabase")

    @patch.dict(os.environ, {
        "MYSQL_USER": "iphone_app", "MYSQL_DATABASE": "iphone_diagnosis",
    }, clear=False)
    def test_mysql_diagnosis_payload_is_persisted(self):
        repository = MySQLRepository()
        features = DEFAULT_FEATURES.copy()
        features["LCD"] = "Gelap"
        connection = Mock()
        cursor = Mock()
        cursor.lastrowid = 101
        cursor_manager = MagicMock()
        cursor_manager.__enter__.return_value = cursor
        connection.cursor.return_value = cursor_manager
        with patch.object(repository, "connect", return_value=connection):
            result = repository.save_diagnosis({
                "customer_name": "Budi", "phone_type": "iPhone 12",
                "complaint": "layar gelap", "features": features,
                "features_confirmed": True, "top_diagnosis": "LCD Rusak",
                "top_probability": 0.95, "all_diagnoses": [], "ds_metrics": {},
                "knowledge_base_version": "test",
            })
        self.assertTrue(result["persistent"])
        self.assertEqual(result["id"], 101)
        self.assertIn("insert into diagnosis_history", cursor.execute.call_args_list[0].args[0])
        self.assertIn("insert into case_verifications", cursor.execute.call_args_list[1].args[0])
        connection.commit.assert_called_once()

    def test_training_command_is_allowlisted(self):
        fake_repository = Mock()
        fake_repository.backend_name = "mysql"
        service = TrainingService(fake_repository, os.path.dirname(os.path.dirname(__file__)))
        output = os.path.join(service.root_dir, "reports", "training_jobs", "test")
        command = service._command("pso", {"particles": 5, "iterations": 5, "seeds": "42"}, output)
        self.assertIn("run_experiment.py", " ".join(command))
        with self.assertRaises(ValueError):
            service._command("shell", {}, output)

    def test_admin_requires_login(self):
        response = app.test_client().get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.headers["Location"])

    def test_admin_dashboard_renders_for_authenticated_session(self):
        repository = Mock()
        repository.backend_name = "mysql"
        repository.dashboard_stats.return_value = {
            "diagnoses": 1, "today": 1, "pending": 1, "verified": 0,
            "jobs": 0, "classes": [{"top_diagnosis": "LCD Rusak", "total": 1}],
        }
        repository.list_diagnoses.return_value = ([], 0)
        repository.list_prices.return_value = []
        repository.list_training_jobs.return_value = []
        repository.list_datasets.return_value = []
        repository.list_models.return_value = []
        repository.get_diagnosis.return_value = {
            "id": 1, "case_number": "NST-TEST", "customer_name": "Budi",
            "phone_type": "iPhone 12", "complaint": "layar gelap",
            "created_at": "2026-08-31", "model_version": "test",
            "extractor_provider": "qwen", "top_diagnosis": "LCD Rusak",
            "top_probability": 0.95, "verification_status": "pending",
            "actual_diagnosis": None, "expert_notes": None,
            "features_json": DEFAULT_FEATURES, "ds_metrics_json": {},
        }
        test_app = Flask(__name__)
        test_app.secret_key = "test-only-secret"
        test_app.register_blueprint(create_admin_blueprint(
            repository, engine, os.path.dirname(os.path.dirname(__file__)), lambda _path: None,
        ))
        client = test_app.test_client()
        with client.session_transaction() as browser_session:
            browser_session["admin_id"] = 1
            browser_session["admin_username"] = "admin"
            browser_session["admin_role"] = "admin"
        for path in ("/admin/", "/admin/diagnoses", "/admin/diagnoses/1",
                     "/admin/prices", "/admin/training", "/admin/models"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
        self.assertIn(b"Dashboard", client.get("/admin/").data)

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_ANON_KEY": "test-anon-key",
        "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
    }, clear=False)
    @patch("app.core.supabase_store.urllib.request.urlopen")
    def test_supabase_auth_uses_auth_user_and_active_profile(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = json.dumps({
            "user": {"id": "00000000-0000-0000-0000-000000000001", "email": "admin@example.com"},
            "expires_in": 3600,
        }).encode("utf-8")
        store = SupabaseStore()
        with patch.object(store, "_one", return_value={
            "id": "00000000-0000-0000-0000-000000000001",
            "username": "admin@example.com", "role": "admin", "is_active": True,
        }):
            profile = store.authenticate_admin("admin@example.com", "not-a-real-password")
        self.assertEqual(profile["role"], "admin")
        self.assertEqual(profile["email"], "admin@example.com")
        request = urlopen.call_args.args[0]
        self.assertIn("/auth/v1/token?grant_type=password", request.full_url)
        self.assertNotIn("password_hash", request.data.decode("utf-8"))

    @patch.dict(os.environ, {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
        "SUPABASE_STORE_DIAGNOSIS_DETAILS": "true",
    }, clear=False)
    def test_supabase_diagnosis_is_ready_for_expert_verification(self):
        store = SupabaseStore()
        with patch.object(store, "_request", return_value=[{
            "id": "00000000-0000-0000-0000-000000000002",
        }]) as request_call:
            result = store.save_diagnosis({
                "customer_name": "Budi", "phone_type": "iPhone 12",
                "complaint": "layar gelap", "features": DEFAULT_FEATURES,
                "features_confirmed": True, "top_diagnosis": "LCD Rusak",
                "all_diagnoses": [], "ds_metrics": {},
            })
        payload = request_call.call_args.args[1]
        self.assertTrue(result["persistent"])
        self.assertTrue(payload["case_number"].startswith("NST-"))
        self.assertEqual(payload["complaint"], "layar gelap")
        self.assertTrue(payload["features_confirmed"])

    def test_supabase_expert_cannot_open_admin_only_price_page(self):
        repository = Mock()
        repository.backend_name = "supabase"
        repository.configured = True
        repository.ping.return_value = True
        repository.authenticate_admin.return_value = {
            "id": "00000000-0000-0000-0000-000000000003",
            "username": "expert@example.com", "role": "expert", "is_active": True,
            "expires_in": 3600,
        }
        repository.dashboard_stats.return_value = {
            "diagnoses": 0, "today": 0, "pending": 0, "verified": 0,
            "jobs": 0, "classes": [],
        }
        test_app = Flask(__name__)
        test_app.secret_key = "test-only-secret"
        test_app.register_blueprint(create_admin_blueprint(
            repository, engine, os.path.dirname(os.path.dirname(__file__)), lambda _path: None,
        ))
        client = test_app.test_client()
        client.get("/admin/login")
        with client.session_transaction() as browser_session:
            token = browser_session["csrf_token"]
        response = client.post("/admin/login", data={
            "csrf_token": token, "username": "expert@example.com", "password": "test-password",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get("/admin/").status_code, 200)
        self.assertEqual(client.get("/admin/prices").status_code, 403)

    def test_supabase_admin_migration_has_auth_roles_and_rls(self):
        migration_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "supabase", "migrations", "002_admin_panel.sql",
        )
        with open(migration_path, "r", encoding="utf-8") as migration_file:
            migration = migration_file.read().lower()
        self.assertIn("references auth.users", migration)
        self.assertIn("role in ('admin', 'expert')", migration)
        self.assertIn("enable row level security", migration)
        self.assertNotIn("password_hash", migration)


if __name__ == "__main__":
    unittest.main()
