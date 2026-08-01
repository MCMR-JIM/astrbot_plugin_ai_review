"""审核工作流。

职责：消息缓存与过滤、正则规则层预筛（命中跳过 LLM）、Prompt 组装、
LLM 调用、结果解析（含重试）、阈值判断、审核任务入队、结构化日志、
违规规则沉淀。过滤/冷却/裁剪等辅助见 filters.py。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from ..config import safe_int
from ..models import ChatRecord, ReviewLog, ReviewResult, ReviewTask
from ..prompt import PromptManager
from ..utils.logger import get_logger, log_event, log_review, review_context
from ..utils.parser import parse_with_llm_retry
from .filters import CooldownManager, MessageFilters, to_record, trim_records, user_content
from .history import HistoryCache
from .persistence import KVStore
from .queue import ReviewQueue
from .stats import StatsStore

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

logger = get_logger()

# 插件自身命令前缀：此类消息不进入聊天缓存，避免污染审核上下文。
_COMMAND_PREFIX = "/review"


class ReviewWorkflow:
    """审核工作流编排器。

    依赖注入 HistoryCache / PromptManager / LLMClient / ReviewQueue /
    RuleEngine（可选）。
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
        rules: Any | None = None,
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
            rules: 正则规则引擎（可选，未配置时跳过规则层）。
        """
        self.history = history
        self.prompt = prompt
        self.llm = llm
        self.queue = queue
        self._get_config = get_config
        self._stats = stats
        self._rules = rules
        self._cooldown = CooldownManager(get_config, store)
        self.filters = MessageFilters(get_config, self._cooldown)

    async def load_state(self) -> None:
        """从 KV 恢复冷却表。"""
        await self._cooldown.load_state()

    # ---------- 公共入口 ----------

    async def on_message(self, event: "AstrMessageEvent") -> None:
        """收到群消息后的被动审核入口。

        流程：缓存记录 → 判断触发方式 → 过滤 → 正则规则层预筛 → 审核。
        建议由外部以后台任务（asyncio.create_task）调用，避免阻塞消息响应。

        Args:
            event: AstrBot 消息事件。
        """
        group_id = event.get_group_id()
        if not group_id:
            return
        record = to_record(event, group_id)
        # 机器人消息与插件自身命令不缓存，避免污染审核上下文
        if not record.user_id or record.user_id == event.get_self_id():
            return
        if (record.content or "").strip().startswith(_COMMAND_PREFIX):
            return
        self.history.add(record)
        config = self._get_config(group_id)
        if not bool(config.get("enable_passive_review", True)):
            return
        if self.filters.review_mode(group_id) not in ("passive", "both"):
            return
        skip, reason = self.filters.should_skip(event)
        if skip:
            logger.debug(
                "[AI审核] 消息被过滤：%s (群=%s 用户=%s)",
                reason,
                group_id,
                event.get_sender_id(),
            )
            return
        with review_context(
            group_id=group_id,
            user_id=event.get_sender_id(),
            provider=self.llm.last_provider_id,
        ):
            log_event("message_received", content=record.content[:80])
            # 正则规则层：命中已激活规则直接生成任务，跳过 LLM 调用
            if await self._try_rule_prefilter(event, record, config):
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
        group_id = event.get_group_id()
        with review_context(
            group_id=group_id or "",
            user_id=target_user_id,
            provider=self.llm.last_provider_id,
        ):
            log_event("manual_review", target=target_user_id or "(整体)")
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
        with review_context(
            group_id=event.get_group_id() or "",
            provider=self.llm.last_provider_id,
        ):
            log_event("manual_review", target="(整体)")
            return await self._run_review(event, target_user_id="", target_nickname="")

    # ---------- 规则层 ----------

    async def _try_rule_prefilter(
        self,
        event: "AstrMessageEvent",
        record: ChatRecord,
        config: dict[str, Any],
    ) -> bool:
        """正则规则层预筛：命中激活规则直接生成任务；返回是否已处理。

        观察期规则命中时不拦截，仍走 LLM，并记录判定一致性。
        """
        if self._rules is None or not bool(
            config.get("enable_regex_prefilter", True)
        ):
            return False
        hits = self._rules.match(record.content)
        if hits:
            result = self._rules.build_result(hits[0], record.content)
            task = await self._enqueue_task(
                event,
                [record],
                record.user_id,
                record.nickname,
                result,
                event.get_group_id(),
                config,
                rule_id=hits[0].rule_id,
            )
            if task is not None:
                await self._rules.record_hit(hits[0].rule_id)
            return True
        observing = self._rules.match_observing(record.content)
        task = await self._run_review(
            event,
            target_user_id=record.user_id,
            target_nickname=record.nickname,
            current_record=record,
        )
        for rule in observing:
            await self._rules.record_observation(rule.rule_id, task is not None)
        return True

    # ---------- 内部实现 ----------

    async def _run_review(
        self,
        event: "AstrMessageEvent",
        target_user_id: str,
        target_nickname: str,
        current_record: ChatRecord | None = None,
    ) -> ReviewTask | None:
        """执行一次完整 LLM 审核。

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
        threshold = safe_int(config.get("risk_threshold"), 80)

        if target_user_id:
            skip, reason = self.filters.should_skip_target(event, target_user_id)
            if skip:
                logger.debug("[AI审核] 主动审核被过滤：%s", reason)
                return None
        records = self.history.get_recent(
            group_id,
            safe_int(config.get("history_count"), 50),
        )
        if not records:
            if current_record is not None:
                records = [current_record]
            else:
                logger.info("[AI审核] 群=%s 暂无聊天记录，本次审核跳过。", group_id)
                return None
        records = trim_records(
            records,
            safe_int(config.get("max_chat_chars"), 3000),
            safe_int(config.get("max_msg_chars"), 200),
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
            log_event("llm_call_failed", group_id=group_id)
            return None
        result = await parse_with_llm_retry(self.llm, system, user, output, umo, text)
        if result is None:
            log_event("parse_failed", group_id=group_id)
            return None

        if not result.illegal or result.risk < threshold:
            logger.info(
                "[AI审核] 群=%s 用户=%s 判定无违规（risk=%d < %d），结束。",
                group_id,
                target_user_id or "(整体)",
                result.risk,
                threshold,
            )
            log_event("review_clean", group_id=group_id, risk=result.risk)
            return None

        return await self._enqueue_task(
            event,
            records,
            target_user_id,
            target_nickname,
            result,
            group_id,
            config,
            llm_provider=self.llm.last_provider_id,
        )

    async def _enqueue_task(
        self,
        event: "AstrMessageEvent",
        records: list[ChatRecord],
        target_user_id: str,
        target_nickname: str,
        result: ReviewResult,
        group_id: str,
        config: dict[str, Any],
        rule_id: str = "",
        llm_provider: str = "",
    ) -> ReviewTask | None:
        """创建并加入审核任务，记录冷却/统计/日志。

        Returns:
            入队成功返回任务；被队列拒绝返回 None。
        """
        task = ReviewTask.create(
            group_id=group_id,
            user_id=target_user_id,
            nickname=target_nickname,
            result=result,
            context=records,
            timeout=float(safe_int(config.get("review_timeout"), 300)),
            platform_id=event.get_platform_id(),
            session_id=event.unified_msg_origin,
            rule_id=rule_id,
            llm_provider=llm_provider,
        )
        if not await self.queue.add(task):
            logger.warning(
                "[AI审核] 任务入队被拒绝（队列已满或该用户待处理任务过多）：群=%s 用户=%s",
                group_id,
                target_user_id or "(整体)",
            )
            return None
        if target_user_id:
            await self._cooldown.touch(group_id, target_user_id)
        if self._stats is not None:
            await self._stats.record_violation(group_id, target_user_id, result.type)
        log_review(
            ReviewLog(
                group_id=group_id,
                user_id=target_user_id,
                content=user_content(records, target_user_id),
                risk=result.risk,
                review_status="pending",
                task_id=task.task_id,
                llm_provider=llm_provider,
            )
        )
        logger.info(
            "[AI审核] 群=%s 用户=%s 生成审核任务 %s（risk=%d 类型=%s 建议=%s%s）。",
            group_id,
            target_user_id or "(整体)",
            task.task_id,
            result.risk,
            result.type,
            result.suggestion,
            " 规则=" + rule_id if rule_id else "",
        )
        log_event(
            "review_created",
            task_id=task.task_id,
            user_id=target_user_id,
            risk=result.risk,
            type=result.type,
            suggestion=result.suggestion,
            rule_id=rule_id,
            provider=llm_provider,
        )
        return task
