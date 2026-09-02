from __future__ import annotations

import json
import re
import time
import uuid
from copy import deepcopy
from typing import Any

from .script_tools import parse_multi_role_script, parse_srt


SCHEMA_VERSION = 2
LANGUAGES = {"auto", "zh", "en"}
DIRECTION_MODES = {"inherit", "override", "neutral"}
TIMING_POLICIES = {"preserve", "overlay", "strict"}
_INDEX_TEXT_RE = re.compile(r"emotion\s*=\s*text\s*:\s*(.+)$", re.IGNORECASE)
_INDEX_VECTOR_RE = re.compile(r"emotion\s*=\s*vector\s*:", re.IGNORECASE)


def _identifier(value: Any = None) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z0-9_-]{8,80}", text) else uuid.uuid4().hex


def _integer(value: Any, default: int = 0, *, minimum: int = 0) -> int:
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(minimum, parsed)


def _json_object(value: Any, *, max_bytes: int = 128 * 1024) -> dict[str, Any]:
    """Keep small JSON metadata while rejecting runtime objects and NaN values."""
    if not isinstance(value, dict):
        return {}
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > max_bytes:
            return {}
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def normalize_line(raw: dict[str, Any], order: int, *, cursor_ms: int = 0) -> dict[str, Any]:
    text = str(raw.get("text") or "").strip()
    if not text:
        raise ValueError(f"第 {order} 句台词不能为空。")
    if len(text) > 20_000:
        raise ValueError(f"第 {order} 句台词超过 20000 字符。")
    language = str(raw.get("language") or "auto").lower()
    if language not in LANGUAGES:
        raise ValueError(f"第 {order} 句语言仅支持 auto、zh、en。")
    direction_mode = str(raw.get("direction_mode") or "inherit").lower()
    direction_text = str(raw.get("direction_text") or "").strip()
    legacy = str(raw.get("emotion") or "").strip()
    if legacy:
        if _INDEX_VECTOR_RE.search(legacy):
            raise ValueError(
                f"第 {order} 句包含 IndexTTS emotion=vector；Breeze 不支持八维向量，"
                "请先改成可审阅的自然语言演绎指令。"
            )
        match = _INDEX_TEXT_RE.search(legacy)
        if match:
            direction_mode, direction_text = "override", match.group(1).strip()
    if direction_mode not in DIRECTION_MODES:
        raise ValueError(f"第 {order} 句演绎模式无效。")
    if direction_mode == "override" and not direction_text:
        raise ValueError(f"第 {order} 句选择逐句覆盖时必须填写演绎指令。")
    start_ms = _integer(raw.get("start_ms"), cursor_ms)
    end_ms = _integer(raw.get("end_ms"), start_ms + max(800, len(text) * 120))
    if end_ms <= start_ms:
        end_ms = start_ms + max(200, _integer(raw.get("duration_ms"), 1000, minimum=1))
    cfg = raw.get("cfg_scale")
    cfg_scale = None if cfg in {None, ""} else float(cfg)
    if cfg_scale is not None and not 0.1 <= cfg_scale <= 10:
        raise ValueError(f"第 {order} 句 CFG 必须在 0.1 到 10 之间。")
    seed = raw.get("seed")
    seed_value = None if seed in {None, ""} else _integer(seed)
    return {
        "line_id": _identifier(raw.get("line_id")),
        "order": order,
        "role": str(raw.get("role") or "旁白").strip()[:80] or "旁白",
        "voice_id": str(raw.get("voice_id") or "").strip(),
        "language": language,
        "text": text,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "direction_mode": direction_mode,
        "direction_text": direction_text,
        "cfg_scale": cfg_scale,
        "seed": seed_value,
        "audio_file": str(raw.get("audio_file") or "").strip(),
        "generation_metadata": _json_object(raw.get("generation_metadata")),
        "generated_at": _integer(raw.get("generated_at"), 0),
        "dirty_fields": sorted({str(value) for value in raw.get("dirty_fields", [])}),
        "status": str(raw.get("status") or "pending"),
        "error": str(raw.get("error") or ""),
        "error_type": str(raw.get("error_type") or "")[:200],
    }


def normalize_project(raw: dict[str, Any], *, increment_revision: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("工程必须是 JSON 对象。")
    lines_raw = raw.get("lines") or []
    if not isinstance(lines_raw, list) or len(lines_raw) > 500:
        raise ValueError("工程台词必须是列表且不能超过 500 句。")
    lines: list[dict[str, Any]] = []
    cursor = 0
    ids: set[str] = set()
    for order, item in enumerate(lines_raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {order} 句格式无效。")
        line = normalize_line(item, order, cursor_ms=cursor)
        if line["line_id"] in ids:
            line["line_id"] = uuid.uuid4().hex
        ids.add(line["line_id"])
        lines.append(line)
        cursor = line["end_ms"] + _integer(raw.get("timing", {}).get("gap_ms"), 200)
    timing_raw = raw.get("timing") if isinstance(raw.get("timing"), dict) else {}
    policy = str(timing_raw.get("policy") or timing_raw.get("slot_policy") or "preserve")
    if policy not in TIMING_POLICIES:
        policy = "preserve"
    revision = _integer(raw.get("revision"), 0)
    if increment_revision:
        revision += 1
    now = int(time.time())
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": _identifier(raw.get("project_id")),
        "revision": revision,
        "name": str(raw.get("name") or "未命名对白工程").strip()[:120] or "未命名对白工程",
        "created_at": _integer(raw.get("created_at"), now),
        "updated_at": _integer(raw.get("updated_at"), now),
        "defaults": deepcopy(raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}),
        "timing": {
            "policy": policy,
            "gap_ms": _integer(timing_raw.get("gap_ms"), 200),
            "snap_ms": _integer(timing_raw.get("snap_ms"), 50),
        },
        "checkpoint": _json_object(raw.get("checkpoint")),
        "lines": lines,
    }


