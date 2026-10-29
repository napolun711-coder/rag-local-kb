from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from app.api import system
from app.features.knowledge.contracts import KnowledgeHealth


class HealthSummaryTests(unittest.TestCase):
    def test_health_keeps_version_and_reports_optional_services(self) -> None:
        gateway = Mock()
        gateway.health.return_value = KnowledgeHealth(
            configured=True,
            reachable=True,
            engine_installed=True,
            state="ready",
            service={
                "engine_identity": {
                    "vector_storage": "NanoVectorDBStorage",
                }
            },
        )
        targets = {
            "embedding_binding": "openai",
            "embedding_host": "https://example.invalid/v1",
            "embedding_model": "embedding-model",
            "vector_storage": "local",
            "qdrant_url": "",
        }
        with patch.object(system, "LightRagHttpClient", return_value=gateway), patch.object(
            system.settings_service,
            "health_targets",
            return_value=targets,
        ):
            payload = system.health()
        self.assertEqual(payload["status"], "ok")
        self.assertIn("version", payload)
        self.assertTrue(payload["lightrag"]["ok"])
        self.assertFalse(payload["ollama"]["enabled"])
        self.assertEqual(payload["ollama"]["state"], "not_in_use")
        self.assertFalse(payload["qdrant"]["enabled"])
        self.assertEqual(payload["qdrant"]["state"], "not_in_use")

    def test_ollama_requires_configured_model(self) -> None:
        targets = {
            "embedding_binding": "ollama",
            "embedding_host": "http://127.0.0.1:11434",
            "embedding_model": "bge-m3:latest",
        }
        with patch.object(
            system,
            "_probe_json",
            return_value=(True, "", {"models": [{"name": "other:latest"}]}),
        ):
            result = system._ollama_health(targets)
        self.assertTrue(result["enabled"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "unavailable")

    def test_qdrant_is_only_probed_when_active(self) -> None:
        with patch.object(system, "_probe_json") as probe:
            inactive = system._qdrant_health(
                {"qdrant_url": "http://127.0.0.1:6333"},
                "local",
            )
        self.assertFalse(inactive["enabled"])
        probe.assert_not_called()

    def test_active_qdrant_uses_health_endpoint(self) -> None:
        with patch.object(system, "_probe_json", return_value=(True, "", {})) as probe:
            result = system._qdrant_health(
                {"qdrant_url": "http://127.0.0.1:6333"},
                "qdrant",
            )
        self.assertTrue(result["ok"])
        probe.assert_called_once_with("http://127.0.0.1:6333/healthz")


if __name__ == "__main__":
    unittest.main()
