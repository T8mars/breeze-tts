from __future__ import annotations

from typing import Any


MAX_ALIASES = 500
MAX_SOURCE_LENGTH = 80
MAX_SPOKEN_LENGTH = 160


def normalize_pronunciation_aliases(value: Any) -> list[dict[str, str]]:
    """Validate an application-level display-text -> spoken-text dictionary."""
    if value is None or value == "":
        return []
    if not isinstance(value, list):
        raise ValueError("读音词典必须是列表。")
    if len(value) > MAX_ALIASES:
        raise ValueError(f"读音词典不能超过 {MAX_ALIASES} 条。")
    aliases: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"第 {index} 条读音规则格式无效。")
        source = str(raw.get("source") or "").strip()
        spoken = str(raw.get("spoken") or "").strip()
        language = str(raw.get("language") or "auto").strip().lower()
        if not source or not spoken:
            raise ValueError(f"第 {index} 条读音规则必须同时填写原文和朗读内容。")
        if len(source) > MAX_SOURCE_LENGTH or len(spoken) > MAX_SPOKEN_LENGTH:
            raise ValueError(f"第 {index} 条读音规则过长。")
        if language not in {"auto", "zh", "en"}:
            raise ValueError(f"第 {index} 条读音规则语言必须是 auto、zh 或 en。")
        key = source.casefold()
        if key in seen:
            raise ValueError(f"读音规则重复：{source}")
        seen.add(key)
        aliases.append({"source": source, "spoken": spoken, "language": language})
    return aliases


def apply_pronunciation_aliases(
    text: str,
    aliases: Any,
    *,
    language: str = "auto",
) -> tuple[str, list[dict[str, Any]]]:
    """Apply longest aliases first and return an auditable replacement list.

    Breeze does not document a native pinyin/phoneme syntax, so the application
    deliberately sends ordinary replacement text to the model while retaining
    the original text for subtitles and project display.
    """
    spoken = str(text or "")
    normalized = normalize_pronunciation_aliases(aliases)
    applicable = [
        item for item in normalized
        if item["language"] in {"auto", str(language or "auto").lower()}
    ]
    replacements: list[dict[str, Any]] = []
    for item in sorted(applicable, key=lambda candidate: len(candidate["source"]), reverse=True):
        count = spoken.count(item["source"])
        if not count:
            continue
        spoken = spoken.replace(item["source"], item["spoken"])
        replacements.append({**item, "count": count})
    return spoken, replacements
