"""LLM 回复解析工具。

从模型返回的文本中提取 JSON 并转换为 ReviewResult。
解析失败抛出 ValueError，由调用方决定重试策略。
"""

from __future__ import annotations

import json
from typing import Any

from ..models import PunishmentType, ReviewResult
from .logger import get_logger

logger = get_logger()


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


def extract_json_object(text: str, required_key: str | None = None) -> dict:
    """从模型回复文本中提取第一个 JSON 对象。

    兼容 Markdown 代码块包裹与前后杂散文本；散文中的其他合法 JSON
    （如空 {}、无关对象）会被跳过，避免误判。

    Args:
        text: 模型回复的原始文本。
        required_key: 要求对象必须包含的键；为 None 时接受任意 JSON 对象。

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
        # 仅接受含必需键的 JSON 对象；散文中的其他合法 JSON
        # （如 {"note": "..."}、空 {}）一律跳过，避免误判。
        if isinstance(data, dict) and (
            required_key is None or required_key in data
        ):
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
    data = extract_json_object(text, required_key="illegal")
    result = ReviewResult.from_dict(data)
    if result.suggestion not in PunishmentType._value2member_map_:
        result.suggestion = PunishmentType.WARN.value
    return result


async def parse_with_llm_retry(
    llm: Any,
    system: str,
    user: str,
    output: str,
    umo: str,
    text: str,
) -> ReviewResult | None:
    """解析模型回复，失败自动重试一次，再次失败结束本次审核。

    Args:
        llm: LLMClient 实例。
        system: 系统提示词。
        user: 用户提示词。
        output: 输出约束提示词。
        umo: unified_message_origin。
        text: 首次模型回复文本。

    Returns:
        审核结果；两次解析均失败时返回 None。
    """
    try:
        return parse_review_result(text)
    except ValueError as first_err:
        logger.warning("[AI审核] 首次解析失败，重试一次：%s", first_err)
        text = await llm.chat(system, user, output, umo)
        if text is None:
            return None
        try:
            return parse_review_result(text)
        except ValueError as second_err:
            logger.error("[AI审核] 二次解析失败，结束本次审核：%s", second_err)
            return None
