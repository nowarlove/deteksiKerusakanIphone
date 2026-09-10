"""Validasi kontrak knowledge base sebelum model digunakan runtime."""

from .feature_schema import DEPLOYMENT_SCHEMA, FEATURE_NAMES, OPTIMAL_KNOWLEDGE_BASE
from .ds_engine import DSEngine


def _evidence_pairs(knowledge_base):
    return {
        (rule.get("symptom_col"), rule.get("symptom_val"))
        for rule in knowledge_base.get("rules", [])
    }


def validate_knowledge_base(candidate, expected=OPTIMAL_KNOWLEDGE_BASE):
    """Raise ValueError when a model is incompatible with runtime artifacts."""
    if not isinstance(candidate, dict):
        raise ValueError("Knowledge base harus berupa object JSON.")
    if not isinstance(candidate.get("metadata", {}), dict):
        raise ValueError("Metadata knowledge base harus berupa object.")
    if not isinstance(candidate.get("class_priors"), dict):
        raise ValueError("Knowledge base harus memiliki class_priors object.")
    if not isinstance(candidate.get("rules"), list) or not candidate["rules"]:
        raise ValueError("Knowledge base harus memiliki rules yang tidak kosong.")

    engine = DSEngine(candidate)
    expected_classes = set(expected.get("class_priors", {}))
    if set(engine.all_damages) != expected_classes:
        raise ValueError("Kelas model tidak kompatibel dengan deployment schema.")

    if _evidence_pairs(candidate) != _evidence_pairs(expected):
        raise ValueError("Pasangan evidence model tidak kompatibel dengan model produksi.")

    allowed_values = DEPLOYMENT_SCHEMA["allowed_values"]
    if {rule["symptom_col"] for rule in candidate["rules"]} != set(FEATURE_NAMES):
        raise ValueError("Kolom evidence model tidak lengkap atau tidak kompatibel.")
    for rule in candidate["rules"]:
        column = rule.get("symptom_col")
        value = rule.get("symptom_val")
        if value not in allowed_values.get(column, []):
            raise ValueError(f"Nilai evidence tidak dikenal: {column}={value}.")

    return engine
