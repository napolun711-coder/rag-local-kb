from __future__ import annotations

import unittest

from app.adapters.lightrag.errors import KnowledgeHTTPError
from app.features.chat.service import _query_error_event


class ChatErrorTests(unittest.TestCase):
    def test_no_context_becomes_actionable_no_context_event(self) -> None:
        event = _query_error_event(KnowledgeHTTPError(502, "HTTP 422: No relevant context found"))
        self.assertTrue(event.get("no_context"))
        self.assertIn("没有找到", event["message"])

    def test_insufficient_balance_is_named(self) -> None:
        event = _query_error_event(KnowledgeHTTPError(502, "HTTP 402: Insufficient Balance"))
        self.assertNotIn("no_context", event)
        self.assertIn("余额", event["message"])

    def test_generic_engine_failure_keeps_guidance(self) -> None:
        event = _query_error_event(KnowledgeHTTPError(502, "HTTP 500: internal error"))
        self.assertIn("知识服务检索失败", event["message"])
        self.assertIn("继续对话", event["message"])


if __name__ == "__main__":
    unittest.main()
