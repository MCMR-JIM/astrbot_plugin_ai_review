"""AI 审核命令（mixin，由 main.py 的 Star 继承注册）。

命令（均为管理员权限）：
- /review @成员 | uid：主动审核指定用户
- /review recent：审核最近聊天记录
- /review list：查看待审核任务
- /review detail <id>：查看任务详情
- /review pass <id>：通过并执行处罚
- /review reject <id>：拒绝任务
- /review rule list|add|del|disable|enable：管理正则规则

宿主 Star 需提供：self.workflow / self.queue / self.config / self.punisher / self.rules。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent

from ..models import ReviewLog
from ..utils.logger import log_review

if TYPE_CHECKING:
    from ..models import ReviewTask

_PER_PAGE = 10


class ReviewCommandMixin:
    """/review 命令实现。"""

    async def _cmd_review(
        self,
        event: AstrMessageEvent,
        target: str = "",
        sub: str = "",
    ):
        """AI 审核命令入口（由 main.py 的 cmd_review 委托调用）。"""
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
            yield event.plain_result(await self._format_list(event))
        elif cmd == "stats":
            yield event.plain_result(
                await self._format_stats(event, (sub or "").strip())
            )
        elif cmd == "rule":
            yield event.plain_result(
                await self._handle_rule(event, (sub or "").strip())
            )
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

    async def _format_list(self, event: AstrMessageEvent) -> str:
        """格式化待审核任务列表。"""
        tasks = await self.queue.list_pending(event.get_group_id())
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
        task = await self.queue.get(task_id)
        if task is None:
            return "❌ 任务不存在或已过期。"
        if task.group_id != event.get_group_id():
            return "❌ 该任务不属于当前群，请到对应群处理。"
        if cmd == "detail":
            return self._format_detail(task)
        if cmd == "pass":
            return await self._approve_task(event, task)
        rejected = await self.queue.reject(task_id, event.get_sender_id())
        if rejected is None:
            return "❌ 任务不存在或已处理。"
        await self._record_decision(rejected, approved=False)
        await self._feedback_rule(rejected, approved=False)
        log_review(
            ReviewLog(
                group_id=rejected.group_id,
                user_id=rejected.user_id,
                risk=rejected.result.risk,
                review_status="rejected",
                admin_id=event.get_sender_id(),
            )
        )
        return f"✅ 已拒绝任务 #{rejected.task_id}。"

    async def _approve_task(self, event: AstrMessageEvent, task: "ReviewTask") -> str:
        """通过任务并执行处罚。"""
        admin_id = event.get_sender_id()
        approved = await self.queue.approve(task.task_id, admin_id)
        if approved is None:
            return "❌ 任务已处理或已过期。"
        punishment_msg = await self._execute_punishment(approved, admin_id)
        await self._record_decision(approved, approved=True)
        await self._feedback_rule(approved, approved=True)
        # 非规则命中任务：异步提炼规则候选（进入待审批池）
        rules = getattr(self, "rules", None)
        if rules is not None and not approved.rule_id:
            asyncio.create_task(
                rules.collect_candidate(
                    getattr(self, "llm", None),
                    getattr(self, "prompt", None),
                    approved,
                )
            )
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

    async def _feedback_rule(self, task: "ReviewTask", approved: bool) -> None:
        """将任务处理结果反馈给命中的规则（激活规则统计与熔断）。"""
        rules = getattr(self, "rules", None)
        if rules is None or not task.rule_id:
            return
        await rules.record_decision(task.rule_id, approved)

    async def _handle_rule(self, event: AstrMessageEvent, sub: str) -> str:
        """处理 /review rule 子命令（正则规则管理）。"""
        rules = getattr(self, "rules", None)
        if rules is None:
            return "❌ 正则规则引擎未启用。"
        raw = (getattr(event, "message_str", "") or "").strip()
        prefix = "/review rule"
        pos = raw.find(prefix)
        rest = raw[pos + len(prefix):].strip() if pos != -1 else sub
        parts = rest.split()
        if not parts:
            return self._rule_usage()
        command = parts[0].lower()
        if command == "list":
            return self._format_rules(rules)
        if command == "pending":
            return self._format_candidates(rules)
        if command == "approve" and len(parts) >= 2:
            ok, message = await rules.approve_candidate(parts[1])
            return ("✅ " if ok else "❌ ") + message
        if command == "deny" and len(parts) >= 2:
            ok = await rules.deny_candidate(parts[1])
            return ("✅ 已拒绝候选 " if ok else "❌ 候选不存在：") + parts[1]
        if command == "add":
            return await self._rule_add(rules, parts[1:], rest)
        if command == "del" and len(parts) >= 2:
            ok = await rules.delete(parts[1])
            return ("✅ 已删除规则 " if ok else "❌ 规则不存在：") + parts[1]
        if command in ("disable", "enable") and len(parts) >= 2:
            ok, message = await rules.set_enabled(
                parts[1], enabled=(command == "enable")
            )
            return ("✅ " if ok else "❌ ") + message
        return self._rule_usage()

    async def _rule_add(self, rules: Any, parts: list[str], rest: str) -> str:
        """添加一条手动规则：/review rule add <pattern> [level]。"""
        if not parts:
            return self._rule_usage()
        level = 1
        tokens = list(parts)
        if tokens[-1].isdigit():
            candidate = int(tokens[-1])
            if 1 <= candidate <= 3:
                level = candidate
                tokens = tokens[:-1]
        pattern = " ".join(tokens).strip()
        if not pattern:
            return "❌ 缺少正则表达式：/review rule add <pattern> [level]"
        ok, message, _ = await rules.add(pattern, source="manual", level=level)
        return ("✅ " if ok else "❌ ") + message

    @staticmethod
    def _format_rules(rules: Any) -> str:
        """格式化规则列表。"""
        records = rules.list()
        if not records:
            return "📋 当前没有正则规则。"
        lines = [f"📋 正则规则（{len(records)}）："]
        for rule in records:
            accuracy = rule.accuracy * 100 if rule.approved + rule.rejected else None
            stat = (
                f"通过 {rule.approved}/拒绝 {rule.rejected}"
                f"（准确率 {accuracy:.0f}%）"
                if accuracy is not None
                else f"命中 {rule.hits}"
            )
            lines.append(
                f"#{rule.rule_id} [{rule.status.value}] "
                f"L{rule.level} {rule.note or rule.pattern[:20]}"
                f" | 来源 {rule.source} | {stat}"
            )
        lines.append(
            "使用 /review rule add <pattern> [level] 添加，"
            "/review rule disable|enable <id> 停用/启用，"
            "/review rule del <id> 删除。"
        )
        return "\n".join(lines)

    @staticmethod
    def _format_candidates(rules: Any) -> str:
        """格式化待审批候选列表。"""
        candidates = rules.candidates()
        if not candidates:
            return "📥 当前没有待审批的规则候选。"
        lines = [f"📥 待审批规则候选（{len(candidates)}）："]
        for candidate in candidates:
            lines.append(
                f"#{candidate.candidate_id} [{candidate.note or candidate.pattern}]"
                f" L{candidate.level}（来源群 {candidate.group_id}，"
                f"任务 {candidate.source_task_id}）"
            )
        lines.append(
            "使用 /review rule approve <id> 批准（进入观察期），"
            "/review rule deny <id> 拒绝。"
        )
        return "\n".join(lines)

    @staticmethod
    def _rule_usage() -> str:
        """正则规则命令用法。"""
        return (
            "🤖 正则规则管理（管理员）\n"
            "/review rule list              查看规则列表\n"
            "/review rule pending           查看待审批候选\n"
            "/review rule approve <id>      批准候选（进入观察期）\n"
            "/review rule deny <id>         拒绝候选\n"
            "/review rule add <pattern> [level]  添加规则（1~3 级）\n"
            "/review rule disable <id>      停用规则\n"
            "/review rule enable <id>       启用规则\n"
            "/review rule del <id>          删除规则"
        )

    async def _record_decision(self, task: "ReviewTask", approved: bool) -> None:
        """记录管理员处理结果到违规统计（若已启用）。"""
        stats = getattr(self, "stats", None)
        if stats is None:
            return
        await stats.record_decision(
            task.group_id,
            task.user_id,
            approved,
            task.result.suggestion if approved else "",
        )

    async def _format_stats(self, event: AstrMessageEvent, target: str) -> str:
        """格式化违规统计。"""
        stats = getattr(self, "stats", None)
        if stats is None:
            return "（未启用违规统计）"
        if target == "all":
            summary = stats.all_summary()
            if not summary:
                return "📊 暂无统计数据。"
            lines = ["📊 各群违规统计："]
            for group_id, rows in summary.items():
                lines.append(f"群 {group_id}：")
                for row in rows[:5]:
                    lines.append(
                        f"  {row['user_id']}：违规 {row['count']} 次"
                        f"（通过 {row['approved']} / 拒绝 {row['rejected']}）"
                    )
            return "\n".join(lines)
        group_id = target or event.get_group_id()
        rows = stats.group_summary(group_id)
        if not rows:
            return f"📊 群 {group_id} 暂无统计数据。"
        lines = [f"📊 群 {group_id} 违规统计："]
        for row in rows[:10]:
            types = "、".join(f"{k}x{v}" for k, v in row["types"].items())
            lines.append(
                f"{row['user_id']}：违规 {row['count']} 次"
                f" | 通过 {row['approved']} | 拒绝 {row['rejected']}"
                + (f" | 类型 {types}" if types else "")
            )
        return "\n".join(lines)

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
            "/review stats     查看本群违规统计\n"
            "/review stats all 查看全部群统计\n"
            "/review rule      管理正则规则（rule 查看详情）\n"
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
