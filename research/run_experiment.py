"""Eksperimen reproducible DS baseline vs DS-PSO pada test set yang sama."""

import argparse
import copy
import glob
import hashlib
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
)

LEAKAGE_TERMS = {
    "LCD Rusak": ("lcd", "display", "screen"),
    "Baterai Rusak": ("baterai", "battery"),
    "Speaker Rusak": ("speaker", "buzzer", "earpiece"),
    "IC Power Rusak": ("ic power", "vcc main", "cpu"),
    "IC Cas Rusak": ("ic cas", "ic charger", "hydra"),
    "IC WTR Rusak": ("wtr", "baseband", "ic rf"),
    "Kamera Rusak": ("kamera", "camera"),
    "Mikrofon Rusak": ("mikrofon", "microphone"),
    "Port Pengisian Rusak": ("konektor cas", "port cas"),
    "IC Audio Rusak": ("ic audio",),
    "Backdoor Rusak": ("backdoor", "kaca belakang"),
    "Housing Rusak": ("housing",),
    "Tombol Rusak": ("tombol", "button"),
}


def latest(pattern):
    files = glob.glob(pattern)
    if not files:
        raise FileNotFoundError(f"Tidak ada file yang cocok dengan {pattern}")
    return max(files, key=os.path.getmtime)


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def leakage_audit(dataframe):
    """Indikator konservatif; temuan tetap perlu diverifikasi manual oleh pakar."""
    if "Raw_Text" not in dataframe.columns:
        return {"available": False, "reason": "Kolom Raw_Text tidak tersedia."}
    flagged = []
    for index, row in dataframe.iterrows():
        text = str(row["Raw_Text"]).lower()
        label = row["Kerusakan"]
        matches = [term for term in LEAKAGE_TERMS.get(label, ()) if term in text]
        if matches:
            flagged.append({"row": int(index), "label": label, "terms": matches})
    return {
        "available": True,
        "flagged_rows": len(flagged),
        "total_rows": len(dataframe),
        "flagged_ratio": len(flagged) / len(dataframe) if len(dataframe) else 0.0,
        "sample": flagged[:50],
        "warning": "Kemunculan istilah komponen tidak selalu leakage; verifikasi terhadap catatan gejala asli wajib dilakukan.",
    }


def constrain(vector, rules):
    result = np.clip(np.asarray(vector, dtype=float), 0.0, 0.95)
    index = 0
    for rule in rules:
        size = len(rule["hypotheses"])
        values = result[index:index + size]
        total = values.sum()
        if total > 0.95:
            result[index:index + size] = values / total * 0.95
        index += size
    return result


class ExperimentEngine:
    def __init__(self, rules, classes, priors):
        self.rules = rules
        self.classes = classes
        self.theta = frozenset(classes)
        total = sum(priors.values())
        self.priors = {key: value / total for key, value in priors.items()}

    def active_rules(self, dataframe):
        result = []
        for _, row in dataframe.iterrows():
            active = []
            offset = 0
            for rule in self.rules:
                size = len(rule["hypotheses"])
                if row.get(rule["symptom_col"]) == rule["symptom_val"]:
                    active.append((offset, rule))
                offset += size
            result.append(active)
        return result

    def combine(self, first, second):
        combined, conflict = {}, 0.0
        for left, left_mass in first.items():
            for right, right_mass in second.items():
                intersection = left.intersection(right)
                if intersection:
                    combined[intersection] = combined.get(intersection, 0.0) + left_mass * right_mass
                else:
                    conflict += left_mass * right_mass
        if conflict >= 1.0 - 1e-12:
            return {self.theta: 1.0}
        return {key: mass / (1.0 - conflict) for key, mass in combined.items()}

    def probabilities(self, active, beliefs):
        if not active:
            return np.array([self.priors[name] for name in self.classes])
        masses = []
        for offset, rule in active:
            mass, used = {}, 0.0
            for index, hypothesis in enumerate(rule["hypotheses"]):
                value = float(beliefs[offset + index])
                mass[frozenset([hypothesis["damage"]])] = value
                used += value
            mass[self.theta] = max(0.0, 1.0 - used)
            masses.append(mass)
        combined = masses[0]
        for mass in masses[1:]:
            combined = self.combine(combined, mass)
        probabilities = {name: 0.0 for name in self.classes}
        theta_share = combined.get(self.theta, 0.0) / len(self.classes)
        for subset, mass in combined.items():
            if subset == self.theta:
                continue
            valid = [name for name in subset if name in probabilities]
            if valid:
                for name in valid:
                    probabilities[name] += mass / len(valid)
        for name in probabilities:
            probabilities[name] += theta_share
        vector = np.array([probabilities[name] for name in self.classes])
        return vector / vector.sum()

    def matrix(self, active_rows, beliefs):
        # Banyak kasus memiliki kombinasi gejala identik. Cache per kombinasi
        # mempercepat fitness PSO tanpa mengubah hasil matematis.
        cache = {}
        rows = []
        for active in active_rows:
            signature = tuple(offset for offset, _ in active)
            if signature not in cache:
                cache[signature] = self.probabilities(active, beliefs)
            rows.append(cache[signature])
        return np.vstack(rows)


