"""AstrBot LLM 调用客户端。

封装对 AstrBot 已配置大模型的唯一调用点（get_using_provider + text_chat），
不接触任何具体模型 SDK。支持并发限流与异常通知。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TYPE_CHECKING

from .logger import get_logger

if TYPE_CHECKING:
    from astrbot.api.star import Context

_DEFAULT_MAX_CONCURRENCY = 3

logger = get_logger()

Notifier = Callable[[str], Awaitable[None]]


class LLMClient:
    """AstrBot 大模型调用客户端。

    Attributes:
        max_concurrency: 同时进行的 LLM 请求数上限（支持热加载）。
    """

    def __init__(
        self,
        context: "Context",
        get_config: Callable[[], dict[str, Any]],
        notifier: Notifier | None = None,
    ) -> None:
        """初始化 LLM 客户端。

        Args:
            context: AstrBot 插件 Context 对象。
            get_config: 返回当前插件配置字典的回调。
            notifier: 异常告警通知回调，可为空。
        """
        self._context = context
        self._get_config = get_config
        self._notifier = notifier
        self._semaphore = asyncio.Semaphore(_DEFAULT_MAX_CONCURRENCY)
        self._max_concurrency = _DEFAULT_MAX_CONCURRENCY

    @property
    def max_concurrency(self) -> int:
        """当前并发上限。"""
        return self._max_concurrency

    def _sync_config(self) -> None:
        """同步并发上限配置（热加载）。"""
        new_limit = int(
            self._get_config().get("llm_max_concurrency", _DEFAULT_MAX_CONCURRENCY)
        )
        new_limit = max(1, new_limit)
        if new_limit != self._max_concurrency:
            self._semaphore = asyncio.Semaphore(new_limit)
            self._max_concurrency = new_limit

    async def _notify(self, message: str) -> None:
        """发送异常告警通知，失败不影响主流程。

        Args:
            message: 告警内容。
        """
        if self._notifier is None:
            return
        try:
            await self._notifier(message)
        except Exception:
            logger.warning("发送 AI 调用异常通知失败。", exc_info=True)

    async def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        output_prompt: str,
        umo: str,
    ) -> str | None:
        """调用当前会话模型完成一次文本生成。

        Args:
            system_prompt: 系统提示词（审核规则）。
            user_prompt: 用户提示词（聊天记录）。
            output_prompt: 输出约束提示词（JSON 格式）。
            umo: unified_message_origin，用于获取该会话使用的模型。

        Returns:
            模型返回的纯文本；无可用模型或调用失败时返回 None。
        """
        self._sync_config()
        provider = self._context.get_using_provider(umo)
        if provider is None:
            message = f"[AI审核] 未找到可用的对话模型 Provider（umo={umo}），本次审核已跳过。"
            logger.error(message)
            await self._notify(message)
            return None
        prompt = f"{user_prompt}\n\n{output_prompt}"
        async with self._semaphore:
            try:
                response = await provider.text_chat(
                    prompt=prompt,
                    system_prompt=system_prompt,
                )
            except Exception as exc:
                message = f"[AI审核] 调用模型失败: {exc!s}"
                logger.error(message, exc_info=True)
                await self._notify(message)
                return None
        if response is None:
            logger.error("[AI审核] 模型返回为空，本次审核结束。")
            return None
        return response.completion_text or ""
