"""审核插件数据模型。

定义插件内部使用的全部数据模型，供其他模块引用。
所有模型均为 dataclass，保证类型清晰、可序列化。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def _parse_bool(value: Any) -> bool:
    """兼容 LLM 返回字符串布尔值（如 "false"/"true"）的情况。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return False


class ReviewStatus(str, Enum):
    """审核任务状态。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PunishmentType(str, Enum):
    """处罚类型。"""

    WARN = "warn"
    MUTE = "mute"
    KICK = "kick"
    BAN = "ban"
    BLACKLIST = "blacklist"


class ReviewMode(str, Enum):
    """审核触发模式。"""

    ACTIVE = "active"
    PASSIVE = "passive"
    BOTH = "both"


@dataclass(slots=True)
class ChatRecord:
    """一条群聊记录。

    Attributes:
        timestamp: 消息时间戳（Unix 秒）。
        nickname: 发送者昵称。
        user_id: 发送者 QQ/平台 ID。
        content: 消息纯文本内容。
        group_id: 所属群号。
    """

    timestamp: float
    nickname: str
    user_id: str
    content: str
    group_id: str = ""

    def to_prompt_line(self, index: int) -> str:
        """将记录格式化为 Prompt 中的一行。

        Args:
            index: 记录序号（从 1 开始）。

        Returns:
            格式化后的文本行。
        """
        time_str = time.strftime("%m-%d %H:%M", time.localtime(self.timestamp))
        return f"{index}. [{time_str}] {self.nickname}({self.user_id}): {self.content}"

    def to_dict(self) -> dict:
        """序列化为字典（用于 KV 持久化）。"""
        return {
            "timestamp": self.timestamp,
            "nickname": self.nickname,
            "user_id": self.user_id,
            "content": self.content,
            "group_id": self.group_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChatRecord":
        """从字典恢复记录。"""
        return cls(
            timestamp=float(data.get("timestamp", 0)),
            nickname=str(data.get("nickname", "")),
            user_id=str(data.get("user_id", "")),
            content=str(data.get("content", "")),
            group_id=str(data.get("group_id", "")),
        )


@dataclass(slots=True)
class ReviewResult:
    """AI 审核结果（对应 AI 返回的 JSON）。

    Attributes:
        illegal: 是否违规。
        risk: 风险值 0~100。
        type: 违规类型。
        reason: 违规原因。
        evidence: 违规证据片段列表。
        suggestion: 建议的处罚类型。
    """

    illegal: bool
    risk: int
    type: str
    reason: str
    evidence: list[str] = field(default_factory=list)
    suggestion: str = PunishmentType.WARN.value

    @classmethod
    def from_dict(cls, data: dict) -> "ReviewResult":
        """从解析后的字典构建审核结果。

        Args:
            data: 由 AI 返回并解析的 JSON 字典。

        Returns:
            审核结果对象。

        Raises:
            ValueError: 当缺少必要字段或字段类型非法时。
        """
        illegal = _parse_bool(data.get("illegal", False))
        raw_risk = data.get("risk", 0)
        try:
            risk = int(raw_risk)
        except (TypeError, ValueError):
            raise ValueError(f"risk 字段非法: {raw_risk!r}")
        risk = max(0, min(100, risk))
        raw_evidence = data.get("evidence", [])
        if not isinstance(raw_evidence, list):
            raw_evidence = []
        evidence = [str(item) for item in raw_evidence]
        suggestion = str(
            data.get("suggestion", PunishmentType.WARN.value)
        ).lower()
        return cls(
            illegal=illegal,
            risk=risk,
            type=str(data.get("type", "")),
            reason=str(data.get("reason", "")),
            evidence=evidence,
            suggestion=suggestion,
        )

    def to_dict(self) -> dict:
        """序列化为字典（用于 KV 持久化）。"""
        return {
            "illegal": self.illegal,
            "risk": self.risk,
            "type": self.type,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "suggestion": self.suggestion,
        }


@dataclass(slots=True)
class ReviewTask:
    """一条待管理员确认的审核任务。

    Attributes:
        task_id: 任务唯一 ID。
        group_id: 群号。
        user_id: 被审核用户 ID。
        nickname: 被审核用户昵称。
        result: AI 审核结果。
        context: 相关的聊天上下文记录。
        created_at: 创建时间戳。
        expires_at: 过期时间戳（超时自动失效）。
        status: 当前状态。
        admin_id: 处理该任务的管理员 ID（未处理为空）。
        decided_at: 处理时间戳（未处理为 None）。
    """

    task_id: str
    group_id: str
    user_id: str
    nickname: str
    result: ReviewResult
    context: list[ChatRecord] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    status: ReviewStatus = ReviewStatus.PENDING
    admin_id: str = ""
    decided_at: float | None = None
    platform_id: str = ""
    session_id: str = ""

    @classmethod
    def create(
        cls,
        group_id: str,
        user_id: str,
        nickname: str,
        result: ReviewResult,
        context: list[ChatRecord],
        timeout: float,
        platform_id: str = "",
        session_id: str = "",
    ) -> "ReviewTask":
        """创建审核任务。

        Args:
            group_id: 群号。
            user_id: 被审核用户 ID。
            nickname: 被审核用户昵称。
            result: AI 审核结果。
            context: 聊天上下文。
            timeout: 超时秒数。

        Returns:
            新创建的审核任务。
        """
        now = time.time()
        return cls(
            task_id=uuid.uuid4().hex[:12],
            group_id=group_id,
            user_id=user_id,
            nickname=nickname,
            result=result,
            context=context,
            created_at=now,
            expires_at=now + timeout,
            platform_id=platform_id,
            session_id=session_id,
        )

    def to_dict(self) -> dict:
        """序列化为字典（用于 KV 持久化）。"""
        return {
            "task_id": self.task_id,
            "group_id": self.group_id,
            "user_id": self.user_id,
            "nickname": self.nickname,
            "result": self.result.to_dict(),
            "context": [record.to_dict() for record in self.context],
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "status": self.status.value,
            "admin_id": self.admin_id,
            "decided_at": self.decided_at,
            "platform_id": self.platform_id,
            "session_id": self.session_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ReviewTask":
        """从字典恢复任务（异常字段按默认值兜底）。"""
        raw_status = data.get("status", ReviewStatus.PENDING.value)
        try:
            status = ReviewStatus(raw_status)
        except ValueError:
            status = ReviewStatus.PENDING
        raw_result = data.get("result")
        if isinstance(raw_result, dict):
            result = ReviewResult.from_dict(raw_result)
        else:
            result = ReviewResult.from_dict(
                {"illegal": False, "risk": 0, "type": "", "reason": ""}
            )
        raw_context = data.get("context") or []
        context = [
            ChatRecord.from_dict(item)
            for item in raw_context
            if isinstance(item, dict)
        ]
        raw_decided = data.get("decided_at")
        return cls(
            task_id=str(data.get("task_id", "")),
            group_id=str(data.get("group_id", "")),
            user_id=str(data.get("user_id", "")),
            nickname=str(data.get("nickname", "")),
            result=result,
            context=context,
            created_at=float(data.get("created_at", 0)),
            expires_at=float(data.get("expires_at", 0)),
            status=status,
            admin_id=str(data.get("admin_id", "")),
            decided_at=float(raw_decided) if raw_decided is not None else None,
            platform_id=str(data.get("platform_id", "")),
            session_id=str(data.get("session_id", "")),
        )

    @property
    def is_expired(self) -> bool:
        """任务是否已超时失效（仅针对待处理状态）。"""
        return self.status == ReviewStatus.PENDING and time.time() >= self.expires_at

    def approve(self, admin_id: str) -> None:
        """标记任务为通过。

        Args:
            admin_id: 处理的管理员 ID。
        """
        self.status = ReviewStatus.APPROVED
        self.admin_id = admin_id
        self.decided_at = time.time()

    def reject(self, admin_id: str) -> None:
        """标记任务为拒绝。

        Args:
            admin_id: 处理的管理员 ID。
        """
        self.status = ReviewStatus.REJECTED
        self.admin_id = admin_id
        self.decided_at = time.time()

    def mark_expired(self) -> None:
        """将任务标记为已失效。"""
        self.status = ReviewStatus.EXPIRED


@dataclass(slots=True)
class ReviewLog:
    """审核日志记录（仅内存日志，不落库）。

    Attributes:
        timestamp: 日志时间戳。
        group_id: 群号。
        user_id: 用户 ID。
        content: 聊天内容。
        risk: AI 风险值。
        review_status: 审核结果。
        admin_id: 处理的管理员。
        punishment: 处罚类型。
        blacklist_sync: 黑库同步状态。
    """

    timestamp: float = field(default_factory=time.time)
    group_id: str = ""
    user_id: str = ""
    content: str = ""
    risk: int = 0
    review_status: str = ""
    admin_id: str = ""
    punishment: str = ""
    blacklist_sync: str = ""