def metrics(targets, probabilities, classes):
    indexes = probabilities.argmax(axis=1)
    predictions = np.array([classes[index] for index in indexes])
    one_hot = np.zeros_like(probabilities)
    for row, target in enumerate(targets):
        one_hot[row, classes.index(target)] = 1.0
    report = classification_report(targets, predictions, labels=classes, output_dict=True, zero_division=0)
    return {
        "accuracy": float(accuracy_score(targets, predictions)),
        "macro_precision": float(report["macro avg"]["precision"]),
        "macro_recall": float(report["macro avg"]["recall"]),
        "macro_f1": float(report["macro avg"]["f1-score"]),
        "weighted_precision": float(report["weighted avg"]["precision"]),
        "weighted_recall": float(report["weighted avg"]["recall"]),
        "weighted_f1": float(report["weighted avg"]["f1-score"]),
        "multiclass_brier": float(np.mean(np.sum((probabilities - one_hot) ** 2, axis=1))),
        "classification_report": report,
        "confusion_matrix": confusion_matrix(targets, predictions, labels=classes).tolist(),
    }


def optimize(engine, active_train, targets, rules, initial, seed, particles, iterations):
    rng = np.random.default_rng(seed)
    positions = np.vstack([initial] + [constrain(initial + rng.uniform(-0.2, 0.2, len(initial)), rules) for _ in range(particles - 1)])
    velocities = rng.uniform(-0.1, 0.1, positions.shape)
    target_one_hot = np.zeros((len(targets), len(engine.classes)))
    for row, target in enumerate(targets):
        target_one_hot[row, engine.classes.index(target)] = 1.0

    def fitness(position):
        probs = engine.matrix(active_train, position)
        return 1.0 - float(np.mean(np.sum((probs - target_one_hot) ** 2, axis=1)))

    scores = np.array([fitness(position) for position in positions])
    personal, personal_scores = positions.copy(), scores.copy()
    best_index = scores.argmax()
    global_best, global_score = positions[best_index].copy(), float(scores[best_index])
    history = [global_score]
    for iteration in range(iterations):
        inertia = 0.9 - (0.5 * iteration / iterations)
        for particle in range(particles):
            r1, r2 = rng.random(len(initial)), rng.random(len(initial))
            velocities[particle] = np.clip(
                inertia * velocities[particle]
                + 2.0 * r1 * (personal[particle] - positions[particle])
                + 2.0 * r2 * (global_best - positions[particle]),
                -0.2,
                0.2,
            )
            positions[particle] = constrain(positions[particle] + velocities[particle], rules)
            score = fitness(positions[particle])
            if score > personal_scores[particle]:
                personal[particle], personal_scores[particle] = positions[particle].copy(), score
                if score > global_score:
                    global_best, global_score = positions[particle].copy(), float(score)
        history.append(global_score)
    return global_best, global_score, history


