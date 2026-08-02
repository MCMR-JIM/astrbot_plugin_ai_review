from __future__ import annotations

import asyncio
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_PACKAGE_NAME = "_plugin_under_test"
if _PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(_PACKAGE_NAME)
    package.__path__ = [str(_REPO_ROOT)]
    package.__package__ = _PACKAGE_NAME
    sys.modules[_PACKAGE_NAME] = package

_saved_fake_astrbot: dict[str, types.ModuleType] = {}
_real_astrbot_modules: dict[str, types.ModuleType] = {}
if "astrbot" in sys.modules and sys.modules["astrbot"].__spec__ is None:
    _saved_fake_astrbot = {
        name: module
        for name, module in sys.modules.items()
        if name == "astrbot" or name.startswith("astrbot.")
    }
    for name in _saved_fake_astrbot:
        del sys.modules[name]

try:
    from astrbot.api.event import MessageChain
    from astrbot.api.message_components import At, Node, Nodes, Plain
except ImportError:
    _ASTRBOT_AVAILABLE = False

    for name in list(sys.modules):
        if name == "astrbot" or name.startswith("astrbot."):
            del sys.modules[name]
    sys.modules.update(_saved_fake_astrbot)

    if "astrbot" not in sys.modules:
        astrbot = types.ModuleType("astrbot")
        astrbot.__path__ = []
        api = types.ModuleType("astrbot.api")
        api.__path__ = []
        event = types.ModuleType("astrbot.api.event")
        event.AstrMessageEvent = object
        components = types.ModuleType("astrbot.api.message_components")
        components.At = type("At", (), {})
        sys.modules.update(
            {
                "astrbot": astrbot,
                "astrbot.api": api,
                "astrbot.api.event": event,
                "astrbot.api.message_components": components,
            }
        )
else:
    _ASTRBOT_AVAILABLE = True
    _real_astrbot_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "astrbot" or name.startswith("astrbot.")
    }
    if _saved_fake_astrbot:
        for name in _real_astrbot_modules:
            del sys.modules[name]
        sys.modules.update(_saved_fake_astrbot)

from _plugin_under_test.commands.review import ReviewCommandMixin  # noqa: E402
from _plugin_under_test.config import ConfigManager  # noqa: E402
from _plugin_under_test.models import ChatRecord, ReviewResult, ReviewTask  # noqa: E402
from _plugin_under_test.review.punishment import PlatformExecutor  # noqa: E402


class _Event:
    unified_msg_origin = "p:GroupMessage:g1"

    @staticmethod
    def get_self_id() -> str:
        return "bot"


class _RecordingExecutor:
    def __init__(self, error: str = "") -> None:
        self.error = error
        self.calls: list[tuple[str, list[tuple[str, str, str]]]] = []

    async def send_forward(
        self, session: str, items: list[tuple[str, str, str]]
    ) -> str:
        self.calls.append((session, items))
        return self.error


class _Store:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    async def put(self, key: str, value: object) -> None:
        self.values[key] = value


class _ReviewCommands(ReviewCommandMixin):
    def __init__(self, threshold: int, executor: _RecordingExecutor) -> None:
        self.config = ConfigManager({"regex_forward_threshold": threshold})
        self.executor = executor


def _make_task(group_id: str = "g1", record_count: int = 2) -> ReviewTask:
    records = [
        ChatRecord(
            timestamp=float(index),
            nickname=f"user-{index}",
            user_id=str(index),
            content=f"message-{index}",
            group_id=group_id,
        )
        for index in range(record_count)
    ]
    return ReviewTask.create(
        group_id=group_id,
        user_id="target",
        nickname="target-user",
        result=ReviewResult(
            illegal=True,
            risk=90,
            type="spam",
            reason="repeated messages",
            evidence=["message"],
            suggestion="warn",
        ),
        context=records,
        timeout=300,
    )


