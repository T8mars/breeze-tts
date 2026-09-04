from __future__ import annotations

import re
from dataclasses import asdict, dataclass


_ROLE_LINE = re.compile(r"^\s*(?:\[([^\]]+)\]|([^：:]{1,40})[：:])\s*(.+?)\s*$")
_INLINE_VOCAL_EVENT_ROLES = frozenset(
    {"笑", "笑声", "咳嗽", "清嗓子", "叹气", "叹息", "抽泣", "哭", "喘息", "呼气"}
)
_SRT_TIME = re.compile(
    r"^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})$"
)


@dataclass(frozen=True)
class SubtitleItem:
    index: int
    start_ms: int
    end_ms: int
    text: str


def _milliseconds(parts: tuple[str, ...]) -> int:
    hours, minutes, seconds, millis = (int(item) for item in parts)
    return (((hours * 60) + minutes) * 60 + seconds) * 1000 + millis


def parse_srt(text: str) -> list[dict]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    result: list[SubtitleItem] = []
    for block in re.split(r"\n\s*\n", normalized):
        lines = [line.strip("\ufeff ") for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        if lines[0].isdigit():
            index = int(lines.pop(0))
        else:
            index = len(result) + 1
        match = _SRT_TIME.match(lines.pop(0))
        if match is None or not lines:
            raise ValueError(f"SRT 第 {index} 段时间轴无效。")
        start_ms = _milliseconds(match.groups()[:4])
        end_ms = _milliseconds(match.groups()[4:])
        if end_ms <= start_ms:
            raise ValueError(f"SRT 第 {index} 段结束时间必须晚于开始时间。")
        result.append(SubtitleItem(index, start_ms, end_ms, " ".join(lines)))
    return [asdict(item) for item in result]


def parse_multi_role_script(text: str, default_role: str = "旁白") -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    active_role = default_role
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        match = _ROLE_LINE.match(line)
        if match and match.group(1) and match.group(1).strip().casefold() in _INLINE_VOCAL_EVENT_ROLES:
            match = None
        if match:
            active_role = (match.group(1) or match.group(2) or default_role).strip()
            content = match.group(3).strip()
        else:
            content = line
        if result and result[-1]["role"] == active_role:
            result[-1]["text"] += "\n" + content
        else:
            result.append({"role": active_role, "text": content})
    return result