def export_rules(rules, beliefs):
    exported, offset = copy.deepcopy(rules), 0
    for rule in exported:
        total = 0.0
        for hypothesis in rule["hypotheses"]:
            hypothesis["optimal_belief"] = round(float(beliefs[offset]), 6)
            hypothesis.pop("initial_belief", None)
            total += float(beliefs[offset])
            offset += 1
        rule["uncertainty_theta"] = round(1.0 - total, 6)
    return exported


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", default=None)
    parser.add_argument("--test", default=None)
    parser.add_argument("--belief", default="data/belief.json")
    parser.add_argument("--seeds", default="42,43,44,45,46,47,48,49,50,51")
    parser.add_argument("--particles", type=int, default=30)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--output-dir", default="reports")
    args = parser.parse_args()
    train_path = args.train or latest("data/dataLatih/dataLatih-*.csv")
    test_path = args.test or latest("data/dataUji/dataUji-*.csv")
    train, test = pd.read_csv(train_path), pd.read_csv(test_path)
    with open(args.belief, "r", encoding="utf-8") as source:
        rules = json.load(source)["rules"]
    classes = sorted(train["Kerusakan"].unique().tolist())
    unknown_test = sorted(set(test["Kerusakan"]) - set(classes))
    if unknown_test:
        raise ValueError(f"Test set memiliki kelas yang tidak ada pada train set: {unknown_test}")
    counts = train["Kerusakan"].value_counts()
    priors = {name: float(counts.get(name, 0) / len(train)) for name in classes}
    engine = ExperimentEngine(rules, classes, priors)
    train_active, test_active = engine.active_rules(train), engine.active_rules(test)
    initial = constrain([h["initial_belief"] for r in rules for h in r["hypotheses"]], rules)
    baseline = metrics(test["Kerusakan"].values, engine.matrix(test_active, initial), classes)
    runs = []
    for seed in [int(value) for value in args.seeds.split(",") if value.strip()]:
        beliefs, fitness, history = optimize(engine, train_active, train["Kerusakan"].values, rules, initial, seed, args.particles, args.iterations)
        result = metrics(test["Kerusakan"].values, engine.matrix(test_active, beliefs), classes)
        runs.append({"seed": seed, "train_fitness": fitness, "metrics": result, "beliefs": beliefs.tolist(), "history": history})
        print(f"Seed {seed}: accuracy={result['accuracy']:.4f}, macro_f1={result['macro_f1']:.4f}, brier={result['multiclass_brier']:.4f}", flush=True)
    best = max(runs, key=lambda run: (run["metrics"]["macro_f1"], run["metrics"]["accuracy"]))
    summary = {}
    for name in ("accuracy", "macro_f1", "weighted_f1", "multiclass_brier"):
        values = np.array([run["metrics"][name] for run in runs])
        summary[name] = {"mean": float(values.mean()), "std": float(values.std(ddof=1)) if len(values) > 1 else 0.0}
    generated_at = datetime.now(timezone.utc).isoformat()
    report = {
        "metadata": {"generated_at": generated_at, "train": train_path, "test": test_path, "train_sha256": file_hash(train_path), "test_sha256": file_hash(test_path), "particles": args.particles, "iterations": args.iterations, "seeds": [run["seed"] for run in runs]},
        "classes": classes,
        "leakage_audit": {"train": leakage_audit(train), "test": leakage_audit(test)},
        "baseline_ds": baseline,
        "ds_pso_summary": summary,
        "runs": [
            {
                "seed": run["seed"],
                "train_fitness": run["train_fitness"],
                "metrics": {
                    name: run["metrics"][name]
                    for name in ("accuracy", "macro_f1", "weighted_f1", "multiclass_brier")
                },
            }
            for run in runs
        ],
        "best_run": best,
    }
    candidate = {
        "metadata": {**report["metadata"], "fitness": "1 - multiclass Brier score", "baseline_test_metrics": baseline, "optimized_test_metrics": best["metrics"]},
        "total_cases": len(train),
        "class_counts": {name: int(counts.get(name, 0)) for name in classes},
        "class_priors": priors,
        "rules": export_rules(rules, best["beliefs"]),
    }
    os.makedirs(args.output_dir, exist_ok=True)
    with open(os.path.join(args.output_dir, "experiment_report.json"), "w", encoding="utf-8") as target:
        json.dump(report, target, indent=2, ensure_ascii=False)
    with open(os.path.join(args.output_dir, "optimal_knowledge_base_candidate.json"), "w", encoding="utf-8") as target:
        json.dump(candidate, target, indent=2, ensure_ascii=False)
    print(json.dumps({"baseline": baseline["accuracy"], "optimized_mean": summary["accuracy"], "best_seed": best["seed"], "best_accuracy": best["metrics"]["accuracy"]}, indent=2))


if __name__ == "__main__":
    main()
