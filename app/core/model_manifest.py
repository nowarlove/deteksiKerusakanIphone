"""Manifest dan checksum artifact untuk lifecycle model lokal."""

import hashlib
import json
import os
from datetime import datetime, timezone

from .feature_schema import DEPLOYMENT_SCHEMA


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_model_manifest(artifact_path, metadata=None, source_dataset=None):
    metadata = metadata or {}
    manifest = {
        "manifest_version": "model-manifest-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "artifact": {
            "path": os.path.abspath(artifact_path),
            "sha256": sha256_file(artifact_path),
            "size_bytes": os.path.getsize(artifact_path),
        },
        "schema_version": DEPLOYMENT_SCHEMA.get("version"),
        "feature_order": list(DEPLOYMENT_SCHEMA.get("feature_order", [])),
        "evidence_set": metadata.get("evidence_set"),
        "source_dataset": source_dataset or metadata.get("train"),
        "train_sha256": metadata.get("train_sha256"),
        "test_sha256": metadata.get("test_sha256"),
        "parameters": {
            key: metadata[key]
            for key in ("particles", "iterations", "seeds")
            if key in metadata
        },
        "metrics": {
            key: metadata[key]
            for key in ("baseline_test_metrics", "optimized_test_metrics")
            if key in metadata
        },
    }
    return manifest


def write_model_manifest(artifact_path, metadata=None, source_dataset=None):
    manifest = build_model_manifest(artifact_path, metadata, source_dataset)
    manifest_path = f"{artifact_path}.manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as target:
        json.dump(manifest, target, ensure_ascii=False, indent=2)
    return manifest_path, manifest
