from __future__ import annotations

import unittest

from app.features.settings import service


class RerankSettingsTests(unittest.TestCase):
    def test_ui_modes_map_to_lightrag_bindings(self) -> None:
        self.assertEqual(service._rerank_binding_for("off", "jina"), "null")
        self.assertEqual(service._rerank_binding_for("local", "jina"), "cohere")
        self.assertEqual(service._rerank_binding_for("cloud", "jina"), "jina")
        self.assertEqual(service._rerank_binding_for("cloud", "aliyun"), "aliyun")

    def test_local_rerank_accepts_loopback_and_private_lan(self) -> None:
        self.assertTrue(service._private_or_local_url("http://127.0.0.1:9000/v2/rerank"))
        self.assertTrue(service._private_or_local_url("http://localhost:9000/rerank"))
        self.assertTrue(service._private_or_local_url("http://192.168.3.4:9000/rerank"))
        self.assertTrue(service._private_or_local_url("http://model.local/rerank"))

    def test_local_rerank_rejects_public_endpoint(self) -> None:
        self.assertFalse(service._private_or_local_url("https://api.cohere.com/v2/rerank"))
        self.assertFalse(service._private_or_local_url("http://8.8.8.8/rerank"))
        self.assertFalse(service._private_or_local_url("ftp://127.0.0.1/rerank"))


if __name__ == "__main__":
    unittest.main()
