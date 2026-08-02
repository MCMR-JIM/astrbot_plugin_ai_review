"""Authorization tests for private rule-candidate push recipients."""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_PKG = "_plugin_under_test"
if _PKG not in sys.modules:
    package = types.ModuleType(_PKG)
    package.__path__ = [str(_REPO_ROOT)]
    package.__package__ = _PKG
    sys.modules[_PKG] = package

try:
    from _plugin_under_test.main import AiReviewPlugin
except ModuleNotFoundError as exc:
    if exc.name != "astrbot":
        raise
    AiReviewPlugin = None


class _Context:
    def __init__(
        self,
        admin_ids: object,
        *,
        admins_by_umo: dict[str, object] | None = None,
        raises: bool = False,
    ) -> None:
        self.admin_ids = admin_ids
        self.admins_by_umo = admins_by_umo or {}
        self.raises = raises
        self.requests: list[str] = []

    def get_config(self, umo: str) -> dict[str, object]:
        self.requests.append(umo)
        if self.raises:
            raise RuntimeError("configuration unavailable")
        return {"admins_id": self.admins_by_umo.get(umo, self.admin_ids)}


class _Config:
    def __init__(
        self,
        *,
        group_admins: list[str] | None = None,
        global_admins: list[str] | None = None,
    ) -> None:
        self.group_admins = group_admins or []
        self.global_admins = global_admins or []
        self.saved: list[tuple[str, str, str]] = []

    def effective(self, _group_id: str) -> dict[str, object]:
        return {"regex_push_admin": self.group_admins}

    def get(self, key: str, default=None):
        if key == "admin_qq":
            return self.global_admins
        return default

    async def set_override(
        self, _store: object, group_id: str, key: str, raw_value: str
    ) -> tuple[bool, str]:
        self.saved.append((group_id, key, raw_value))
        return True, f"{key} = {raw_value}"


class _Event:
    message_str = ""
    unified_msg_origin = "platform:GroupMessage:30003"

    def get_group_id(self) -> str:
        return "30003"

    def get_platform_id(self) -> str:
        return "platform"


class _Rules:
    def __init__(self, group_ids: tuple[str, ...] = ("30003",)) -> None:
        self._candidates = [
            types.SimpleNamespace(group_id=group_id, platform_id="platform")
            for group_id in group_ids
        ]

    def candidates(self) -> list[object]:
        return self._candidates


