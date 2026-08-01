"""核心逻辑冒烟测试（标准库 unittest，无需安装 astrbot/pytest）。

运行方式：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

# 将仓库根目录的上级加入 sys.path，以命名空间包 "repo" 方式导入，
# 使模块内的相对导入（如 review.punishment 中的 ..models）能够解析。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from repo.config import ConfigManager  # noqa: E402
from repo.models import ChatRecord, ReviewResult, ReviewTask  # noqa: E402
from repo.prompt import PromptManager  # noqa: E402
from repo.review.persistence import KVStore  # noqa: E402
from repo.review.history import HistoryCache  # noqa: E402
from repo.review.punishment import Punisher  # noqa: E402
from repo.review.queue import ReviewQueue  # noqa: E402
from repo.review.stats import StatsStore  # noqa: E402
from repo.review.workflow import ReviewWorkflow  # noqa: E402
from repo.utils.llm import LLMClient  # noqa: E402
from repo.utils.parser import parse_review_result  # noqa: E402


class _FakeMessageObj:
    def __init__(self, timestamp: float | None = 1234567890.0) -> None:
        self.timestamp = timestamp


class _FakeKV(KVStore):
    """内存版 KV 存储（用于测试持久化行为）。"""

    def __init__(self) -> None:
        super().__init__(self._get, self._put)
        self.data: dict = {}

    async def _get(self, key: str, default=None):
        return self.data.get(key, default)

    async def _put(self, key: str, value) -> None:
        self.data[key] = value


class _FakeEvent:
    def __init__(self, timestamp: float | None = 1234567890.0) -> None:
        self.message_obj = _FakeMessageObj(timestamp)

    def get_sender_name(self) -> str:
        return "测试用户"

    def get_sender_id(self) -> str:
        return "10001"

    def get_message_outline(self) -> str:
        return "测试消息"


class BooleanParsingTest(unittest.TestCase):
    def test_string_false_is_false(self) -> None:
        result = ReviewResult.from_dict(
            {"illegal": "false", "risk": 95, "type": "x", "reason": "r"}
        )
        self.assertFalse(result.illegal)

    def test_string_true_is_true(self) -> None:
        result = ReviewResult.from_dict(
            {"illegal": "true", "risk": 95, "type": "x", "reason": "r"}
        )
        self.assertTrue(result.illegal)

    def test_int_flags(self) -> None:
        self.assertTrue(ReviewResult.from_dict({"illegal": 1}).illegal)
        self.assertFalse(ReviewResult.from_dict({"illegal": 0}).illegal)


class JsonParsingTest(unittest.TestCase):
    _VALID = (
        '{"illegal": true, "risk": 95, "type": "刷屏", '
        '"reason": "r", "evidence": ["e1"], "suggestion": "mute"}'
    )

    def test_plain_json(self) -> None:
        result = parse_review_result(self._VALID)
        self.assertEqual(result.risk, 95)
        self.assertEqual(result.suggestion, "mute")

    def test_markdown_fence(self) -> None:
        text = f"```json\n{self._VALID}\n```"
        self.assertEqual(parse_review_result(text).risk, 95)

    def test_prose_with_braces_before_and_after(self) -> None:
        text = (
            "审核结果说明：{左括号} 表示开始。\n"
            f"结论：{self._VALID}\n"
            "后续说明：{右括号} 表示结束。"
        )
        result = parse_review_result(text)
        self.assertEqual(result.risk, 95)

    def test_invalid_text_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_review_result("没有任何 JSON 内容")

    def test_unknown_suggestion_falls_back_to_warn(self) -> None:
        text = self._VALID.replace('"mute"', '"delete_hard"')
        self.assertEqual(parse_review_result(text).suggestion, "warn")


class ToRecordTest(unittest.TestCase):
    def test_uses_message_obj_timestamp(self) -> None:
        record = ReviewWorkflow._to_record(_FakeEvent(1700000000.0), "g1")
        self.assertEqual(record.timestamp, 1700000000.0)
        self.assertEqual(record.group_id, "g1")

    def test_falls_back_to_now(self) -> None:
        before = time.time()
        record = ReviewWorkflow._to_record(_FakeEvent(None), "g1")
        self.assertGreaterEqual(record.timestamp, before)


class TrimRecordsTest(unittest.TestCase):
    def test_trim_respects_budget(self) -> None:
        records = [
            ChatRecord(timestamp=1.0, nickname="a", user_id="1", content="hello", group_id="g"),
            ChatRecord(timestamp=2.0, nickname="b", user_id="2", content="world", group_id="g"),
        ]
        trimmed = ReviewWorkflow._trim_records(records, max_chars=10, max_msg_chars=100)
        self.assertGreater(len(trimmed), 0)
        self.assertLessEqual(len(trimmed), len(records))


class PunisherHotReloadTest(unittest.TestCase):
    def test_config_changes_apply(self) -> None:
        cfg = {
            "enable_blacklist": False,
            "mute_duration": 600,
            "punish_pipeline": {},
        }
        punisher = Punisher(None, None, lambda gid="": cfg)
        self.assertEqual(punisher._stages["mute"]._duration, 600)
        self.assertFalse(punisher._blacklist_enabled)

        cfg["mute_duration"] = 1200
        cfg["enable_blacklist"] = True
        cfg["punish_pipeline"] = {"mute": ["mute"]}
        punisher._sync_config()

        self.assertEqual(punisher._stages["mute"]._duration, 1200)
        self.assertTrue(punisher._blacklist_enabled)
        self.assertEqual(punisher._pipelines["mute"], ["mute"])


class QueueTest(unittest.TestCase):
    def test_pending_count_cleans_expired(self) -> None:
        async def scenario() -> int:
            queue = ReviewQueue()
            task = ReviewTask.create(
                group_id="g",
                user_id="u",
                nickname="n",
                result=ReviewResult.from_dict(
                    {"illegal": True, "risk": 90, "type": "t", "reason": "r"}
                ),
                context=[],
                timeout=0.001,
            )
            await queue.add(task)
            await asyncio.sleep(0.01)
            return await queue.pending_count()

        self.assertEqual(asyncio.run(scenario()), 0)


class TaskIdTest(unittest.TestCase):
    def test_task_id_length(self) -> None:
        task = ReviewTask.create(
            group_id="g",
            user_id="u",
            nickname="n",
            result=ReviewResult.from_dict(
                {"illegal": True, "risk": 90, "type": "t", "reason": "r"}
            ),
            context=[],
            timeout=300,
        )
        self.assertEqual(len(task.task_id), 12)


class _StubLLM:
    """记录调用次数并返回固定审核结果的 LLM 桩。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, system: str, user: str, output: str, umo: str) -> str:
        self.calls += 1
        return (
            '{"illegal": true, "risk": 95, "type": "测试", '
            '"reason": "r", "evidence": ["e"], "suggestion": "mute"}'
        )