def new_project(name: str = "未命名对白工程") -> dict[str, Any]:
    return normalize_project({"name": name, "lines": []})


def apply_timeline_edit(
    project: dict[str, Any], *, line_id: str, start_ms: Any, end_ms: Any, revision: int
) -> dict[str, Any]:
    current = normalize_project(project)
    if int(revision) != current["revision"]:
        raise RuntimeError(f"工程版本冲突：当前 revision={current['revision']}。")
    selected = next((line for line in current["lines"] if line["line_id"] == line_id), None)
    if selected is None:
        raise KeyError("台词不存在。")
    start, end = _integer(start_ms), _integer(end_ms)
    if end <= start:
        raise ValueError("结束时间必须晚于开始时间。")
    selected["start_ms"], selected["end_ms"] = start, end
    selected["dirty_fields"] = sorted(set(selected["dirty_fields"]) | {"timing"})
    return normalize_project(current, increment_revision=True)


def parse_dialogue(kind: str, text: str, *, default_role: str = "旁白") -> dict[str, Any]:
    source = str(text or "")
    if not source.strip():
        raise ValueError("导入内容不能为空。")
    warnings: list[str] = []
    if kind == "srt":
        lines = [
            {
                "role": default_role,
                "text": item["text"],
                "start_ms": item["start_ms"],
                "end_ms": item["end_ms"],
            }
            for item in parse_srt(source)
        ]
    elif kind == "script":
        cursor = 0
        lines = []
        for item in parse_multi_role_script(source):
            duration = max(800, len(item["text"]) * 120)
            lines.append({**item, "start_ms": cursor, "end_ms": cursor + duration})
            cursor += duration + 200
    elif kind == "txt":
        lines, cursor = [], 0
        for number, raw_line in enumerate(source.splitlines(), 1):
            if not raw_line.strip():
                continue
            parts = [part.strip() for part in raw_line.split("|")]
            role = parts[0] if len(parts) > 1 else default_role
            value = parts[1] if len(parts) > 1 else parts[0]
            language = (parts[2] if len(parts) > 2 else "auto").lower()
            direction = parts[3] if len(parts) > 3 else ""
            if _INDEX_VECTOR_RE.search(direction):
                raise ValueError(
                    f"第 {number} 行包含 emotion=vector；请先转换为自然语言演绎指令。"
                )
            match = _INDEX_TEXT_RE.search(direction)
            if match:
                direction = match.group(1).strip()
                warnings.append(f"第 {number} 行已把 Index emotion=text 映射为 Breeze 演绎指令。")
            duration = max(800, len(value) * 120)
            lines.append({
                "role": role, "text": value, "language": language,
                "direction_mode": "override" if direction else "inherit",
                "direction_text": direction, "start_ms": cursor, "end_ms": cursor + duration,
            })
            cursor += duration + 200
    elif kind == "json":
        payload = json.loads(source)
        if isinstance(payload, list):
            payload = {"lines": payload}
        project = normalize_project(payload)
        return {"project": project, "warnings": warnings}
    else:
        raise ValueError("导入类型仅支持 srt、script、txt、json。")
    return {"project": normalize_project({"lines": lines}), "warnings": warnings}


def to_srt(project: dict[str, Any]) -> str:
    normalized = normalize_project(project)

    def stamp(value: int) -> str:
        hours, remain = divmod(value, 3_600_000)
        minutes, remain = divmod(remain, 60_000)
        seconds, milliseconds = divmod(remain, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    blocks = []
    for line in normalized["lines"]:
        blocks.append(
            f"{line['order']}\n{stamp(line['start_ms'])} --> {stamp(line['end_ms'])}\n{line['text']}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def effective_generation(line: dict[str, Any], voice: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Map per-line controls to native Breeze request fields without faking Index controls."""
    merged = {**defaults, "text": line["text"], "voice_id": line.get("voice_id") or defaults.get("voice_id", "")}
    mode = str(voice.get("mode") or merged.get("mode") or "design")
    inherited = str(voice.get("instruction") or defaults.get("instruction") or "Speak clearly and naturally.")
    direction_mode = line.get("direction_mode", "inherit")
    if direction_mode == "override":
        merged["instruction"] = line.get("direction_text") or inherited
        if mode == "clone":
            mode = "direction"
    elif direction_mode == "neutral":
        merged["instruction"] = "Speak clearly and naturally."
        if mode == "clone":
            mode = "direction"
    else:
        merged["instruction"] = inherited
    merged["mode"] = mode
    if line.get("cfg_scale") is not None:
        merged["cfg_scale"] = line["cfg_scale"]
    if line.get("seed") is not None:
        merged["seed"] = line["seed"]
    return merged
