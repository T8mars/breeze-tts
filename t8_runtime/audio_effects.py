from __future__ import annotations

from typing import Any
import re

import numpy as np


EFFECT_PRESETS: dict[str, dict[str, Any]] = {
    "none": {"label": "原声（无效果）", "category": "基础", "taps": ()},
    # Indoor / studio spaces. These are intentionally conservative so speech stays intelligible.
    "small_room": {"label": "小房间", "category": "室内空间", "taps": ((18, .18), (37, .12), (61, .08))},
    "bedroom": {"label": "卧室", "category": "室内空间", "taps": ((24, .15), (49, .10), (83, .06))},
    "office": {"label": "办公室", "category": "室内空间", "taps": ((31, .17), (67, .11), (108, .07))},
    "classroom": {"label": "教室", "category": "室内空间", "taps": ((43, .20), (91, .13), (151, .08))},
    "studio": {"label": "录音棚", "category": "室内空间", "taps": ((13, .07), (29, .04), (47, .025))},
    "hall": {"label": "大厅", "category": "大型空间", "taps": ((58, .23), (128, .16), (225, .10), (340, .06))},
    "auditorium": {"label": "礼堂", "category": "大型空间", "taps": ((72, .24), (158, .17), (288, .11), (470, .07))},
    "church": {"label": "教堂", "category": "大型空间", "taps": ((95, .28), (215, .20), (410, .13), (720, .08))},
    "bathroom": {"label": "浴室", "category": "大型空间", "taps": ((21, .24), (46, .18), (78, .12), (119, .07))},
    "mountain_echo": {"label": "山间回音", "category": "户外／特殊空间", "taps": ((220, .44), (470, .27), (780, .15))},
    "valley": {"label": "峡谷远回声", "category": "户外／特殊空间", "taps": ((310, .42), (690, .25), (1120, .13))},
    "cave": {"label": "洞穴", "category": "户外／特殊空间", "taps": ((75, .28), (170, .22), (330, .15), (610, .10), (940, .06))},
    "tunnel": {"label": "隧道", "category": "户外／特殊空间", "taps": ((85, .30), (190, .21), (335, .14), (530, .09))},
    # Device/communication colours. Filters are deterministic and add no artificial noise.
    "telephone": {"label": "电话通话", "category": "设备／传播", "taps": (), "highpass_hz": 300, "lowpass_hz": 3400, "drive": 1.35},
    "walkie_talkie": {"label": "对讲机", "category": "设备／传播", "taps": ((17, .06),), "highpass_hz": 420, "lowpass_hz": 3000, "drive": 2.2},
    "radio": {"label": "广播收音机", "category": "设备／传播", "taps": (), "highpass_hz": 180, "lowpass_hz": 5200, "drive": 1.55},
    "megaphone": {"label": "扩音器", "category": "设备／传播", "taps": ((28, .09),), "highpass_hz": 520, "lowpass_hz": 3900, "drive": 2.6},
    # Creative tone presets. They remain post effects and do not alter model identity.
    "warm": {"label": "温暖质感", "category": "质感／创意", "taps": (), "lowpass_hz": 8200, "drive": 1.18},
    "bright": {"label": "明亮清晰", "category": "质感／创意", "taps": (), "pre_emphasis": .24},
    "muffled": {"label": "隔墙闷声", "category": "质感／创意", "taps": ((42, .08),), "lowpass_hz": 1150},
    "dream": {"label": "梦境氛围", "category": "质感／创意", "taps": ((38, .16), (92, .12), (185, .09), (360, .05)), "lowpass_hz": 7200},
    "robot": {"label": "机器人", "category": "质感／创意", "taps": ((12, .05),), "ring_hz": 42.0, "drive": 1.3},
}
_LABEL_TO_PRESET = {str(value["label"]): key for key, value in EFFECT_PRESETS.items()}
_INLINE_LABEL_PATTERN = "|".join(
    sorted((re.escape(value) for value in _LABEL_TO_PRESET), key=len, reverse=True)
)
_INLINE_EFFECT_RE = re.compile(
    rf"(?:\[FX\s*:\s*([^\]]+)\]|[（(]({_INLINE_LABEL_PATTERN})[）)])",
    re.IGNORECASE,
)