class _StubGroupEvent:
    """满足 workflow.on_message 最小接口的群消息事件桩。"""

    def __init__(self, sender_id: str = "10001", content: str = "测试消息") -> None:
        self.message_obj = _FakeMessageObj(1700000000.0)
        self.unified_msg_origin = "aiocqhttp:GroupMessage:g1"
        self.role = "member"
        self._sender_id = sender_id
        self._content = content

    def get_group_id(self) -> str:
        return "g1"

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return "测试用户"

    def get_message_outline(self) -> str:
        return self._content

    def get_self_id(self) -> str:
        return "bot"

    def is_admin(self) -> bool:
        return False

    def get_platform_id(self) -> str:
        return "aiocqhttp"


class PassiveReviewToggleTest(unittest.TestCase):
    def test_default_enabled(self) -> None:
        cfg = ConfigManager({})
        self.assertTrue(cfg.get("enable_passive_review", True))

    def test_toggle_via_set_value(self) -> None:
        cfg = ConfigManager({})
        ok, _ = asyncio.run(cfg.set_value("enable_passive_review", "false"))
        self.assertTrue(ok)
        self.assertFalse(cfg.get("enable_passive_review", True))

        ok, _ = asyncio.run(cfg.set_value("enable_passive_review", "true"))
        self.assertTrue(ok)
        self.assertTrue(cfg.get("enable_passive_review", False))


class PassiveReviewWorkflowTest(unittest.TestCase):
    @staticmethod
    def _make_cfg(enabled: bool) -> dict:
        return {
            "enable_passive_review": enabled,
            "enable_history": True,
            "review_mode": "both",
            "risk_threshold": 80,
            "history_count": 50,
            "whitelist": [],
            "min_msg_len": 2,
            "cooldown": 300,
            "review_timeout": 300,
            "max_chat_chars": 3000,
            "max_msg_chars": 200,
            "max_pending_per_user": 2,
            "max_pending_total": 200,
        }

    @classmethod
    def _make_workflow(cls, cfg: dict, llm: _StubLLM):
        history = HistoryCache(lambda gid="": cfg)
        prompt = PromptManager(
            str(Path(__file__).resolve().parent.parent),
            lambda gid="": cfg,
        )
        queue = ReviewQueue()
        workflow = ReviewWorkflow(
            history,
            prompt,
            llm,
            queue,
            lambda gid="": cfg,
        )
        return workflow, history, queue

    def test_disabled_skips_review_but_caches(self) -> None:
        llm = _StubLLM()
        cfg = self._make_cfg(False)
        workflow, history, _ = self._make_workflow(cfg, llm)

        asyncio.run(workflow.on_message(_StubGroupEvent()))

        self.assertEqual(llm.calls, 0)
        self.assertEqual(len(history.get_recent("g1")), 1)

    def test_enabled_triggers_review(self) -> None:
        llm = _StubLLM()
        cfg = self._make_cfg(True)
        workflow, _, queue = self._make_workflow(cfg, llm)

        async def scenario() -> int:
            await workflow.on_message(_StubGroupEvent())
            return await queue.pending_count()

        count = asyncio.run(scenario())
        self.assertEqual(llm.calls, 1)
        self.assertEqual(count, 1)


