import unittest

import pandas as pd

from app.core.ds_engine import DSEngine
from app.core.feature_schema import DEFAULT_FEATURES, OPTIMAL_KNOWLEDGE_BASE
from research.run_experiment import ExperimentEngine, constrain


class RuntimeResearchContractTests(unittest.TestCase):
    def test_runtime_and_research_pignistic_outputs_agree(self):
        knowledge_base = OPTIMAL_KNOWLEDGE_BASE
        runtime = DSEngine(knowledge_base)
        features = DEFAULT_FEATURES.copy()
        features["LCD"] = "Gelap"
        features["Konektor Cas"] = "Tidak Ngecas"

        runtime_result = runtime.run_inference(features)
        runtime_probabilities = {
            item["damage"]: item["probability"]
            for item in runtime_result["all_diagnoses"]
        }

        beliefs = constrain(
            [
                hypothesis["optimal_belief"]
                for rule in knowledge_base["rules"]
                for hypothesis in rule["hypotheses"]
            ],
            knowledge_base["rules"],
        )
        research = ExperimentEngine(
            knowledge_base["rules"], runtime.all_damages, runtime.priors
        )
        active = research.active_rules(pd.DataFrame([features]))[0]
        research_probabilities = dict(
            zip(research.classes, research.probabilities(active, beliefs).tolist())
        )

        for damage in research.classes:
            self.assertAlmostEqual(
                runtime_probabilities[damage],
                research_probabilities[damage],
                delta=0.0001,
                msg=f"Perbedaan probabilitas pada {damage}",
            )
        self.assertEqual(
            runtime_result["top_diagnosis"],
            max(research_probabilities, key=research_probabilities.get),
        )


if __name__ == "__main__":
    unittest.main()
