"""核心逻辑冒烟测试（标准库 unittest，无需安装 astrbot/pytest）。

运行方式：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

# 将仓库根目录的上级加入 sys.path，以命名空间包 "repo" 方式导入，
# 使模块内的相对导入（如 review.punishment 中的 ..models）能够解析。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from repo.models import ChatRecord, ReviewResult, ReviewTask  # noqa: E402
from repo.review.punishment import Punisher  # noqa: E402
from repo.review.queue import ReviewQueue  # noqa: E402
from repo.review.workflow import ReviewWorkflow  # noqa: E402
from repo.utils.parser import parse_review_result  # noqa: E402


class _FakeMessageObj:
    def __init__(self, timestamp: float | None = 1234567890.0) -> None:
        self.timestamp = timestamp


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
        punisher = Punisher(None, None, lambda: cfg)
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
        queue.add(task)
        time.sleep(0.01)
        self.assertEqual(queue.pending_count, 0)


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


if __name__ == "__main__":
    unittest.main()
