"""AstrBot AI 审核插件入口。

装配各模块（配置/聊天缓存/Prompt/LLM/工作流/队列/处罚/黑库适配器），
注册群消息监听（被动审核）与管理员命令（主动审核）。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .adapters.pimeng import PimengBlacklistAdapter
from .commands.config import ConfigCommandMixin
from .commands.review import ReviewCommandMixin
from .config import ConfigManager
from .prompt import PromptManager
from .review.history import HistoryCache
from .review.punishment import PlatformExecutor, Punisher
from .review.queue import ReviewQueue
from .review.workflow import ReviewWorkflow
from .utils.llm import LLMClient

_PLUGIN_NAME = "astrbot_plugin_ai_review"
_PLUGIN_AUTHOR = "N"
_PLUGIN_DESC = "基于 AstrBot 大模型的群聊 AI 审核助手，生成审核建议供管理员确认后执行处罚。"
_PLUGIN_VERSION = "1.0.0"


@register(_PLUGIN_NAME, _PLUGIN_AUTHOR, _PLUGIN_DESC, _PLUGIN_VERSION)
class AiReviewPlugin(ReviewCommandMixin, ConfigCommandMixin, Star):
    """AI 审核插件主类。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        """初始化插件并装配各模块。

        Args:
            context: AstrBot 插件上下文。
            config: AstrBot 传入的插件配置对象。
        """
        super().__init__(context, config)
        self.config = ConfigManager(config if config else {})
        get_config = self._get_config
        plugin_dir = os.path.dirname(os.path.abspath(__file__))

        self.history = HistoryCache(get_config)
        self.prompt = PromptManager(plugin_dir, get_config)
        self.queue = ReviewQueue()
        self.adapter = PimengBlacklistAdapter(context)
        self.llm = LLMClient(context, get_config, notifier=self._notify_admin)
        self.workflow = ReviewWorkflow(
            self.history,
            self.prompt,
            self.llm,
            self.queue,
            get_config,
        )
        self.executor = PlatformExecutor(context)
        self.punisher = Punisher(self.executor, self.adapter, get_config)

    def _get_config(self) -> dict:
        """返回当前配置字典（供各模块热加载）。"""
        return self.config.raw

    async def _notify_admin(self, message: str) -> None:
        """向配置的管理员发送告警消息。

        Args:
            message: 告警内容。
        """
        admin_ids = [str(uid) for uid in self.config.get("admin_qq", [])]
        if not admin_ids:
            return
        try:
            from astrbot.api.event import MessageChain
            from astrbot.api.message_components import Plain

            chain = MessageChain([Plain(message)])
        except Exception:
            return
        platform_manager = getattr(self.context, "platform_manager", None)
        for platform in (platform_manager.platform_insts if platform_manager else []):
            platform_id = platform.meta().id
            for admin_id in admin_ids:
                try:
                    await self.context.send_message(
                        f"{platform_id}:FriendMessage:{admin_id}",
                        chain,
                    )
                except Exception:
                    continue

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent) -> None:
        """群消息监听：后台触发被动审核，不阻塞消息响应。"""
        mode = str(self.config.get("review_mode", "both"))
        if self.config.get("enable_history", True) or mode in ("passive", "both"):
            asyncio.create_task(self.workflow.on_message(event))
