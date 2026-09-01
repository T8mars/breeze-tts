from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?；;\.])\s*|\n+")


def token_count(tokenizer: Any, text: str) -> int:
    encoded = tokenizer(text, add_special_tokens=False, return_attention_mask=False)
    # Hugging Face returns BatchEncoding, which implements Mapping but is not a
    # dict. Counting the container itself would report its single `input_ids`
    # field instead of the number of tokens and silently disable long-text
    # splitting.
    ids = encoded.get("input_ids", []) if isinstance(encoded, Mapping) else encoded
    if hasattr(ids, "shape"):
        shape = tuple(int(item) for item in ids.shape)
        return shape[-1] if shape else 0
    if ids and isinstance(ids[0], (list, tuple)):
        ids = ids[0]
    return len(ids)


def _split_oversized(text: str, tokenizer: Any, max_tokens: int) -> list[str]:
    result: list[str] = []
    remaining = text.strip()
    while remaining:
        low, high = 1, len(remaining)
        best = 0
        while low <= high:
            middle = (low + high) // 2
            if token_count(tokenizer, remaining[:middle]) <= max_tokens:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best <= 0:
            raise ValueError("文本中存在无法编码的超长片段。")
        cut = best
        if best < len(remaining):
            natural = max(
                remaining.rfind("，", 0, best),
                remaining.rfind(",", 0, best),
                remaining.rfind(" ", 0, best),
            )
            if natural >= max(1, best // 2):
                cut = natural + 1
        result.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return [item for item in result if item]


def split_text_for_model(text: str, tokenizer: Any, max_tokens: int = 384) -> list[str]:
    """Split on sentence boundaries and pack segments under a tokenizer-aware limit."""
    text = text.strip()
    if not text:
        return []
    if token_count(tokenizer, text) <= max_tokens:
        return [text]
    candidates = [item.strip() for item in _SENTENCE_BOUNDARY.split(text) if item.strip()]
    expanded: list[str] = []
    for candidate in candidates:
        if token_count(tokenizer, candidate) <= max_tokens:
            expanded.append(candidate)
        else:
            expanded.extend(_split_oversized(candidate, tokenizer, max_tokens))
    packed: list[str] = []
    current = ""
    for candidate in expanded:
        joined = f"{current} {candidate}" if current else candidate
        if current and token_count(tokenizer, joined) > max_tokens:
            packed.append(current)
            current = candidate
        else:
            current = joined
    if current:
        packed.append(current)
    return packed