@unittest.skipIf(AiReviewPlugin is None, "real AstrBot is not installed")
class PushRecipientAuthorizationTest(unittest.TestCase):
    def _command(
        self,
        *,
        admin_ids: list[str] | None = None,
        group_admins: list[str] | None = None,
        global_admins: list[str] | None = None,
        admins_by_umo: dict[str, object] | None = None,
        lookup_raises: bool = False,
    ) -> tuple[object, _Config, _Context]:
        command = object.__new__(AiReviewPlugin)
        context = _Context(
            [] if admin_ids is None else admin_ids,
            admins_by_umo=admins_by_umo,
            raises=lookup_raises,
        )
        config = _Config(
            group_admins=group_admins,
            global_admins=global_admins,
        )
        command.context = context
        command.config = config
        command._kv = object()
        return command, config, context

    def test_accepts_explicit_astrbot_administrator(self) -> None:
        command, config, context = self._command(admin_ids=["10001"])

        result = asyncio.run(command._handle_push(_Event(), "admin 10001"))

        self.assertTrue(result.startswith("✅"))
        self.assertEqual(context.requests, ["platform:FriendMessage:10001"])
        self.assertEqual(
            config.saved,
            [
                ("30003", "regex_push_admin", "10001"),
                ("30003", "regex_push_target", "admin"),
            ],
        )

    def test_accepts_recipient_authorized_only_in_private_session(self) -> None:
        command, config, context = self._command(
            admins_by_umo={
                _Event.unified_msg_origin: [],
                "platform:FriendMessage:10001": ["10001"],
            }
        )

        result = asyncio.run(command._handle_push(_Event(), "admin 10001"))

        self.assertTrue(result.startswith("✅"))
        self.assertEqual(context.requests, ["platform:FriendMessage:10001"])
        self.assertEqual(len(config.saved), 2)

    def test_rejects_recipient_authorized_only_in_source_group(self) -> None:
        command, config, context = self._command(
            admins_by_umo={
                _Event.unified_msg_origin: ["10001"],
                "platform:FriendMessage:10001": [],
            }
        )

        result = asyncio.run(command._handle_push(_Event(), "admin 10001"))

        self.assertIn("10001", result)
        self.assertIn("不是 AstrBot 管理员", result)
        self.assertEqual(context.requests, ["platform:FriendMessage:10001"])
        self.assertEqual(config.saved, [])

    def test_rejects_explicit_non_administrator_without_persisting(self) -> None:
        command, config, _context = self._command(admin_ids=["10001"])

        result = asyncio.run(
            command._handle_push(_Event(), "admin 10001,20002")
        )

        self.assertIn("20002", result)
        self.assertIn("不是 AstrBot 管理员", result)
        self.assertEqual(config.saved, [])

    def test_rejects_admin_mode_when_no_recipient_is_configured(self) -> None:
        command, config, _context = self._command(admin_ids=["10001"])

        result = asyncio.run(command._handle_push(_Event(), "admin"))

        self.assertIn("未配置", result)
        self.assertEqual(config.saved, [])

    def test_accepts_existing_group_recipient_list(self) -> None:
        command, config, _context = self._command(
            admin_ids=["10001"], group_admins=["10001"]
        )

        result = asyncio.run(command._handle_push(_Event(), "admin"))

        self.assertTrue(result.startswith("✅"))
        self.assertEqual(
            config.saved,
            [("30003", "regex_push_target", "admin")],
        )

    def test_rejects_invalid_global_fallback_recipient(self) -> None:
        command, config, _context = self._command(
            admin_ids=["10001"], global_admins=["20002"]
        )

        result = asyncio.run(command._handle_push(_Event(), "admin"))

        self.assertIn("20002", result)
        self.assertIn("不是 AstrBot 管理员", result)
        self.assertEqual(config.saved, [])

    def test_command_fails_closed_when_admin_lookup_raises(self) -> None:
        command, config, _context = self._command(
            admin_ids=["10001"], lookup_raises=True
        )

        result = asyncio.run(command._handle_push(_Event(), "admin 10001"))

        self.assertIn("10001", result)
        self.assertIn("不是 AstrBot 管理员", result)
        self.assertEqual(config.saved, [])

    def test_command_fails_closed_for_none_admin_configuration(self) -> None:
        command, config, _context = self._command(
            admins_by_umo={
                _Event.unified_msg_origin: None,
                "platform:FriendMessage:10001": None,
            }
        )

        result = asyncio.run(command._handle_push(_Event(), "admin 10001"))

        self.assertIn("10001", result)
        self.assertIn("不是 AstrBot 管理员", result)
        self.assertEqual(config.saved, [])

    def test_runtime_skips_stale_recipient_and_continues_valid_delivery(self) -> None:
        plugin = object.__new__(AiReviewPlugin)
        plugin.context = _Context(["10001"])
        plugin.config = _Config(global_admins=[])
        plugin.rules = _Rules()
        plugin._get_config = lambda _group_id: {
            "regex_push_target": "admin",
            "regex_push_admin": ["10001", "20002"],
        }
        pushed_sessions: list[str] = []

        async def record_push(session: str, *_args) -> None:
            pushed_sessions.append(session)

        plugin._push_candidates_to = record_push

        with self.assertLogs("astrbot", level="WARNING") as logs:
            asyncio.run(plugin._push_rule_candidates())

        self.assertEqual(pushed_sessions, ["platform:FriendMessage:10001"])
        self.assertEqual(
            plugin.context.requests,
            [
                "platform:FriendMessage:10001",
                "platform:FriendMessage:20002",
            ],
        )
        self.assertEqual(sum("20002" in line for line in logs.output), 1)

    def test_runtime_fails_closed_when_admin_lookup_raises(self) -> None:
        plugin = object.__new__(AiReviewPlugin)
        plugin.context = _Context(["10001"], raises=True)
        plugin.config = _Config(global_admins=[])
        plugin.rules = _Rules()
        plugin._get_config = lambda _group_id: {
            "regex_push_target": "admin",
            "regex_push_admin": ["10001"],
        }
        pushed_sessions: list[str] = []

        async def record_push(session: str, *_args) -> None:
            pushed_sessions.append(session)

        plugin._push_candidates_to = record_push

        with self.assertLogs("astrbot", level="WARNING"):
            asyncio.run(plugin._push_rule_candidates())

        self.assertEqual(pushed_sessions, [])

    def test_runtime_normalizes_ids_and_continues_after_malformed_config(self) -> None:
        plugin = object.__new__(AiReviewPlugin)
        plugin.context = _Context(
            [],
            admins_by_umo={
                "platform:FriendMessage:20002": 123,
                "platform:FriendMessage:10001": ["10001"],
            },
        )
        plugin.config = _Config(global_admins=[])
        plugin.rules = _Rules(("30003", "40004"))
        first_group_recipients = ["20002", " 10001 "]

        def group_config(group_id: str) -> dict[str, object]:
            recipients = (
                first_group_recipients if group_id == "30003" else ["10001"]
            )
            return {
                "regex_push_target": "admin",
                "regex_push_admin": recipients,
            }

        plugin._get_config = group_config
        pushed_sessions: list[str] = []

        async def record_push(session: str, *_args) -> None:
            pushed_sessions.append(session)

        plugin._push_candidates_to = record_push

        with self.assertLogs("astrbot", level="WARNING") as logs:
            asyncio.run(plugin._push_rule_candidates())

        self.assertEqual(
            pushed_sessions,
            [
                "platform:FriendMessage:10001",
                "platform:FriendMessage:10001",
            ],
        )
        self.assertEqual(first_group_recipients, ["20002", " 10001 "])
        self.assertEqual(sum("20002" in line for line in logs.output), 1)


if __name__ == "__main__":
    unittest.main()
