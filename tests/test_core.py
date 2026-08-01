"""核心逻辑冒烟测试（标准库 unittest，无需安装 astrbot/pytest）。

运行方式：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
import sys
import time
import types
import unittest
from pathlib import Path

# 以固定别名的命名空间包方式导入插件源码，与仓库目录名解耦：
# 将仓库根目录注册为命名空间包 _plugin_under_test，其子模块的相对导入
# （如 review.punishment 中的 ..models）可正常解析。
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PKG = "_plugin_under_test"
_pkg = types.ModuleType(_PKG)
_pkg.__path__ = [str(_REPO_ROOT)]  # 命名空间包
_pkg.__package__ = _PKG
sys.modules[_PKG] = _pkg
sys.path.insert(0, str(_REPO_ROOT))

from _plugin_under_test.config import ConfigManager  # noqa: E402
from _plugin_under_test.models import ChatRecord, ReviewResult, ReviewTask  # noqa: E402
from _plugin_under_test.prompt import PromptManager  # noqa: E402
from _plugin_under_test.review.persistence import KVStore  # noqa: E402
from _plugin_under_test.review.filters import to_record, trim_records  # noqa: E402
from _plugin_under_test.review.history import HistoryCache  # noqa: E402
from _plugin_under_test.review.punishment import Punisher  # noqa: E402
from _plugin_under_test.review.queue import ReviewQueue  # noqa: E402
from _plugin_under_test.review.rules import RuleEngine  # noqa: E402
from _plugin_under_test.review.stats import StatsStore  # noqa: E402
from _plugin_under_test.review.workflow import ReviewWorkflow  # noqa: E402
from _plugin_under_test.utils.llm import LLMClient  # noqa: E402
from _plugin_under_test.utils.parser import parse_review_result  # noqa: E402


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
