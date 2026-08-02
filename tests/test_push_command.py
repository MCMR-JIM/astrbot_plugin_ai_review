from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


_REPO_ROOT = Path(__file__).resolve().parent.parent
_PACKAGE_NAME = "_plugin_under_test"
if _PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(_PACKAGE_NAME)
    package.__path__ = [str(_REPO_ROOT)]
    package.__package__ = _PACKAGE_NAME
    sys.modules[_PACKAGE_NAME] = package

try:
    from _plugin_under_test.commands.review import ReviewCommandMixin
except ModuleNotFoundError as exc:
    if not (exc.name == "astrbot" or exc.name.startswith("astrbot.")):
        raise
    ReviewCommandMixin = None


class _RecordingConfig:
    def __init__(self) -> None:
        self.saved: list[tuple[object, str, str]] = []

    async def set_override(self, store, group_id, key, value):
        self.saved.append((group_id, key, value))
        return True, "saved"

    def effective(self, group_id):
        return {"regex_push_target": "group"}

    def get(self, key, default=None):
        return default


@unittest.skipIf(ReviewCommandMixin is None, "AstrBot is not installed")
class PushCommandTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = _RecordingConfig()
        self.command = object.__new__(ReviewCommandMixin)
        self.command.config = self.config
        self.command._kv = object()
        # #16 合入后 _handle_push 校验接收者必须是 AstrBot 管理员；
        # 测试的接收者均视为管理员，聚焦命令解析行为
        self.command.context = SimpleNamespace(
            get_config=lambda umo: {"admins_id": ["10001", "10002", "10003", "10004"]}
        )

    def test_prefix_stripped_admin_command_preserves_qq_list(self) -> None:
        event = SimpleNamespace(
            message_str="review push admin 10001,10002",
            get_group_id=lambda: "g1",
            get_platform_id=lambda: "aiocqhttp",
        )

        asyncio.run(self.command._handle_push(event, "admin"))

        self.assertEqual(
            self.config.saved[0],
            ("g1", "regex_push_admin", "10001,10002"),
        )

    def test_slash_prefixed_admin_command_preserves_qq_list(self) -> None:
        event = SimpleNamespace(
            message_str="/review push admin 10003,10004",
            get_group_id=lambda: "g1",
            get_platform_id=lambda: "aiocqhttp",
        )

        asyncio.run(self.command._handle_push(event, "admin"))

        self.assertEqual(
            self.config.saved[0],
            ("g1", "regex_push_admin", "10003,10004"),
        )

    def test_group_and_off_fall_back_to_parsed_subcommand(self) -> None:
        for mode in ("group", "off"):
            with self.subTest(mode=mode):
                self.config.saved.clear()
                event = SimpleNamespace(
                    message_str="unrelated raw text",
                    get_group_id=lambda: "g1",
                )

                asyncio.run(self.command._handle_push(event, mode))

                self.assertEqual(
                    self.config.saved,
                    [("g1", "regex_push_target", mode)],
                )

    def test_view_falls_back_to_parsed_subcommand(self) -> None:
        event = SimpleNamespace(
            message_str="unrelated raw text",
            get_group_id=lambda: "g1",
        )

        result = asyncio.run(self.command._handle_push(event, "view"))

        self.assertIn("本群推送配置", result)
        self.assertEqual(self.config.saved, [])


if __name__ == "__main__":
    unittest.main()
