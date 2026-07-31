"""LLM 回复解析工具。

从模型返回的文本中提取 JSON 并转换为 ReviewResult。
解析失败抛出 ValueError，由调用方决定重试策略。
"""

from __future__ import annotations

import json

from ..models import PunishmentType, ReviewResult


def _extract_json(text: str) -> dict:
    """从模型回复文本中提取第一个 JSON 对象。

    兼容 Markdown 代码块包裹与前后杂散文本。

    Args:
        text: 模型回复的原始文本。

    Returns:
        提取并解析后的 JSON 字典。

    Raises:
        ValueError: 无法提取或解析出合法 JSON 对象。
    """
    content = (text or "").strip()
    if not content:
        raise ValueError("模型回复为空，无法解析。")
    lines = content.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    content = "\n".join(lines).strip()
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("模型回复中未找到 JSON 对象。")
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层不是对象。")
    return data


def parse_review_result(text: str) -> ReviewResult:
    """解析模型回复为审核结果。

    Args:
        text: 模型回复的原始文本。

    Returns:
        审核结果对象。

    Raises:
        ValueError: 文本无法解析为合法审核结果。
    """
    data = _extract_json(text)
    result = ReviewResult.from_dict(data)
    if result.suggestion not in PunishmentType._value2member_map_:
        result.suggestion = PunishmentType.WARN.value
    return result
