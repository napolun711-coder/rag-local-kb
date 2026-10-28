from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.features.knowledge import index_guard


def _service(model: str = "bge-m3:latest", dim: str = "1024") -> dict:
    return {
        "engine_identity": {
            "working_directory": "C:/ragkb-test/index",
            "embedding_binding": "ollama",
            "embedding_model": model,
            "embedding_dim": dim,
            "embedding_binding_host": "http://127.0.0.1:11434",
            "vector_storage": "NanoVectorDBStorage",
        }
    }


class IndexCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manifest = Path(self.temp_dir.name) / "index-compatibility.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_baseline_then_model_change_requires_rebuild(self) -> None:
        first = index_guard.status_from_service(
            _service(),
            path=self.manifest,
            create_baseline=True,
        )
        self.assertEqual(first["state"], "compatible")

        changed = index_guard.status_from_service(
            _service(model="other-embedding"),
            path=self.manifest,
        )
        self.assertEqual(changed["state"], "mismatch")
        self.assertTrue(changed["blocking"])

    def test_accept_requires_exact_confirmation_and_updates_baseline(self) -> None:
        index_guard.status_from_service(
            _service(),
            path=self.manifest,
            create_baseline=True,
        )
        with self.assertRaises(ValueError):
            index_guard.accept_current(_service(dim="768"), "yes", path=self.manifest)

        accepted = index_guard.accept_current(
            _service(dim="768"),
            index_guard.CONFIRMATION,
            path=self.manifest,
        )
        self.assertEqual(accepted["state"], "compatible")

    def test_corrupt_manifest_blocks_instead_of_overwriting(self) -> None:
        self.manifest.write_text("{not-json", encoding="utf-8")
        status = index_guard.status_from_service(_service(), path=self.manifest)
        self.assertEqual(status["state"], "unreadable")
        self.assertTrue(status["blocking"])
        self.assertEqual(self.manifest.read_text(encoding="utf-8"), "{not-json")

    def test_manifest_does_not_contain_secret_values(self) -> None:
        index_guard.status_from_service(
            _service(),
            path=self.manifest,
            create_baseline=True,
        )
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        rendered = json.dumps(payload)
        self.assertNotIn("api_key", rendered.lower())
        self.assertNotIn("token", rendered.lower())
        self.assertNotIn("ragkb-test", rendered.lower())


if __name__ == "__main__":
    unittest.main()
