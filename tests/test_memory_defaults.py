from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.features.chat.service import ChatService
from app.features.memory import runtime
from app.features.memory.config import MemorySettings
from app.features.memory.context import build_chat_context
from app.features.memory.db import MemoryDatabase
from app.features.memory.store import MemoryStore
from app.features.settings.schema import group_defaults


class BalancedMemoryDefaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = MemoryStore(MemoryDatabase(Path(self.temp_dir.name) / "test.db"))
        self.store.initialize()
        runtime.reset_for_tests(self.store)

    def tearDown(self) -> None:
        runtime.reset_for_tests()
        self.temp_dir.cleanup()

    def test_balanced_defaults_are_enabled(self) -> None:
        cfg = MemorySettings()
        self.assertTrue(cfg.memory_enabled)
        self.assertTrue(cfg.memory_context_enabled)
        self.assertTrue(cfg.memory_fast_memory)
        self.assertTrue(cfg.memory_recall_enabled)
        self.assertTrue(cfg.memory_auto_extract)
        self.assertTrue(cfg.memory_worker)
        self.assertEqual(cfg.memory_context_budget, 1200)
        self.assertEqual(cfg.memory_recent_history_messages, 12)
        self.assertEqual(cfg.memory_recent_history_tokens, 2000)
        self.assertEqual(cfg.memory_recall_budget, 600)
        self.assertEqual(cfg.memory_recall_max_items, 6)
        self.assertEqual(cfg.memory_upstream_history, "on")
        self.assertEqual(cfg.memory_upstream_memory, "on")

    def test_settings_schema_matches_balanced_defaults(self) -> None:
        defaults = group_defaults()
        self.assertEqual(defaults[("memory", "enabled")], "true")
        self.assertEqual(defaults[("memory", "context_enabled")], "true")
        self.assertEqual(defaults[("memory", "fast_memory")], "true")
        self.assertEqual(defaults[("memory", "recall_enabled")], "true")
        self.assertEqual(defaults[("memory", "auto_extract")], "true")
        self.assertEqual(defaults[("memory", "worker")], "true")
        self.assertEqual(defaults[("memory", "context_budget")], "1200")
        self.assertEqual(defaults[("memory", "recent_history_messages")], "12")
        self.assertEqual(defaults[("memory", "recent_history_tokens")], "2000")
        self.assertEqual(defaults[("memory", "recall_budget")], "600")
        self.assertEqual(defaults[("memory", "recall_max_items")], "6")
        self.assertEqual(defaults[("memory", "upstream_history")], "on")
        self.assertEqual(defaults[("memory", "upstream_memory")], "on")

    def test_context_injects_history_and_long_term_memory(self) -> None:
        session = self.store.create_session(title="本地项目")
        user = self.store.add_user_message(
            session["id"],
            "我偏好简洁回答，也请记住我的项目叫 Atlas。",
        )
        self.store.add_assistant_message(
            session["id"],
            content="好的，我会保持简洁。",
            status="complete",
            reply_to_id=user["id"],
        )
        self.store.memories.create(
            content="用户偏好简洁回答",
            subject="回答风格",
            kind="preference",
            scope="global",
            source_type="user_confirmed",
            confidence=0.95,
            status="active",
        )
        context = build_chat_context(
            self.store,
            session["id"],
            "我的项目叫什么？",
            settings=MemorySettings(),
        )
        self.assertIsNotNone(context)
        self.assertTrue(context.history)
        self.assertTrue(context.memory_items)
        self.assertGreater(context.trace["injected_tokens"], 0)
        self.assertLessEqual(
            context.trace["injected_tokens"],
            context.trace["combined_budget"],
        )
        self.assertIn("Atlas", " ".join(item["content"] for item in context.history) + context.memory_prompt)

    def test_direct_memory_answer_handles_personal_facts_only(self) -> None:
        from app.features.chat.service import _direct_memory_answer

        session = self.store.create_session(title="Atlas")
        memory = self.store.memories.create(
            content="我的项目代号是 ORBIT-LANTERN-7421",
            subject="项目代号",
            kind="fact",
            scope="global",
            source_type="user_explicit",
            confidence=1.0,
            status="active",
        )
        context = build_chat_context(
            self.store,
            session["id"],
            "我的项目代号是什么？",
            settings=MemorySettings(),
        )
        self.assertIsNotNone(context)
        result = _direct_memory_answer(context, "我的项目代号是什么？只回答代号。")
        self.assertIsNotNone(result)
        answer, ids = result
        self.assertIn("ORBIT-LANTERN-7421", answer)
        self.assertIn(memory["id"], ids)
        self.assertIsNone(
            _direct_memory_answer(context, "知识库里的项目代号是什么？")
        )

    def test_upstream_options_send_history_and_memory(self) -> None:
        session = self.store.create_session(title="本地项目")
        user = self.store.add_user_message(session["id"], "记住我喜欢简洁回答。")
        self.store.add_assistant_message(
            session["id"],
            content="记住了。",
            status="complete",
            reply_to_id=user["id"],
        )
        self.store.memories.create(
            content="用户喜欢简洁回答",
            subject="回答风格",
            kind="preference",
            scope="global",
            source_type="user_confirmed",
            confidence=0.95,
            status="active",
        )
        context = build_chat_context(
            self.store,
            session["id"],
            "继续",
            settings=MemorySettings(),
        )
        self.assertIsNotNone(context)
        with patch(
            "app.features.chat.service.get_memory_settings",
            return_value=MemorySettings(),
        ):
            options = ChatService(gateway=object())._upstream_options(context)
        self.assertTrue(options.get("conversation_history"))
        self.assertTrue(options.get("user_prompt"))
        self.assertIn("用户喜欢简洁回答", options["user_prompt"])


if __name__ == "__main__":
    unittest.main()