def _make_task(group_id: str = "g1", user_id: str = "u1") -> ReviewTask:
    return ReviewTask.create(
        group_id=group_id,
        user_id=user_id,
        nickname="n",
        result=ReviewResult.from_dict(
            {"illegal": True, "risk": 90, "type": "t", "reason": "r"}
        ),
        context=[],
        timeout=300,
    )


class SerializationTest(unittest.TestCase):
    def test_task_roundtrip(self) -> None:
        task = ReviewTask.create(
            group_id="g1",
            user_id="u1",
            nickname="nick",
            result=ReviewResult.from_dict(
                {
                    "illegal": True,
                    "risk": 92,
                    "type": "辱骂",
                    "reason": "r",
                    "evidence": ["e"],
                    "suggestion": "mute",
                }
            ),
            context=[
                ChatRecord(1.0, "nick", "u1", "hello", "g1"),
            ],
            timeout=300,
            platform_id="aiocqhttp",
            session_id="session",
        )
        restored = ReviewTask.from_dict(task.to_dict())
        self.assertEqual(restored.task_id, task.task_id)
        self.assertEqual(restored.status, task.status)
        self.assertEqual(restored.result.risk, 92)
        self.assertEqual(restored.result.suggestion, "mute")
        self.assertEqual(len(restored.context), 1)
        self.assertEqual(restored.context[0].content, "hello")
        self.assertEqual(restored.session_id, "session")


class QueuePersistenceTest(unittest.TestCase):
    def test_save_and_restore(self) -> None:
        async def scenario():
            store = _FakeKV()
            queue1 = ReviewQueue(store=store)
            task = _make_task()
            await queue1.add(task)
            queue2 = ReviewQueue(store=store)
            await queue2.load()
            return await queue2.get(task.task_id), task.task_id

        restored, task_id = asyncio.run(scenario())
        self.assertIsNotNone(restored)
        self.assertEqual(restored.task_id, task_id)
        self.assertEqual(restored.user_id, "u1")
        self.assertEqual(restored.result.risk, 90)


class QueueGovernanceTest(unittest.TestCase):
    def test_per_user_and_total_limits(self) -> None:
        async def scenario():
            cfg = {"max_pending_per_user": 1, "max_pending_total": 2}
            queue = ReviewQueue(get_config=lambda gid="": cfg)
            results = []
            for user_id in ("u1", "u1", "u2", "u3"):
                results.append(await queue.add(_make_task(user_id=user_id)))
            return results

        added = asyncio.run(scenario())
        self.assertEqual(added, [True, False, True, False])


class ConfigValidationTest(unittest.TestCase):
    def test_risk_threshold_range(self) -> None:
        cfg = ConfigManager({})
        ok, _ = asyncio.run(cfg.set_value("risk_threshold", "200"))
        self.assertFalse(ok)
        ok, _ = asyncio.run(cfg.set_value("risk_threshold", "70"))
        self.assertTrue(ok)
        self.assertEqual(cfg.get("risk_threshold"), 70)

    def test_temperature_range(self) -> None:
        cfg = ConfigManager({})
        ok, _ = asyncio.run(cfg.set_value("llm_temperature", "3"))
        self.assertFalse(ok)
        ok, _ = asyncio.run(cfg.set_value("llm_temperature", "0.5"))
        self.assertTrue(ok)
        self.assertEqual(cfg.get("llm_temperature"), 0.5)


class GroupOverrideTest(unittest.TestCase):
    def test_override_effective_and_clear(self) -> None:
        cfg = ConfigManager({})
        store = _FakeKV()
        asyncio.run(cfg.load_overrides(store))
        ok, _ = asyncio.run(
            cfg.set_override(store, "g1", "risk_threshold", "70")
        )
        self.assertTrue(ok)
        self.assertEqual(cfg.effective("g1")["risk_threshold"], 70)
        self.assertEqual(cfg.effective("g2")["risk_threshold"], 80)
        ok, _ = asyncio.run(
            cfg.clear_override(store, "g1", "risk_threshold")
        )
        self.assertTrue(ok)
        self.assertEqual(cfg.effective("g1")["risk_threshold"], 80)

    def test_overrides_persist_across_instances(self) -> None:
        store = _FakeKV()
        cfg1 = ConfigManager({})
        asyncio.run(cfg1.set_override(store, "g1", "cooldown", "120"))
        cfg2 = ConfigManager({})
        asyncio.run(cfg2.load_overrides(store))
        self.assertEqual(cfg2.effective("g1")["cooldown"], 120)


