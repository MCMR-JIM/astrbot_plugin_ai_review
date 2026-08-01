"""AI 审核命令（mixin，由 main.py 的 Star 继承注册）。

命令（均为管理员权限）：
- /review @成员 | uid：主动审核指定用户
- /review recent：审核最近聊天记录
- /review list：查看待审核任务
- /review detail <id>：查看任务详情
- /review pass <id>：通过并执行处罚
- /review reject <id>：拒绝任务

宿主 Star 需提供：self.workflow / self.queue / self.config / self.punisher。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.api.event import filter, AstrMessageEvent

from ..models import ReviewLog
from ..utils.logger import log_review

if TYPE_CHECKING:
    from ..models import ReviewTask

_PER_PAGE = 10


class ReviewCommandMixin:
    """/review 命令实现。"""

    @filter.command("review")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_review(
        self,
        event: AstrMessageEvent,
        target: str = "",
        sub: str = "",
    ):
        """AI 审核命令入口。"""
        cmd = (target or "").strip().lower()
        if cmd == "auto":
            sub_cmd = (sub or "").strip().lower()
            if sub_cmd in ("on", "off"):
                ok, message = await self.config.set_value(
                    "enable_passive_review",
                    "true" if sub_cmd == "on" else "false",
                )
                prefix = "✅ 已开启被动自主审核：" if ok else "❌ "
                yield event.plain_result(prefix + message)
                return
            yield event.plain_result(self._usage())
            return
        if cmd == "recent":
            yield event.plain_result(await self._review_recent(event))
        elif cmd == "list":
            yield event.plain_result(self._format_list(event))
        elif cmd in ("detail", "pass", "reject"):
            yield event.plain_result(await self._handle_task(event, cmd, (sub or "").strip()))
        elif cmd.isdigit():
            yield event.plain_result(await self._review_uid(event, cmd))
        else:
            at_id = self._extract_at(event)
            if at_id:
                yield event.plain_result(await self._review_uid(event, at_id))
            else:
                yield event.plain_result(self._usage())

    # ---------- 主动审核 ----------

    async def _review_uid(self, event: AstrMessageEvent, uid: str) -> str:
        """审核指定 QQ 用户。"""
        group_id = event.get_group_id()
        user_records = self.workflow.history.get_user_recent(group_id, uid, 1)
        nickname = user_records[-1].nickname if user_records else uid
        task = await self.workflow.review_target(event, uid, nickname)
        if task is None:
            return "本次审核未发现违规，或目标被过滤（白名单/冷却/无记录）。"
        return (
            f"⚠️ 已生成审核任务 #{task.task_id}\n"
            f"用户: {nickname}({uid})\n"
            f"风险: {task.result.risk}  类型: {task.result.type or '-'}\n"
            f"原因: {task.result.reason or '-'}\n"
            f"建议: {task.result.suggestion}\n"
            f"请用 /review detail {task.task_id} 查看详情，"
            f"/review pass {task.task_id} 处理。"
        )

    async def _review_recent(self, event: AstrMessageEvent) -> str:
        """审核最近聊天记录整体。"""
        task = await self.workflow.review_recent(event)
        if task is None:
            return "本次审核未发现违规，或暂无聊天记录。"
        return (
            f"⚠️ 已生成审核任务 #{task.task_id}（群聊整体）\n"
            f"风险: {task.result.risk}  类型: {task.result.type or '-'}\n"
            f"原因: {task.result.reason or '-'}\n"
            f"建议: {task.result.suggestion}\n"
            f"请用 /review detail {task.task_id} 查看详情，"
            f"/review pass {task.task_id} 处理。"
        )

    # ---------- 队列管理 ----------

    def _format_list(self, event: AstrMessageEvent) -> str:
        """格式化待审核任务列表。"""
        tasks = self.queue.list_pending(event.get_group_id())
        if not tasks:
            return "📋 当前没有待审核任务。"
        lines = [f"📋 待审核任务（{len(tasks)}）："]
        for task in tasks[:_PER_PAGE]:
            lines.append(
                f"#{task.task_id} {task.nickname or task.user_id}({task.user_id}) "
                f"risk={task.result.risk} 类型={task.result.type or '-'} "
                f"建议={task.result.suggestion}"
            )
        if len(tasks) > _PER_PAGE:
            lines.append(f"…共 {len(tasks)} 条")
        lines.append("使用 /review detail <id> 查看详情，/review pass|reject <id> 处理。")
        return "\n".join(lines)

    async def _handle_task(self, event: AstrMessageEvent, cmd: str, task_id: str) -> str:
        """处理 detail / pass / reject 子命令。"""
        if not task_id:
            return f"❌ 请提供任务 ID：/review {cmd} <id>"
        if cmd == "detail":
            task = self.queue.get(task_id)
            if task is None:
                return "❌ 任务不存在或已过期。"
            return self._format_detail(task)
        if cmd == "pass":
            task = self.queue.get(task_id)
            if task is None:
                return "❌ 任务不存在或已过期。"
            return await self._approve_task(event, task)
        task = self.queue.reject(task_id, event.get_sender_id())
        if task is None:
            return "❌ 任务不存在或已处理。"
        log_review(
            ReviewLog(
                group_id=task.group_id,
                user_id=task.user_id,
                risk=task.result.risk,
                review_status="rejected",
                admin_id=event.get_sender_id(),
            )
        )
        return f"✅ 已拒绝任务 #{task.task_id}。"

    async def _approve_task(self, event: AstrMessageEvent, task: "ReviewTask") -> str:
        """通过任务并执行处罚。"""
        admin_id = event.get_sender_id()
        approved = self.queue.approve(task.task_id, admin_id)
        if approved is None:
            return "❌ 任务已处理或已过期。"
        punishment_msg = await self._execute_punishment(approved, admin_id)
        log_review(
            ReviewLog(
                group_id=approved.group_id,
                user_id=approved.user_id,
                risk=approved.result.risk,
                review_status="approved",
                admin_id=admin_id,
                punishment=approved.result.suggestion,
            )
        )
        return f"✅ 已通过任务 #{approved.task_id}。\n{punishment_msg}"

    async def _execute_punishment(
        self,
        task: "ReviewTask",
        admin_id: str,
    ) -> str:
        """处罚执行钩子（由 main.py 注入 punisher）。"""
        punisher = getattr(self, "punisher", None)
        if punisher is None:
            return "（未配置处罚执行器，仅记录通过）"
        if not task.user_id:
            return "（该任务无目标用户，仅记录通过，跳过处罚执行）"
        return await punisher.execute(task, admin_id)

    # ---------- 展示 ----------

    @staticmethod
    def _format_detail(task: "ReviewTask") -> str:
        """格式化任务详情。"""
        context_lines = "\n".join(
            record.to_prompt_line(index)
            for index, record in enumerate(task.context, start=1)
        ) or "（无上下文）"
        evidence_lines = "\n".join(f"- {item}" for item in task.result.evidence) or "（无）"
        return (
            f"📄 任务 #{task.task_id}\n"
            f"群: {task.group_id}\n"
            f"用户: {task.nickname or '未知'}({task.user_id})\n"
            f"状态: {task.status.value}\n"
            f"风险: {task.result.risk}  类型: {task.result.type or '-'}\n"
            f"建议处罚: {task.result.suggestion}\n"
            f"原因: {task.result.reason or '-'}\n"
            f"证据:\n{evidence_lines}\n"
            f"—— 聊天上下文 ——\n{context_lines}"
        )

    @staticmethod
    def _usage() -> str:
        """命令用法说明。"""
        return (
            "🤖 AI 审核命令（管理员）\n"
            "/review @成员     审核指定成员\n"
            "/review <uid>     审核指定 QQ\n"
            "/review recent    审核最近聊天\n"
            "/review auto on   开启被动自主审核\n"
            "/review auto off  关闭被动自主审核\n"
            "/review list      查看待审核任务\n"
            "/review detail <id>   查看详情\n"
            "/review pass <id>     通过并执行处罚\n"
            "/review reject <id>   拒绝任务"
        )

    @staticmethod
    def _extract_at(event: AstrMessageEvent) -> str:
        """从消息链中提取第一个 @ 提及的 QQ。"""
        message_obj = getattr(event, "message_obj", None)
        if message_obj is None:
            return ""
        for comp in getattr(message_obj, "message", []):
            if getattr(comp, "type", "") == "at":
                qq = getattr(comp, "qq", None)
                if qq:
                    return str(qq)
        return ""
