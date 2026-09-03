from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from .audio_effects import apply_audio_effect, normalize_audio_effect
from .config import SAMPLE_RATE, output_dir


def _read_mono(path: Path) -> np.ndarray:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    if int(sample_rate) != SAMPLE_RATE:
        raise ValueError(f"批量输出采样率不一致：{sample_rate}")
    return np.mean(audio, axis=1, dtype=np.float32)


def merge_batch_outputs(
    results: list[dict[str, Any]], *, timeline: bool, timing_policy: str = "preserve"
) -> tuple[Path, dict]:
    if not results:
        raise ValueError("没有可合并的批量输出。")
    clips = []
    for result in results:
        metadata = result["metadata"]
        path = Path(str(metadata.get("dry_output") or metadata["output"])).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"批量输出不存在：{path}")
        effect = normalize_audio_effect(result.get("audio_effect"))
        clips.append((result, apply_audio_effect(_read_mono(path), SAMPLE_RATE, effect), effect))
    if timeline:
        placements = []
        total_samples = 0
        cursor = 0
        timing_warnings: list[str] = []
        if timing_policy not in {"preserve", "overlay", "strict"}:
            raise ValueError("时间策略必须是 preserve、overlay 或 strict。")
        for result, audio, _effect in clips:
            subtitle = result.get("subtitle") or {}
            requested_start = max(0, int(round(int(subtitle.get("start_ms", 0)) * SAMPLE_RATE / 1000)))
            requested_end = max(
                requested_start + 1,
                int(round(int(subtitle.get("end_ms", subtitle.get("start_ms", 0))) * SAMPLE_RATE / 1000)),
            )
            start = requested_start if timing_policy in {"overlay", "strict"} else max(requested_start, cursor)
            if timing_policy == "preserve" and start != requested_start:
                timing_warnings.append(
                    f"第 {int(result.get('index', 0)) + 1} 句为保留完整语音已顺延 "
                    f"{(start - requested_start) * 1000 / SAMPLE_RATE:.0f} ms。"
                )
            if timing_policy == "strict":
                slot = requested_end - requested_start
                if audio.size > slot:
                    timing_warnings.append(
                        f"第 {int(result.get('index', 0)) + 1} 句超过严格槽位，尾部被裁剪。"
                    )
                    audio = audio[:slot]
                elif audio.size < slot:
                    audio = np.pad(audio, (0, slot - audio.size))
            end = start + int(audio.size)
            placements.append((start, end, audio))
            total_samples = max(total_samples, end)
            cursor = max(cursor, end)
        merged = np.zeros(total_samples, dtype=np.float32)
        for start, end, audio in placements:
            merged[start:end] += audio
        np.clip(merged, -1.0, 1.0, out=merged)
        mode = "timeline"
    else:
        pause = np.zeros(int(SAMPLE_RATE * 0.2), dtype=np.float32)
        pieces = []
        for index, (_result, audio, _effect) in enumerate(clips):
            if index:
                pieces.append(pause)
            pieces.append(audio)
        merged = np.concatenate(pieces) if pieces else np.empty(0, dtype=np.float32)
        mode = "batch"
    destination = output_dir()
    destination.mkdir(parents=True, exist_ok=True)
    request_id = uuid.uuid4().hex[:8]
    target = destination / f"breeze_{mode}_{time.strftime('%Y%m%d_%H%M%S')}_{request_id}.wav"
    sf.write(target, merged, SAMPLE_RATE, subtype="PCM_16")
    metadata = {
        "mode": mode,
        "sample_rate": SAMPLE_RATE,
        "samples": int(merged.size),
        "duration_seconds": float(merged.size) / SAMPLE_RATE,
        "item_count": len(results),
        "output": str(target),
        "items": results,
        "timing_policy": timing_policy if timeline else None,
        "timing_warnings": timing_warnings if timeline else [],
        "audio_effects": [effect for _result, _audio, effect in clips],
    }
    target.with_suffix(".json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target, metadata