class StatsStoreTest(unittest.TestCase):
    def test_record_and_summary(self) -> None:
        async def scenario() -> StatsStore:
            store = _FakeKV()
            stats = StatsStore(store)
            await stats.load()
            await stats.record_violation("g1", "u1", "辱骂")
            await stats.record_violation("g1", "u1", "辱骂")
            await stats.record_violation("g1", "u2", "广告")
            await stats.record_decision("g1", "u1", True, "mute")
            await stats.record_decision("g1", "u1", False)
            return stats

        stats = asyncio.run(scenario())
        rows = stats.group_summary("g1")
        self.assertEqual(rows[0]["user_id"], "u1")
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(rows[0]["approved"], 1)
        self.assertEqual(rows[0]["rejected"], 1)
        self.assertEqual(rows[0]["types"], {"辱骂": 2})


class _Resp:
    def __init__(self, text: str) -> None:
        self.completion_text = text


class _StubProvider:
    def __init__(self, failures: int = 0, temperature_error: bool = False) -> None:
        self.calls = 0
        self.failures = failures
        self.temperature_error = temperature_error
        self.last_kwargs: dict = {}

    async def text_chat(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self.temperature_error and self.calls == 1:
            raise TypeError(
                "text_chat() got an unexpected keyword argument 'temperature'"
            )
        if self.calls <= self.failures:
            raise RuntimeError("network error")
        return _Resp("ok")


class _StubContext:
    def __init__(self, provider: _StubProvider) -> None:
        self.provider = provider

    def get_using_provider(self, umo: str):
        return self.provider


class LLMClientRetryTest(unittest.TestCase):
    @staticmethod
    def _make_client(provider: _StubProvider, notifier=None) -> LLMClient:
        cfg = {"llm_temperature": 0.5, "llm_max_concurrency": 3}
        context = _StubContext(provider)
        return LLMClient(
            context,
            lambda gid="": cfg,
            notifier=notifier,
            retry_times=2,
            retry_delays=(0.0, 0.0),
        )

    def test_retries_then_succeeds(self) -> None:
        provider = _StubProvider(failures=2)
        client = self._make_client(provider)
        text = asyncio.run(client.chat("s", "u", "o", "umo"))
        self.assertEqual(text, "ok")
        self.assertEqual(provider.calls, 3)

    def test_notifies_once_after_final_failure(self) -> None:
        provider = _StubProvider(failures=99)
        notified: list[str] = []

        async def notify(message: str) -> None:
            notified.append(message)

        client = self._make_client(provider, notifier=notify)
        text = asyncio.run(client.chat("s", "u", "o", "umo"))
        self.assertIsNone(text)
        self.assertEqual(provider.calls, 3)
        self.assertEqual(len(notified), 1)

    def test_temperature_passed_through(self) -> None:
        provider = _StubProvider()
        client = self._make_client(provider)
        asyncio.run(client.chat("s", "u", "o", "umo"))
        self.assertEqual(provider.last_kwargs.get("temperature"), 0.5)

    def test_temperature_fallback_when_unsupported(self) -> None:
        provider = _StubProvider(temperature_error=True)
        client = self._make_client(provider)
        text = asyncio.run(client.chat("s", "u", "o", "umo"))
        self.assertEqual(text, "ok")
        self.assertEqual(provider.calls, 2)
        self.assertNotIn("temperature", provider.last_kwargs)


class PromptContentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = ConfigManager({})
        self.prompt = PromptManager(
            str(Path(__file__).resolve().parent.parent),
            lambda gid="": self.cfg.raw,
        )

    def test_system_contains_guardrails(self) -> None:
        system = self.prompt.build_system()
        self.assertIn("数据边界", system)
        self.assertIn("宁缺毋滥", system)
        self.assertIn("80", system)
        self.assertNotIn("{threshold}", system)

    def test_user_target_semantics(self) -> None:
        user = self.prompt.build_user([], "")
        self.assertIn("仅为该用户", user)

    def test_output_has_examples(self) -> None:
        output = self.prompt.build_output()
        self.assertIn("未违规时示例", output)
        self.assertIn("宁轻勿重", output)


if __name__ == "__main__":
    unittest.main()
