from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.features.chat import api as chat_api
from app.features.chat import sessions
from app.features.chat.contracts import HistorySearchResponse
from app.features.memory import runtime
from app.features.memory.db import MemoryDatabase
from app.features.memory.store import MemoryStore


class ChatHistorySearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = MemoryStore(MemoryDatabase(Path(self.temp_dir.name) / "test.db"))
        self.store.initialize()
        runtime.reset_for_tests(self.store)

    def tearDown(self) -> None:
        runtime.reset_for_tests()
        self.temp_dir.cleanup()

    def _session_with_message(
        self,
        *,
        title: str,
        content: str,
        topic_id: str | None = None,
    ) -> str:
        session = self.store.create_session(title=title, topic_id=topic_id)
        self.store.add_user_message(session["id"], content)
        return str(session["id"])

    def test_search_returns_contract_fields_and_topic_filter(self) -> None:
        topic = self.store.create_topic(name="主题甲")
        wanted = self._session_with_message(
            title="项目说明",
            content="项目说明文档已经整理",
            topic_id=topic["id"],
        )
        self._session_with_message(
            title="其他事项",
            content="另一份项目说明",
        )

        result = sessions.search_history(
            "项目说明",
            topic_id=str(topic["id"]),
            limit=10,
        )
        payload = HistorySearchResponse.model_validate(result)

        self.assertTrue(payload.enabled)
        self.assertEqual(payload.count, 1)
        self.assertEqual(payload.items[0].session_id, wanted)
        self.assertEqual(payload.items[0].topic_name, "主题甲")
        self.assertEqual(payload.items[0].role, "user")
        self.assertIn("项目说明", payload.items[0].snippet)

    def test_search_degrades_when_persistence_is_disabled(self) -> None:
        runtime.reset_for_tests(enabled=False)

        self.assertEqual(
            sessions.search_history("项目说明"),
            {"enabled": False, "count": 0, "items": []},
        )

    def test_history_route_is_registered(self) -> None:
        routes = {
            (route.path, tuple(sorted(route.methods or [])))
            for route in chat_api.router.routes
        }
        self.assertIn(("/api/history/search", ("GET",)), routes)


if __name__ == "__main__":
    unittest.main()
