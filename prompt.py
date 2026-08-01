"""Prompt 构建与管理。

Prompt 文本独立存放于文件（默认 data/prompts/ 或配置 prompt_path），
代码只负责加载与组装，禁止将 Prompt 写死在代码逻辑中。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import safe_int
from .models import ChatRecord

_SYSTEM_FILE = "system.txt"
_USER_FILE = "user.txt"
_OUTPUT_FILE = "output.txt"
_REASON_FILE = "reason.txt"
_RULE_FILE = "rule.txt"


class PromptManager:
    """Prompt 加载与组装器。

    支持配置热加载：目录（prompt_path）或文件内容变化后自动刷新。

    Attributes:
        system: 系统审核规则原文。
        user: 用户聊天记录模板（含 {records}/{target} 占位符）。
        output: 输出 JSON 格式约束原文。
        reason: 审核原因模板。
        rule: 正则规则提炼模板（含 {type}/{reason}/{evidence}/{records} 占位符）。
    """

    def __init__(
        self,
        plugin_dir: str,
        get_config: Callable[[], dict[str, Any]],
    ) -> None:
        """初始化 Prompt 管理器。

        Args:
            plugin_dir: 插件根目录路径，用于定位默认 prompt 目录。
            get_config: 返回当前插件配置字典的回调，用于热加载。
        """
        self._plugin_dir = Path(plugin_dir)
        self._get_config = get_config
        self._prompt_dir: Path | None = None
        self._cached: dict[str, tuple[Path, float, str]] = {}
        self.system = ""
        self.user = ""
        self.output = ""
        self.reason = ""
        self.rule = ""
        self._sync()

    def _resolve_dir(self) -> Path:
        """解析 prompt 目录：优先配置 prompt_path，否则使用插件内置目录。"""
        raw = str(self._get_config().get("prompt_path", "") or "")
        if raw:
            path = Path(raw)
            if not path.is_absolute():
                path = self._plugin_dir / path
        else:
            path = self._plugin_dir / "data" / "prompts"
        return path

    def _sync(self) -> None:
        """根据最新配置刷新 prompt 目录，并重新加载缺失文件。"""
        prompt_dir = self._resolve_dir()
        if prompt_dir != self._prompt_dir:
            self._prompt_dir = prompt_dir
            self._cached.clear()
        for name in (_SYSTEM_FILE, _USER_FILE, _OUTPUT_FILE, _REASON_FILE, _RULE_FILE):
            self._load(name)

    def _load(self, name: str) -> str:
        """加载单个 prompt 文件（带 mtime 缓存，变化时重读）。

        Args:
            name: 文件名（system.txt/user.txt/output.txt）。

        Returns:
            文件内容；读取失败时返回空字符串。
        """
        if self._prompt_dir is None:
            return ""
        path = self._prompt_dir / name
        cached = self._cached.get(name)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = -1.0
        if cached and cached[0] == path and cached[1] == mtime:
            return cached[2]
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            content = ""
        self._cached[name] = (path, mtime, content)
        if name == _SYSTEM_FILE:
            self.system = content
        elif name == _USER_FILE:
            self.user = content
        elif name == _OUTPUT_FILE:
            self.output = content
        elif name == _REASON_FILE:
            self.reason = content
        elif name == _RULE_FILE:
            self.rule = content
        return content

    def build_system(self) -> str:
        """构建系统 Prompt（默认审核规则）。

        Returns:
            系统提示词文本，已替换 risk 阈值占位符。
        """
        self._sync()
        threshold = safe_int(self._get_config().get("risk_threshold"), 80)
        return self.system.replace("{threshold}", str(threshold))

    def build_user(
        self,
        records: list[ChatRecord],
        target_desc: str = "",
    ) -> str:
        """构建用户 Prompt（聊天记录）。

        Args:
            records: 聊天记录列表。
            target_desc: 审核对象描述，为空表示审核整段记录。

        Returns:
            用户提示词文本。
        """
        self._sync()
        lines = "\n".join(
            record.to_prompt_line(index)
            for index, record in enumerate(records, start=1)
        )
        return (
            self.user.replace("{records}", lines).replace("{target}", target_desc)
        )

    def build_output(self) -> str:
        """构建输出 Prompt（JSON 格式约束 + 审核原因模板）。

        Returns:
            输出提示词文本。
        """
        self._sync()
        if self.reason:
            return f"{self.output}\n\n{self.reason}"
        return self.output

    def build_rule(self, task: Any) -> str:
        """构建正则规则提炼 Prompt（基于已确认的审核任务）。

        Args:
            task: 已通过管理员确认的 ReviewTask。

        Returns:
            渲染后的提炼提示词；模板缺失时返回空字符串。
        """
        self._sync()
        if not self.rule:
            return ""
        records = "\n".join(
            record.to_prompt_line(index)
            for index, record in enumerate(task.context, start=1)
        ) or "（无上下文）"
        evidence = "；".join(task.result.evidence) or "（无证据）"
        return (
            self.rule.replace("{type}", task.result.type or "未知")
            .replace("{reason}", task.result.reason or "")
            .replace("{evidence}", evidence)
            .replace("{records}", records)
        )
