"""Schema fitur tunggal yang diturunkan dari artefak penelitian DS-PSO."""

import csv
import glob
import json
import os


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
def _latest_dataset_path(directory, prefix):
    candidates = glob.glob(os.path.join(ROOT_DIR, "data", directory, f"{prefix}-*.csv"))
    return max(candidates, key=os.path.getmtime) if candidates else ""


TRAIN_PATH = _latest_dataset_path("dataLatih", "dataLatih")
TEST_PATH = _latest_dataset_path("dataUji", "dataUji")
OPTIMAL_KB_PATH = os.path.join(ROOT_DIR, "data", "optimal_knowledge_base.json")
BELIEF_PATH = os.path.join(ROOT_DIR, "data", "belief.json")
SCHEMA_PATH = os.path.join(ROOT_DIR, "data", "model_schema.json")
NON_FEATURE_COLUMNS = {"Tipe HP", "Raw_Text", "Kerusakan"}


def _read_rows(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def _load_json(path):
    with open(path, "r", encoding="utf-8") as json_file:
        return json.load(json_file)


TRAIN_ROWS = _read_rows(TRAIN_PATH) if os.path.exists(TRAIN_PATH) else []
TEST_ROWS = _read_rows(TEST_PATH) if os.path.exists(TEST_PATH) else []
OPTIMAL_KNOWLEDGE_BASE = _load_json(OPTIMAL_KB_PATH)
INITIAL_BELIEF = _load_json(BELIEF_PATH)
DEPLOYMENT_SCHEMA = _load_json(SCHEMA_PATH)
FEATURE_NAMES = tuple(DEPLOYMENT_SCHEMA["feature_order"])
FEATURE_OPTIONS = {name: tuple(values) for name, values in DEPLOYMENT_SCHEMA["allowed_values"].items()}

if TRAIN_ROWS and TEST_ROWS:
    dataset_names = tuple(column for column in TRAIN_ROWS[0] if column not in NON_FEATURE_COLUMNS)
    dataset_options = {
        name: tuple(sorted({row[name] for row in TRAIN_ROWS + TEST_ROWS}))
        for name in dataset_names
    }
    if dataset_names != FEATURE_NAMES or dataset_options != FEATURE_OPTIONS:
        raise ValueError("model_schema.json tidak sama dengan dataset train/test.")

_rule_values = {}
for rule in OPTIMAL_KNOWLEDGE_BASE["rules"]:
    _rule_values.setdefault(rule["symptom_col"], set()).add(rule["symptom_val"])

DEFAULT_FEATURES = {}
for name, values in FEATURE_OPTIONS.items():
    default_candidates = set(values) - _rule_values.get(name, set())
    if len(default_candidates) != 1:
        raise ValueError(f"Default fitur {name} tidak dapat diturunkan secara unik: {default_candidates}")
    DEFAULT_FEATURES[name] = default_candidates.pop()

if DEFAULT_FEATURES != DEPLOYMENT_SCHEMA["defaults"]:
    raise ValueError("Default model_schema.json tidak sama dengan aturan DS-PSO.")


def _validate_artifact_alignment():
    if set(_rule_values) != set(FEATURE_NAMES):
        raise ValueError("Kolom aturan DS-PSO tidak sama dengan kolom fitur dataset.")
    for name, values in _rule_values.items():
        if not values.issubset(FEATURE_OPTIONS[name]):
            raise ValueError(f"Nilai aturan {name} tidak tersedia pada dataset.")
    initial_pairs = {
        (rule["symptom_col"], rule["symptom_val"])
        for rule in INITIAL_BELIEF["rules"]
    }
    optimal_pairs = {
        (rule["symptom_col"], rule["symptom_val"])
        for rule in OPTIMAL_KNOWLEDGE_BASE["rules"]
    }
    if initial_pairs != optimal_pairs:
        raise ValueError("Pasangan evidence belief awal dan knowledge base optimal tidak sama.")


_validate_artifact_alignment()


def validate_features(candidate):
    """Validasi ketat output Qwen terhadap domain kategorikal dataset."""
    if not isinstance(candidate, dict):
        return DEFAULT_FEATURES.copy(), [{"feature": "*", "value": candidate}]
    validated = DEFAULT_FEATURES.copy()
    rejected = []
    for name, value in candidate.items():
        if name not in FEATURE_OPTIONS or value not in FEATURE_OPTIONS.get(name, ()):
            rejected.append({"feature": name, "value": value})
        else:
            validated[name] = value
    return validated, rejected


def dataset_prompt_context():
    """Schema agregat artefak penelitian tanpa membagikan baris data mentah."""
    return {
        "source": {
            "train": os.path.basename(TRAIN_PATH),
            "test": os.path.basename(TEST_PATH),
            "rules": os.path.basename(OPTIMAL_KB_PATH),
            "initial_belief": os.path.basename(BELIEF_PATH),
        },
        "feature_order": list(FEATURE_NAMES),
        "allowed_values": {name: list(values) for name, values in FEATURE_OPTIONS.items()},
        "defaults": DEFAULT_FEATURES,
        "ds_pso_evidence": [
            {"feature": rule["symptom_col"], "value": rule["symptom_val"]}
            for rule in OPTIMAL_KNOWLEDGE_BASE["rules"]
        ],
        "dataset_size": DEPLOYMENT_SCHEMA["dataset_size"],
    }
