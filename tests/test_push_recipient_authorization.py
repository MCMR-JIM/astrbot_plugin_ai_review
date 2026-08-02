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

from _plugin_under_test.config import ConfigManager


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
        approval_permission: str = "astrbot_admin",
        group_permissions: dict[str, str] | None = None,
    ) -> None:
        self.group_admins = group_admins or []
        self.global_admins = global_admins or []
        self.approval_permission = approval_permission
        self.group_permissions = group_permissions or {}
        self.saved: list[tuple[str, str, str]] = []

    def effective(self, group_id: str) -> dict[str, object]:
        return {
            "regex_push_admin": self.group_admins,
            "regex_approval_permission": self.group_permissions.get(
                group_id, self.approval_permission
            ),
        }

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
    unified_msg_origin = "platform:GroupMessage:30003"

    def __init__(
        self,
        message_str: str = "",
        *,
        admin: bool = False,
        sender_id: str = "10001",
    ) -> None:
        self.message_str = message_str
        self.admin = admin
        self.sender_id = sender_id

    def get_group_id(self) -> str:
        return "30003"

    def get_platform_id(self) -> str:
        return "platform"

    def get_sender_id(self) -> str:
        return self.sender_id

    def is_admin(self) -> bool:
        return self.admin

    def plain_result(self, text: str) -> str:
        return text


class _Rules:
    def __init__(self, group_ids: tuple[str, ...] = ("30003",)) -> None:
        self._candidates = [
            types.SimpleNamespace(group_id=group_id, platform_id="platform")
            for group_id in group_ids
        ]

    def candidates(self) -> list[object]:
        return self._candidates


class _RoleExecutor:
    def __init__(self, roles: dict[tuple[str, str, str], object] | None = None) -> None:
        self.roles = roles or {}
        self.calls: list[tuple[str, str, str]] = []

    async def is_group_moderator(
        self, platform_id: str, group_id: str, user_id: str
    ) -> tuple[bool, str]:
        key = (platform_id, group_id, user_id)
        self.calls.append(key)
        role = self.roles.get(key, "member")
        if isinstance(role, Exception):
            return False, str(role)
        return role in {"owner", "admin"}, ""


class _CandidateRules:
    def __init__(self, candidates: list[object]) -> None:
        self._candidates = candidates
        self.handled: list[tuple[str, str]] = []

    def candidates(self) -> list[object]:
        return self._candidates

    async def approve_candidate(self, candidate_id: str) -> tuple[bool, str]:
        self.handled.append(("approve", candidate_id))
        return True, candidate_id

    async def deny_candidate(self, candidate_id: str) -> bool:
        self.handled.append(("deny", candidate_id))
        return True


class _Store:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    async def put(self, key: str, value: object) -> None:
        self.values[key] = value


class ApprovalPermissionConfigTest(unittest.TestCase):
    def test_defaults_to_astrbot_admin(self) -> None:
        self.assertEqual(
            ConfigManager({}).get("regex_approval_permission"),
            "astrbot_admin",
        )

    def test_accepts_group_admin_global_value(self) -> None:
        config = ConfigManager({})

        ok, _message = asyncio.run(
            config.set_value("regex_approval_permission", "group_admin")
        )

        self.assertTrue(ok)

    def test_rejects_unknown_global_value(self) -> None:
        config = ConfigManager({})

        ok, _message = asyncio.run(
            config.set_value("regex_approval_permission", "unknown")
        )

        self.assertFalse(ok)

    def test_accepts_group_override(self) -> None:
        config = ConfigManager({})

        ok, _message = asyncio.run(
            config.set_override(
                _Store(), "g1", "regex_approval_permission", "group_admin"
            )
        )

        self.assertTrue(ok)

    def test_rejects_unknown_group_override(self) -> None:
        config = ConfigManager({})

        ok, _message = asyncio.run(
            config.set_override(
                _Store(), "g1", "regex_approval_permission", "unknown"
            )
        )

        self.assertFalse(ok)


