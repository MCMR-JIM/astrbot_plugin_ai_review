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


if __name__ == "__main__":
    unittest.main()
