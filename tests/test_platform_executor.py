from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_PACKAGE_NAME = "_plugin_under_test"
if _PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(_PACKAGE_NAME)
    package.__path__ = [str(_REPO_ROOT)]
    package.__package__ = _PACKAGE_NAME
    sys.modules[_PACKAGE_NAME] = package

from _plugin_under_test.review.punishment import PlatformExecutor  # noqa: E402

try:
    from astrbot.api.event import MessageChain  # noqa: F401
    from astrbot.api.message_components import Plain  # noqa: F401
except ImportError:
    _ASTRBOT_AVAILABLE = False
else:
    _ASTRBOT_AVAILABLE = True


class _Context:
    def __init__(self, result: object = True, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    async def send_message(self, session, chain):
        if self.error is not None:
            raise self.error
        return self.result


class _Bot:
    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def call_action(self, **params):
        self.calls.append(params)
        if self.error is not None:
            raise self.error
        if isinstance(self.result, list):
            return self.result.pop(0)
        return self.result


class _Adapter:
    def __init__(
        self, bot: object = None, error: Exception | None = None
    ) -> None:
        self.bot = bot
        self.error = error

    def get_client(self):
        if self.error is not None:
            raise self.error
        return self.bot


class _RoleContext:
    def __init__(
        self, adapter: object = None, error: Exception | None = None
    ) -> None:
        self.adapter = adapter
        self.error = error

    def get_platform_inst(self, platform_id: str):
        if self.error is not None:
            raise self.error
        return self.adapter if platform_id == "platform" else None


@unittest.skipUnless(_ASTRBOT_AVAILABLE, "AstrBot is required for message components")
class PlatformExecutorSendMessageTest(unittest.TestCase):
    def test_reports_explicit_false_result(self) -> None:
        error = asyncio.run(
            PlatformExecutor(_Context(False)).send_message(
                "missing:FriendMessage:1", "x"
            )
        )
        self.assertEqual(error, "未找到会话对应的平台: missing:FriendMessage:1")

    def test_accepts_true_result(self) -> None:
        error = asyncio.run(
            PlatformExecutor(_Context(True)).send_message("p:FriendMessage:1", "x")
        )
        self.assertEqual(error, "")

    def test_accepts_legacy_none_result(self) -> None:
        error = asyncio.run(
            PlatformExecutor(_Context(None)).send_message("p:FriendMessage:1", "x")
        )
        self.assertEqual(error, "")

    def test_preserves_exception_error(self) -> None:
        error = asyncio.run(
            PlatformExecutor(_Context(error=RuntimeError("boom"))).send_message(
                "p:FriendMessage:1", "x"
            )
        )
        self.assertEqual(error, "发送消息失败: boom")


class PlatformExecutorGroupModeratorTest(unittest.TestCase):
    def test_accepts_owner_and_admin_roles(self) -> None:
        for role in ("owner", "admin"):
            with self.subTest(role=role):
                bot = _Bot({"role": role})
                result = asyncio.run(
                    PlatformExecutor(_RoleContext(_Adapter(bot))).is_group_moderator(
                        "platform", "30003", "10001"
                    )
                )
                self.assertEqual(result, (True, ""))
                self.assertEqual(
                    bot.calls,
                    [
                        {
                            "action": "get_group_member_info",
                            "group_id": "30003",
                            "user_id": "10001",
                            "no_cache": True,
                        }
                    ],
                )

    def test_rejects_member_and_malformed_results(self) -> None:
        for result in ({"role": "member"}, None, {}, "owner"):
            with self.subTest(result=result):
                allowed, error = asyncio.run(
                    PlatformExecutor(
                        _RoleContext(_Adapter(_Bot(result)))
                    ).is_group_moderator("platform", "30003", "10001")
                )
                self.assertFalse(allowed)
                self.assertTrue(error)

    def test_rechecks_role_without_cache_after_revocation(self) -> None:
        bot = _Bot([{"role": "owner"}, {"role": "member"}])
        executor = PlatformExecutor(_RoleContext(_Adapter(bot)))

        before = asyncio.run(
            executor.is_group_moderator("platform", "30003", "10001")
        )
        after = asyncio.run(
            executor.is_group_moderator("platform", "30003", "10001")
        )

        self.assertEqual(before, (True, ""))
        self.assertFalse(after[0])
        self.assertEqual(
            bot.calls,
            [
                {
                    "action": "get_group_member_info",
                    "group_id": "30003",
                    "user_id": "10001",
                    "no_cache": True,
                },
                {
                    "action": "get_group_member_info",
                    "group_id": "30003",
                    "user_id": "10001",
                    "no_cache": True,
                },
            ],
        )

    def test_contains_missing_platform_client_and_onebot_errors(self) -> None:
        contexts = (
            _RoleContext(),
            _RoleContext(_Adapter()),
            _RoleContext(error=RuntimeError("platform failed")),
            _RoleContext(_Adapter(error=RuntimeError("client failed"))),
            _RoleContext(_Adapter(_Bot(error=RuntimeError("boom")))),
        )
        for context in contexts:
            with self.subTest(context=context):
                allowed, error = asyncio.run(
                    PlatformExecutor(context).is_group_moderator(
                        "platform", "30003", "10001"
                    )
                )
                self.assertFalse(allowed)
                self.assertTrue(error)


if __name__ == "__main__":
    unittest.main()
