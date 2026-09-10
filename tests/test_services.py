import json
import os
import unittest

from app.core.ds_engine import DSEngine
from app.core.feature_schema import DEFAULT_FEATURES, OPTIMAL_KNOWLEDGE_BASE
from app.services.diagnosis_service import DiagnosisService, FeedbackService


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeStore:
    configured = True
    backend_name = "fake"

    def __init__(self):
        self.saved_diagnoses = []
        self.saved_feedback = []

    def save_diagnosis(self, record):
        self.saved_diagnoses.append(record)
        return {"success": True, "persistent": True}

    def save_feedback(self, entry):
        self.saved_feedback.append(entry)
        return {"success": True, "persistent": True}

    def resolve_price(self, _damage, _phone):
        return None


def load_expert_db():
    with open(os.path.join(ROOT_DIR, "data", "expertDiagnosesAndFix.json"), encoding="utf-8") as source:
        return json.load(source)


class DiagnosisServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.engine = DSEngine(OPTIMAL_KNOWLEDGE_BASE)
        self.service = DiagnosisService(
            self.engine,
            self.store,
            load_expert_db(),
            OPTIMAL_KNOWLEDGE_BASE,
            ("software",),
        )

    def test_hardware_diagnosis_runs_without_flask_context(self):
        features = DEFAULT_FEATURES.copy()
        features["LCD"] = "Gelap"

        def extractor(_complaint, _phone):
            return features, "Periksa layar.", None, {"provider": "test"}

        result = self.service.diagnose("Budi", "iPhone 12", "layar gelap", None, extractor)
        self.assertTrue(result["success"])
        self.assertEqual(result["extraction"]["provider"], "test")
        self.assertEqual(len(self.store.saved_diagnoses), 1)

    def test_software_diagnosis_skips_extractor(self):
        def extractor(*_args):
            raise AssertionError("extractor tidak boleh dipanggil")

        result = self.service.diagnose("Budi", "iPhone 12", "masalah software", None, extractor)
        self.assertTrue(result["is_software"])
        self.assertEqual(result["top_probability"], 1.0)

    def test_confirmed_invalid_features_are_rejected(self):
        result = self.service.diagnose(
            "Budi", "iPhone 12", "keluhan", {"LCD": "invalid"}, lambda *_args: None
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["status_code"], 400)

    def test_low_confidence_policy_requires_technician_review(self):
        result = {
            "used_fallback": False,
            "top_probability": 0.45,
            "uncertainty_theta": 0.25,
            "all_diagnoses": [
                {"damage": "LCD Rusak", "probability": 0.45},
                {"damage": "Baterai Rusak", "probability": 0.40},
            ],
            "dev_log": {"conflict_steps": [{"conflict_k": 0.1}]},
        }
        policy = self.service.confidence_policy(result)
        self.assertEqual(policy["status"], "technician_review")
        self.assertTrue(policy["requires_technician"])

    def test_multiple_confirmed_components_are_returned_as_clinical_topics(self):
        features = DEFAULT_FEATURES.copy()
        features["Baterai"] = "Daya Tahan Rendah/Gembung"
        features["Kamera"] = "Blank"

        result = self.service.diagnose(
            "Budi", "iPhone 12", "baterai dan kamera bermasalah", features, lambda *_args: None
        )
        topics = [item["damage"] for item in result["clinical_knowledge"]]
        self.assertEqual(topics, ["Baterai Rusak", "Kamera Rusak"])


class FeedbackServiceTests(unittest.TestCase):
    def test_feedback_is_persisted_without_flask_context(self):
        store = FakeStore()
        service = FeedbackService(store, DSEngine(OPTIMAL_KNOWLEDGE_BASE))
        damage = next(iter(service.engine.all_damages))
        result = service.submit("Budi", "iPhone 12", "keluhan", damage)
        self.assertTrue(result["success"])
        self.assertTrue(result["persistent"])
        self.assertEqual(len(store.saved_feedback), 1)


if __name__ == "__main__":
    unittest.main()
