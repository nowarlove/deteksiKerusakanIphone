import json
import os
import tempfile
import unittest

from app.core.model_manifest import build_model_manifest, write_model_manifest


class ModelManifestTests(unittest.TestCase):
    def test_manifest_contains_artifact_checksum_and_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact = os.path.join(temp_dir, "model.json")
            with open(artifact, "w", encoding="utf-8") as target:
                target.write('{"rules": []}')
            manifest = build_model_manifest(
                artifact,
                {"train_sha256": "train-hash", "particles": 30, "iterations": 100},
            )
            self.assertEqual(manifest["manifest_version"], "model-manifest-v1")
            self.assertEqual(len(manifest["artifact"]["sha256"]), 64)
            self.assertEqual(manifest["parameters"]["particles"], 30)

    def test_manifest_is_written_next_to_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact = os.path.join(temp_dir, "model.json")
            with open(artifact, "w", encoding="utf-8") as target:
                target.write('{}')
            path, _ = write_model_manifest(artifact)
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as manifest_file:
                manifest = json.load(manifest_file)
            self.assertEqual(manifest["manifest_version"], "model-manifest-v1")


if __name__ == "__main__":
    unittest.main()
