"""LLM 回复解析工具。

从模型返回的文本中提取 JSON 并转换为 ReviewResult。
解析失败抛出 ValueError，由调用方决定重试策略。
"""

from __future__ import annotations

import json

from ..models import PunishmentType, ReviewResult


def _scan_json_spans(content: str) -> list[tuple[int, int]]:
    """扫描所有可能是顶层 JSON 对象的 [start, end] 区间（按出现顺序）。

    使用括号配对定位，跳过字符串内的花括号，兼容 JSON 前后
    存在含大括号的散文或 Markdown 说明的情况。
    """
    spans: list[tuple[int, int]] = []
    i = 0
    while True:
        start = content.find("{", i)
        if start == -1:
            break
        depth = 0
        in_string = False
        escaped = False
        end = -1
        for j in range(start, len(content)):
            ch = content[j]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end == -1:
            break
        spans.append((start, end))
        i = end + 1
    return spans


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
    candidates = _scan_json_spans(content)
    if not candidates:
        raise ValueError("模型回复中未找到闭合的 JSON 对象。")
    for start, end in candidates:
        try:
            data = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("模型回复中的 JSON 解析失败或顶层不是对象。")


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