class DetailForwardThresholdTest(unittest.TestCase):
    def test_zero_threshold_returns_full_text_without_forwarding(self) -> None:
        executor = _RecordingExecutor()
        commands = _ReviewCommands(0, executor)
        task = _make_task()

        result = asyncio.run(commands._send_task_detail(_Event(), task))

        self.assertEqual(result, commands._format_detail(task))
        self.assertEqual(executor.calls, [])

    def test_below_threshold_returns_full_text_without_forwarding(self) -> None:
        executor = _RecordingExecutor()
        commands = _ReviewCommands(3, executor)
        task = _make_task(record_count=2)

        result = asyncio.run(commands._send_task_detail(_Event(), task))

        self.assertEqual(result, commands._format_detail(task))
        self.assertEqual(executor.calls, [])

    def test_at_threshold_sends_forward_and_returns_confirmation(self) -> None:
        executor = _RecordingExecutor()
        commands = _ReviewCommands(2, executor)
        task = _make_task(record_count=2)

        result = asyncio.run(commands._send_task_detail(_Event(), task))

        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(
            result,
            f"📄 已发送任务 #{task.task_id} 详情（合并转发，点击展开）。",
        )

    def test_forward_error_returns_full_text_fallback(self) -> None:
        executor = _RecordingExecutor("boom")
        commands = _ReviewCommands(2, executor)
        task = _make_task(record_count=2)

        result = asyncio.run(commands._send_task_detail(_Event(), task))

        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(result, commands._format_detail(task))

    def test_group_threshold_overrides_global_value_independently(self) -> None:
        executor = _RecordingExecutor()
        commands = _ReviewCommands(3, executor)
        ok, _ = asyncio.run(
            commands.config.set_override(
                _Store(), "g1", "regex_forward_threshold", "2"
            )
        )
        g1_task = _make_task(group_id="g1", record_count=2)
        g2_task = _make_task(group_id="g2", record_count=2)

        g1_result = asyncio.run(commands._send_task_detail(_Event(), g1_task))
        g2_result = asyncio.run(commands._send_task_detail(_Event(), g2_task))

        self.assertTrue(ok)
        self.assertEqual(len(executor.calls), 1)
        self.assertIn("已发送任务", g1_result)
        self.assertEqual(g2_result, commands._format_detail(g2_task))


class _RecordingContext:
    def __init__(self, result: object = True, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, object]] = []

    async def send_message(self, session: str, chain: object) -> object:
        self.calls.append((session, chain))
        if self.error is not None:
            raise self.error
        return self.result


@contextmanager
def _using_real_astrbot():
    if not _saved_fake_astrbot:
        yield
        return
    for name in _saved_fake_astrbot:
        del sys.modules[name]
    sys.modules.update(_real_astrbot_modules)
    try:
        yield
    finally:
        for name in _real_astrbot_modules:
            del sys.modules[name]
        sys.modules.update(_saved_fake_astrbot)


@unittest.skipUnless(_ASTRBOT_AVAILABLE, "real AstrBot components are required")
class PlatformExecutorForwardTest(unittest.TestCase):
    def assert_forward_components(self, context: _RecordingContext) -> None:
        chain = context.calls[0][1]
        self.assertIsInstance(chain, MessageChain)
        self.assertIsInstance(chain.chain[0], Nodes)
        self.assertIsInstance(chain.chain[0].nodes[0], Node)
        self.assertIsInstance(chain.chain[0].nodes[0].content[0], Plain)

    def test_accepts_true_result_with_real_components(self) -> None:
        context = _RecordingContext(True)

        with _using_real_astrbot():
            error = asyncio.run(
                PlatformExecutor(context).send_forward(
                    "p:GroupMessage:g1", [("name", "1", "text")]
                )
            )

        self.assertEqual(error, "")
        self.assert_forward_components(context)

    def test_reports_explicit_false_with_real_components(self) -> None:
        context = _RecordingContext(False)

        with _using_real_astrbot():
            error = asyncio.run(
                PlatformExecutor(context).send_forward(
                    "p:GroupMessage:g1", [("name", "1", "text")]
                )
            )

        self.assertEqual(error, "未找到会话对应的平台: p:GroupMessage:g1")
        self.assert_forward_components(context)

    def test_preserves_exception_with_real_components(self) -> None:
        context = _RecordingContext(error=RuntimeError("boom"))

        with _using_real_astrbot():
            error = asyncio.run(
                PlatformExecutor(context).send_forward(
                    "p:GroupMessage:g1", [("name", "1", "text")]
                )
            )

        self.assertEqual(error, "合并转发发送失败: boom")
        self.assert_forward_components(context)

    def test_rejects_empty_items_without_sending(self) -> None:
        context = _RecordingContext()

        with _using_real_astrbot():
            error = asyncio.run(
                PlatformExecutor(context).send_forward("p:GroupMessage:g1", [])
            )

        self.assertEqual(error, "转发内容为空。")
        self.assertEqual(context.calls, [])


if __name__ == "__main__":
    unittest.main()
