"""审核工作流。

职责：消息缓存、触发方式判断、消息过滤、Prompt 组装、LLM 调用、
结果解析（含重试）、阈值判断、审核任务入队、结构化日志。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from ..models import ChatRecord, ReviewLog, ReviewResult, ReviewTask
from ..prompt import PromptManager
from ..utils.logger import get_logger, log_review
from ..utils.parser import parse_review_result
from .history import HistoryCache
from .persistence import KVStore
from .queue import ReviewQueue
from .stats import StatsStore

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

logger = get_logger()

_FILTER_ROLES = ("admin", "owner")


class ReviewWorkflow:
    """审核工作流编排器。

    依赖注入 HistoryCache / PromptManager / LLMClient / ReviewQueue。
    """

    def __init__(
        self,
        history: HistoryCache,
        prompt: PromptManager,
        llm: Any,
        queue: ReviewQueue,
        get_config: Callable[[str], dict[str, Any]],
        stats: StatsStore | None = None,
        store: KVStore | None = None,
    ) -> None:
        """初始化工作流。

        Args:
            history: 聊天记录缓存。
            prompt: Prompt 组装器。
            llm: LLMClient 实例。
            queue: 审核任务队列。
            get_config: 返回当前插件配置字典的回调，可接受群号参数。
            stats: 违规统计存储（可选）。
            store: KV 持久化存储（用于冷却表，可选）。
        """
        self.history = history
        self.prompt = prompt
        self.llm = llm
        self.queue = queue
        self._get_config = get_config
        self._stats = stats
        self._store = store
        self._cooldowns: dict[str, float] = {}

    async def load_state(self) -> None:
        """从 KV 恢复冷却表。"""
        if self._store is None:
            return
        raw = await self._store.get("cooldowns", {})
        if isinstance(raw, dict):
            self._cooldowns = {
                str(key): float(value)
                for key, value in raw.items()
                if isinstance(value, (int, float))
            }

    # ---------- 公共入口 ----------

    async def on_message(self, event: "AstrMessageEvent") -> None:
        """收到群消息后的被动审核入口。

        流程：缓存记录 → 判断触发方式 → 过滤 → 审核。
        建议由外部以后台任务（asyncio.create_task）调用，避免阻塞消息响应。

        Args:
            event: AstrBot 消息事件。
        """
        group_id = event.get_group_id()
        if not group_id:
            return
        record = self._to_record(event, group_id)
        self.history.add(record)
        if not bool(
            self._get_config(group_id).get("enable_passive_review", True)
        ):
            return
        if self._review_mode(group_id) not in ("passive", "both"):
            return
        skip, reason = self._should_skip(event)
        if skip:
            logger.debug("[AI审核] 消息被过滤：%s (群=%s 用户=%s)", reason, group_id, event.get_sender_id())
            return
        await self._run_review(
            event,
            target_user_id=event.get_sender_id(),
            target_nickname=event.get_sender_name(),
            current_record=record,
        )

    async def review_target(
        self,
        event: "AstrMessageEvent",
        target_user_id: str,
        target_nickname: str,
    ) -> ReviewTask | None:
        """主动审核指定用户（/review @成员 或 /review uid）。

        Args:
            event: 触发命令的消息事件。
            target_user_id: 目标用户 ID。
            target_nickname: 目标用户昵称。

        Returns:
            生成的审核任务；未触发时返回 None。
        """
        return await self._run_review(
            event,
            target_user_id=target_user_id,
            target_nickname=target_nickname,
        )

    async def review_recent(self, event: "AstrMessageEvent") -> ReviewTask | None:
        """主动审核最近聊天记录整体（/review recent）。

        Args:
            event: 触发命令的消息事件。

        Returns:
            生成的审核任务；未触发时返回 None。
        """
        return await self._run_review(event, target_user_id="", target_nickname="")

    # ---------- 内部实现 ----------

    async def _run_review(
        self,
        event: "AstrMessageEvent",
        target_user_id: str,
        target_nickname: str,
        current_record: ChatRecord | None = None,
    ) -> ReviewTask | None:
        """执行一次完整审核。

        Args:
            event: 消息事件。
            target_user_id: 目标用户 ID；为空表示审核整体聊天记录。
            target_nickname: 目标用户昵称。

        Returns:
            审核任务；无结果时返回 None。
        """
        group_id = event.get_group_id()
        if not group_id:
            return None
        config = self._get_config(group_id)
        threshold = int(config.get("risk_threshold", 80))

        if target_user_id:
            skip, reason = self._should_skip_target(event, target_user_id)
            if skip:
                logger.debug("[AI审核] 主动审核被过滤：%s", reason)
                return None

        records = self.history.get_recent(
            group_id,
            int(config.get("history_count", 50)),
        )
        if not records:
            if current_record is not None:
                records = [current_record]
            else:
                logger.info("[AI审核] 群=%s 暂无聊天记录，本次审核跳过。", group_id)
                return None
        records = self._trim_records(
            records,
            int(config.get("max_chat_chars", 3000)),
            int(config.get("max_msg_chars", 200)),
        )

        target_desc = ""
        if target_user_id:
            target_desc = (
                f"本次审核对象：{target_nickname or target_user_id}（{target_user_id}）。"
                "请重点分析该用户的发言。"
            )

        system = self.prompt.build_system()
        user = self.prompt.build_user(records, target_desc)
        output = self.prompt.build_output()
        umo = event.unified_msg_origin

        text = await self.llm.chat(system, user, output, umo)
        if text is None:
            return None
        result = await self._parse_with_retry(system, user, output, umo, text)
        if result is None:
            return None

        if not result.illegal or result.risk < threshold:
            logger.info(
                "[AI审核] 群=%s 用户=%s 判定无违规（risk=%d < %d），结束。",
                group_id,
                target_user_id or "(整体)",
                result.risk,
                threshold,
            )
            return None

        task = ReviewTask.create(
            group_id=group_id,
            user_id=target_user_id,
            nickname=target_nickname,
            result=result,
            context=records,
            timeout=float(config.get("review_timeout", 300)),
            platform_id=event.get_platform_id(),
            session_id=event.unified_msg_origin,
        )
        if not await self.queue.add(task):
            logger.warning(
                "[AI审核] 任务入队被拒绝（队列已满或该用户待处理任务过多）：群=%s 用户=%s",
                group_id,
                target_user_id or "(整体)",
            )
            return None
        if target_user_id:
            await self._touch_cooldown(group_id, target_user_id)
        if self._stats is not None:
            await self._stats.record_violation(
                group_id,
                target_user_id,
                result.type,
            )
        log_review(
            ReviewLog(
                group_id=group_id,
                user_id=target_user_id,
                content=self._user_content(records, target_user_id),
                risk=result.risk,
                review_status="pending",
            )
        )
        logger.info(
            "[AI审核] 群=%s 用户=%s 生成审核任务 %s（risk=%d 类型=%s 建议=%s）。",
            group_id,
            target_user_id or "(整体)",
            task.task_id,
            result.risk,
            result.type,
            result.suggestion,
        )
        return task

    async def _parse_with_retry(
        self,
        system: str,
        user: str,
        output: str,
        umo: str,
        text: str,
    ) -> ReviewResult | None:
        """解析模型回复，失败自动重试一次，再次失败结束本次审核。

        Returns:
            审核结果；两次解析均失败时返回 None。
        """
        try:
            return parse_review_result(text)
        except ValueError as first_err:
            logger.warning("[AI审核] 首次解析失败，重试一次：%s", first_err)
            text = await self.llm.chat(system, user, output, umo)
            if text is None:
                return None
            try:
                return parse_review_result(text)
            except ValueError as second_err:
                logger.error("[AI审核] 二次解析失败，结束本次审核：%s", second_err)
                return None

    # ---------- 过滤器 ----------

    def _should_skip(self, event: "AstrMessageEvent") -> tuple[bool, str]:
        """被动审核前置过滤。

        Returns:
            (是否跳过, 原因)。
        """
        sender_id = event.get_sender_id()
        content = event.get_message_outline()
        if not sender_id or sender_id == event.get_self_id():
            return True, "机器人消息"
        role = str(getattr(event, "role", "member"))
        if role in _FILTER_ROLES or event.is_admin():
            return True, "管理员/群主"
        config = self._get_config(event.get_group_id())
        if sender_id in [str(u) for u in config.get("whitelist", [])]:
            return True, "白名单用户"
        if self._in_cooldown(event.get_group_id(), sender_id):
            return True, "冷却中"
        if not content or not content.strip():
            return True, "空消息"
        if len(content.strip()) < int(config.get("min_msg_len", 2)):
            return True, "过短消息"
        return False, ""

    def _should_skip_target(
        self,
        event: "AstrMessageEvent",
        target_user_id: str,
    ) -> tuple[bool, str]:
        """主动审核前置过滤（仅过滤机器人/白名单/冷却）。

        Returns:
            (是否跳过, 原因)。
        """
        if not target_user_id or target_user_id == event.get_self_id():
            return True, "目标无效或为机器人"
        config = self._get_config(event.get_group_id())
        if target_user_id in [str(u) for u in config.get("whitelist", [])]:
            return True, "目标在白名单"
        if self._in_cooldown(event.get_group_id(), target_user_id):
            return True, "目标冷却中"
        return False, ""

    def _in_cooldown(self, group_id: str, user_id: str) -> bool:
        """判断用户是否处于审核冷却中。

        Args:
            group_id: 群号。
            user_id: 用户 ID。

        Returns:
            冷却中返回 True。
        """
        key = self._cooldown_key(group_id, user_id)
        last = self._cooldowns.get(key)
        if last is None:
            return False
        cooldown = int(self._get_config(group_id).get("cooldown", 300))
        return time.time() - last < cooldown

    async def _touch_cooldown(self, group_id: str, user_id: str) -> None:
        """记录用户最近的审核时间（设置冷却起点）。"""
        if len(self._cooldowns) > 1024:
            self._cleanup_cooldowns()
        self._cooldowns[self._cooldown_key(group_id, user_id)] = time.time()
        if self._store is not None:
            await self._store.put("cooldowns", self._cooldowns)

    def _cleanup_cooldowns(self) -> None:
        """清理已过期的冷却记录，避免字典无限增长。"""
        cooldown = int(self._get_config().get("cooldown", 300))
        now = time.time()
        self._cooldowns = {
            key: ts
            for key, ts in self._cooldowns.items()
            if now - ts < cooldown
        }

    @staticmethod
    def _cooldown_key(group_id: str, user_id: str) -> str:
        return f"{group_id}:{user_id}"

    def _review_mode(self, group_id: str = "") -> str:
        """当前触发模式（支持按群覆盖）。"""
        return str(self._get_config(group_id).get("review_mode", "both"))

    # ---------- 辅助 ----------

    @staticmethod
    def _trim_records(
        records: list[ChatRecord],
        max_chars: int,
        max_msg_chars: int,
    ) -> list[ChatRecord]:
        """按字符预算裁剪聊天记录，控制发给 AI 的 token 量。

        优先保留最新消息；单条消息过长时截断；超过总预算后丢弃更早的记录。

        Args:
            records: 原始聊天记录（时间正序）。
            max_chars: 聊天记录总字符预算。
            max_msg_chars: 单条消息字符上限。

        Returns:
            裁剪后的记录列表（时间正序）。
        """
        if max_chars <= 0:
            return []
        trimmed: list[ChatRecord] = []
        total = 0
        for record in reversed(records):
            content = record.content
            if max_msg_chars > 0 and len(content) > max_msg_chars:
                content = content[:max_msg_chars] + "…"
            cost = len(content) + len(record.nickname) + len(record.user_id)
            if total + cost > max_chars:
                break
            total += cost
            trimmed.append(
                ChatRecord(
                    timestamp=record.timestamp,
                    nickname=record.nickname,
                    user_id=record.user_id,
                    content=content,
                    group_id=record.group_id,
                )
            )
        trimmed.reverse()
        return trimmed

    @staticmethod
    def _to_record(event: "AstrMessageEvent", group_id: str) -> ChatRecord:
        """将消息事件转换为聊天记录。"""
        # AstrMessageEvent 没有 created_at 属性，时间戳来自 message_obj.timestamp
        message_obj = getattr(event, "message_obj", None)
        timestamp = getattr(message_obj, "timestamp", None)
        if timestamp is None:
            timestamp = time.time()
        return ChatRecord(
            timestamp=float(timestamp),
            nickname=event.get_sender_name(),
            user_id=event.get_sender_id(),
            content=event.get_message_outline(),
            group_id=group_id,
        )

    @staticmethod
    def _user_content(records: list[ChatRecord], user_id: str) -> str:
        """提取目标用户的最近发言摘要用于日志。"""
        if not user_id:
            return ""
        texts = [
            r.content for r in records[-5:] if r.user_id == user_id and r.content
        ]
        return " | ".join(texts[-3:])