@unittest.skipIf(AiReviewPlugin is None, "real AstrBot is not installed")
class PushRecipientAuthorizationTest(unittest.TestCase):
    def _command(
        self,
        *,
        admin_ids: list[str] | None = None,
        group_admins: list[str] | None = None,
        global_admins: list[str] | None = None,
        admins_by_umo: dict[str, object] | None = None,
        approval_permission: str = "astrbot_admin",
        roles: dict[tuple[str, str, str], object] | None = None,
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
            approval_permission=approval_permission,
        )
        command.context = context
        command.config = config
        command.executor = _RoleExecutor(roles)
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

    def test_group_admin_mode_accepts_owner_and_admin_recipients(self) -> None:
        for role in ("owner", "admin"):
            with self.subTest(role=role):
                command, config, _context = self._command(
                    approval_permission="group_admin",
                    roles={("platform", "30003", "10001"): role},
                )

                result = asyncio.run(
                    command._handle_push(_Event(), "admin 10001")
                )

                self.assertTrue(result.startswith("✅"))
                self.assertEqual(len(config.saved), 2)

    def test_group_admin_mode_rejects_unauthorized_recipient_roles(self) -> None:
        for role in ("member", None, RuntimeError("onebot failed")):
            with self.subTest(role=role):
                command, config, _context = self._command(
                    approval_permission="group_admin",
                    roles={("platform", "30003", "10001"): role},
                )

                result = asyncio.run(
                    command._handle_push(_Event(), "admin 10001")
                )

                self.assertIn("不是群主或群管理员", result)
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

    def test_group_admin_runtime_rechecks_current_roles(self) -> None:
        plugin = object.__new__(AiReviewPlugin)
        plugin.context = _Context([])
        plugin.config = _Config(global_admins=[])
        plugin.rules = _Rules()
        plugin._get_config = lambda _group_id: {
            "regex_push_target": "admin",
            "regex_push_admin": ["10001", "20002"],
            "regex_approval_permission": "group_admin",
        }
        plugin.executor = _RoleExecutor(
            {
                ("platform", "30003", "10001"): "owner",
                ("platform", "30003", "20002"): "member",
            }
        )
        pushed_sessions: list[str] = []

        async def record_push(session: str, *_args) -> None:
            pushed_sessions.append(session)

        plugin._push_candidates_to = record_push

        with self.assertLogs("astrbot", level="WARNING"):
            asyncio.run(plugin._push_rule_candidates())

        self.assertEqual(pushed_sessions, ["platform:FriendMessage:10001"])
        self.assertEqual(
            plugin.executor.calls,
            [
                ("platform", "30003", "10001"),
                ("platform", "30003", "20002"),
            ],
        )

    @staticmethod
    def _candidate(
        candidate_id: str = "candidate-1",
        *,
        platform_id: str = "platform",
        group_id: str = "30003",
    ) -> object:
        return types.SimpleNamespace(
            candidate_id=candidate_id,
            platform_id=platform_id,
            group_id=group_id,
        )

    def _gate_plugin(
        self,
        *,
        candidates: list[object] | None = None,
        permission: str = "group_admin",
        roles: dict[tuple[str, str, str], object] | None = None,
    ) -> tuple[object, list[tuple[str, str]]]:
        plugin = object.__new__(AiReviewPlugin)
        plugin.rules = _CandidateRules(
            [self._candidate()] if candidates is None else candidates
        )
        plugin.config = _Config(
            approval_permission=permission,
            group_permissions={"30003": permission, "40004": permission},
        )
        plugin.executor = _RoleExecutor(roles)
        delegated: list[tuple[str, str]] = []

        async def delegate(_event, target: str, sub: str):
            delegated.append((target, sub))
            yield "delegated"

        plugin._cmd_review = delegate
        return plugin, delegated

    @staticmethod
    def _run_cmd(plugin: object, event: _Event, target: str, sub: str) -> list[str]:
        async def collect() -> list[str]:
            return [item async for item in plugin.cmd_review(event, target, sub)]

        return asyncio.run(collect())

    def test_astrbot_admin_retains_all_review_subcommands(self) -> None:
        plugin, delegated = self._gate_plugin(permission="astrbot_admin")
        event = _Event(admin=True)

        for target, sub in (
            ("list", ""),
            ("push", "view"),
            ("pass", "task-1"),
            ("reject", "task-1"),
            ("rule", "approve candidate-1"),
            ("provider", ""),
        ):
            with self.subTest(target=target):
                self.assertEqual(
                    self._run_cmd(plugin, event, target, sub), ["delegated"]
                )

        self.assertEqual(len(delegated), 6)

    def test_non_admin_is_denied_in_default_astrbot_admin_mode(self) -> None:
        plugin, delegated = self._gate_plugin(
            permission="astrbot_admin",
            roles={("platform", "30003", "10001"): "owner"},
        )
        event = _Event("/review rule approve candidate-1")

        result = self._run_cmd(plugin, event, "rule", "approve")

        self.assertIn("权限不足", result[0])
        self.assertEqual(delegated, [])

    def test_group_manager_can_only_approve_or_deny_existing_candidate(self) -> None:
        for command in ("approve", "deny"):
            with self.subTest(command=command):
                plugin, delegated = self._gate_plugin(
                    roles={("platform", "30003", "10001"): "admin"}
                )
                event = _Event(f"/review rule {command} candidate-1")

                result = self._run_cmd(plugin, event, "rule", command)

                self.assertEqual(result, ["delegated"])
                self.assertEqual(delegated, [("rule", command)])

    def test_group_manager_is_denied_for_candidate_from_other_group(self) -> None:
        candidate = self._candidate(group_id="40004")
        plugin, delegated = self._gate_plugin(
            candidates=[candidate],
            roles={
                ("platform", "30003", "10001"): "owner",
                ("platform", "40004", "10001"): "member",
            },
        )
        event = _Event("/review rule approve candidate-1")

        result = self._run_cmd(plugin, event, "rule", "approve")

        self.assertIn("权限不足", result[0])
        self.assertEqual(delegated, [])
        self.assertEqual(
            plugin.executor.calls,
            [("platform", "40004", "10001")],
        )

    def test_group_manager_role_revocation_is_effective_immediately(self) -> None:
        role_key = ("platform", "30003", "10001")
        plugin, delegated = self._gate_plugin(roles={role_key: "owner"})
        plugin._get_config = lambda _group_id: {
            "regex_push_target": "admin",
            "regex_push_admin": ["10001"],
            "regex_approval_permission": "group_admin",
        }
        pushed_sessions: list[str] = []

        async def record_push(session: str, *_args) -> None:
            pushed_sessions.append(session)

        plugin._push_candidates_to = record_push
        event = _Event("/review rule approve candidate-1")

        asyncio.run(plugin._push_rule_candidates())
        self.assertEqual(pushed_sessions, ["platform:FriendMessage:10001"])
        plugin.executor.roles[role_key] = "member"

        result = self._run_cmd(plugin, event, "rule", "approve")

        self.assertIn("权限不足", result[0])
        self.assertEqual(delegated, [])
        self.assertEqual(plugin.executor.calls, [role_key, role_key])

    def test_group_manager_is_denied_when_role_lookup_fails(self) -> None:
        role_key = ("platform", "30003", "10001")
        plugin, delegated = self._gate_plugin(
            roles={role_key: RuntimeError("onebot failed")}
        )
        event = _Event("/review rule approve candidate-1")

        result = self._run_cmd(plugin, event, "rule", "approve")

        self.assertIn("权限不足", result[0])
        self.assertEqual(delegated, [])

    def test_group_manager_denies_all_other_or_malformed_commands(self) -> None:
        plugin, delegated = self._gate_plugin(
            roles={("platform", "30003", "10001"): "owner"}
        )
        cases = (
            ("list", "", "/review list"),
            ("push", "view", "/review push view"),
            ("pass", "task-1", "/review pass task-1"),
            ("reject", "task-1", "/review reject task-1"),
            ("auto", "on", "/review auto on"),
            ("config", "x", "/review config x"),
            ("rule", "list", "/review rule list"),
            ("rule", "approve", "/review rule approve"),
            ("rule", "approve", "/review rule approve missing"),
            ("rule", "approve", "/review rule approve candidate-1 extra"),
        )

        for target, sub, raw in cases:
            with self.subTest(raw=raw):
                result = self._run_cmd(plugin, _Event(raw), target, sub)
                self.assertIn("权限不足", result[0])

        self.assertEqual(delegated, [])

    def test_group_manager_denies_candidate_missing_platform_or_group(self) -> None:
        for candidate in (
            self._candidate(platform_id=""),
            self._candidate(group_id=""),
        ):
            with self.subTest(candidate=candidate):
                plugin, delegated = self._gate_plugin(candidates=[candidate])
                event = _Event("/review rule approve candidate-1")

                result = self._run_cmd(plugin, event, "rule", "approve")

                self.assertIn("权限不足", result[0])
                self.assertEqual(delegated, [])

    def test_rule_parser_handles_slash_and_prefix_stripped_commands(self) -> None:
        rules = _CandidateRules([self._candidate()])
        plugin = object.__new__(AiReviewPlugin)
        plugin.rules = rules

        approve = asyncio.run(
            plugin._handle_rule(
                _Event("review rule approve candidate-1"),
                "approve",
            )
        )
        deny = asyncio.run(
            plugin._handle_rule(
                _Event("/review rule deny candidate-1"),
                "deny",
            )
        )

        self.assertTrue(approve.startswith("✅"))
        self.assertTrue(deny.startswith("✅"))
        self.assertEqual(
            rules.handled,
            [("approve", "candidate-1"), ("deny", "candidate-1")],
        )


if __name__ == "__main__":
    unittest.main()