def normalize_audio_effect(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {"preset": value}
    preset = str(raw.get("preset") or "none").strip().lower()
    if preset not in EFFECT_PRESETS:
        raise ValueError(f"未知空间声效：{preset}")
    try:
        mix = float(raw.get("mix", 0.35))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("空间声效干湿比必须是 0 到 1 的数字。") from exc
    if not np.isfinite(mix) or not 0 <= mix <= 1:
        raise ValueError("空间声效干湿比必须在 0 到 1 之间。")
    return {"preset": preset, "mix": mix, "label": EFFECT_PRESETS[preset]["label"]}


def _moving_average(audio: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or audio.size == 0:
        return audio.copy()
    padded = np.pad(audio, (window - 1, 0), mode="edge")
    cumulative = np.cumsum(np.insert(padded.astype(np.float64), 0, 0.0))
    return ((cumulative[window:] - cumulative[:-window]) / window).astype(np.float32)


def _lowpass(audio: np.ndarray, sample_rate: int, cutoff_hz: float) -> np.ndarray:
    cutoff = max(80.0, min(float(cutoff_hz), sample_rate * 0.45))
    return _moving_average(audio, max(1, int(round(sample_rate / cutoff))))


def _highpass(audio: np.ndarray, sample_rate: int, cutoff_hz: float) -> np.ndarray:
    cutoff = max(40.0, min(float(cutoff_hz), sample_rate * 0.40))
    return audio - _moving_average(audio, max(2, int(round(sample_rate / cutoff))))


def _effect_wet(audio: np.ndarray, sample_rate: int, preset: dict[str, Any]) -> np.ndarray:
    wet = audio.copy()
    if preset.get("highpass_hz"):
        wet = _highpass(wet, sample_rate, float(preset["highpass_hz"]))
    if preset.get("lowpass_hz"):
        wet = _lowpass(wet, sample_rate, float(preset["lowpass_hz"]))
    if preset.get("pre_emphasis"):
        amount = float(preset["pre_emphasis"])
        emphasized = wet.copy()
        if emphasized.size > 1:
            emphasized[1:] = wet[1:] - amount * wet[:-1]
        wet = emphasized
    if preset.get("ring_hz") and wet.size:
        phase = np.arange(wet.size, dtype=np.float32) / np.float32(sample_rate)
        carrier = .62 + .38 * np.sin(2.0 * np.pi * float(preset["ring_hz"]) * phase)
        wet = wet * carrier.astype(np.float32, copy=False)
    if preset.get("drive"):
        drive = max(1.0, float(preset["drive"]))
        wet = np.tanh(wet * drive) / np.tanh(drive)

    taps = preset.get("taps") or ()
    if not taps:
        return wet.astype(np.float32, copy=False)
    tail = max(int(round(delay_ms * sample_rate / 1000)) for delay_ms, _gain in taps)
    spatial = np.zeros(wet.size + tail, dtype=np.float32)
    spatial[: wet.size] = wet
    for delay_ms, gain in taps:
        delay = int(round(delay_ms * sample_rate / 1000))
        spatial[delay : delay + wet.size] += wet * float(gain)
    return spatial


def apply_audio_effect(audio: np.ndarray, sample_rate: int, value: Any) -> np.ndarray:
    effect = normalize_audio_effect(value)
    dry = np.asarray(audio, dtype=np.float32).reshape(-1)
    preset = EFFECT_PRESETS[effect["preset"]]
    has_processing = bool(
        preset.get("taps") or preset.get("highpass_hz") or preset.get("lowpass_hz")
        or preset.get("pre_emphasis") or preset.get("ring_hz") or preset.get("drive")
    )
    if not has_processing or effect["mix"] <= 0 or dry.size == 0:
        return dry.copy()
    wet = _effect_wet(dry, sample_rate, preset)
    extended_dry = np.pad(dry, (0, max(0, wet.size - dry.size)))
    result = extended_dry * (1.0 - effect["mix"]) + wet * effect["mix"]
    peak = float(np.max(np.abs(result))) if result.size else 0.0
    if peak > 0.98:
        result *= np.float32(0.98 / peak)
    return result.astype(np.float32, copy=False)


def extract_inline_audio_effect(text: str, configured: Any = None) -> tuple[str, dict[str, Any]]:
    """Remove a known T8 spatial tag and return the selected post effect."""
    detected: str | None = None

    def remove(match: re.Match[str]) -> str:
        nonlocal detected
        label = str(match.group(1) or match.group(2) or "").strip()
        candidate = label.lower().replace(" ", "_")
        if candidate in EFFECT_PRESETS:
            detected = candidate
        elif label in _LABEL_TO_PRESET:
            detected = _LABEL_TO_PRESET[label]
        return ""

    cleaned = _INLINE_EFFECT_RE.sub(remove, str(text or ""))
    selected = normalize_audio_effect(configured)
    if detected and selected["preset"] == "none":
        selected = normalize_audio_effect({"preset": detected, "mix": selected["mix"]})
    return cleaned.strip(), selected
