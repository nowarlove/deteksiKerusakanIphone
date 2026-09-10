"""Smoke test provider NLP aktif tanpa mencetak credential."""

import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.core.llm_extractor import extract_features_llm


def main():
    features, advice, error, metadata = extract_features_llm(
        "baterai cepat habis dan layar gelap",
        "iPhone 12",
    )
    print(json.dumps({
        "error": error,
        "provider": metadata.get("provider"),
        "model": metadata.get("model"),
        "fallback": metadata.get("fallback"),
        "attempts": metadata.get("attempts", []),
        "features": features,
        "advice_present": bool(advice),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
