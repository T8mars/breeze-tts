from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
import threading
from typing import Any

import numpy as np
import soundfile as sf

from .config import project_root, user_data_dir


_BUNDLED_LARGE_FILES = (
    "config.json",
    "model.bin",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocabulary.json",
)
_MODEL_LOCK = threading.RLock()
_MODEL_CACHE: dict[tuple[str, str, str], Any] = {}


def bundled_whisper_model_dir() -> Path:
    packaged = project_root() / "models" / "faster-whisper-large-v3"
    if all((packaged / name).is_file() for name in _BUNDLED_LARGE_FILES):
        return packaged
    # Source/development builds keep large weights out of the tracked models
    # tree; build_runtime downloads the exact same pinned snapshot here.
    return project_root() / ".runtime" / "whisper-models" / "faster-whisper-large-v3"


def bundled_whisper_large_available() -> bool:
    root = bundled_whisper_model_dir()
    return all((root / name).is_file() and (root / name).stat().st_size > 0 for name in _BUNDLED_LARGE_FILES)


def resolve_whisper_model(model_size: str) -> tuple[str, Path | None, bool]:
    if model_size == "large-v3" and bundled_whisper_large_available():
        return str(bundled_whisper_model_dir()), None, True
    cache = user_data_dir() / "models" / "whisper"
    cache.mkdir(parents=True, exist_ok=True)
    return model_size, cache, False


def whisper_available() -> bool:
    return find_spec("faster_whisper") is not None


def analyze_reference_audio(path: Path) -> dict[str, Any]:
    """Return conservative recording-health hints without altering the audio.

    These measurements intentionally avoid claiming that room echo can be
    detected reliably.  They catch common, objective problems (silence,
    clipping, very low level and DC offset) and remind the user that Whisper
    text is not an audio-cleaning step.
    """
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    if sample_rate <= 0 or audio.shape[0] <= 0:
        raise ValueError("参考音频为空或无法读取。")
    channels = int(audio.shape[1])
    finite = bool(np.isfinite(audio).all())
    mono = np.nan_to_num(np.mean(audio, axis=1, dtype=np.float32))
    duration = float(mono.size) / float(sample_rate)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(mono, dtype=np.float64)))) if mono.size else 0.0
    rms_dbfs = float(20.0 * np.log10(max(rms, 1e-9)))
    clipping_ratio = float(np.mean(np.abs(mono) >= 0.995)) if mono.size else 0.0
    dc_offset = float(abs(np.mean(mono, dtype=np.float64))) if mono.size else 0.0
    frame_size = max(1, int(round(sample_rate * 0.02)))
    padded_size = ((mono.size + frame_size - 1) // frame_size) * frame_size
    framed = np.pad(mono, (0, padded_size - mono.size)).reshape(-1, frame_size)
    frame_rms = np.sqrt(np.mean(np.square(framed, dtype=np.float64), axis=1))
    silence_ratio = float(np.mean(frame_rms < 10 ** (-50 / 20)))
    warnings: list[str] = []
    if not finite:
        warnings.append("音频包含无效样本，请重新导出。")
    if duration < 3:
        warnings.append("参考音频短于 3 秒，克隆音色可能不稳定。")
    elif duration > 20:
        warnings.append("参考音频较长，建议裁成 5–15 秒的单人干声。")
    if rms_dbfs < -36:
        warnings.append("整体音量过低，底噪会被相对放大。")
    if clipping_ratio > 0.001:
        warnings.append("检测到明显削波失真。")
    if silence_ratio > 0.55:
        warnings.append("静音占比较高，建议裁掉长空白。")
    if dc_offset > 0.02:
        warnings.append("检测到明显直流偏移，请先清理录音。")
    return {
        "sample_rate": int(sample_rate),
        "channels": channels,
        "duration_seconds": duration,
        "peak": peak,
        "rms_dbfs": rms_dbfs,
        "clipping_ratio": clipping_ratio,
        "silence_ratio": silence_ratio,
        "dc_offset": dc_offset,
        "warnings": warnings,
        "recommended": "建议使用 5–15 秒、单人、无音乐、无混响/回声的干声；该检查不能可靠识别所有房间混响。",
    }


def transcribe_audio(
    path: Path,
    *,
    model_size: str = "large-v3",
    language: str | None = None,
) -> dict[str, Any]:
    if not whisper_available():
        raise RuntimeError(
            "内置 faster-whisper 组件缺失。请重新下载完整整合包，或运行 packaging/install_whisper.ps1 修复。"
        )
    import torch
    from faster_whisper import WhisperModel

    if model_size != "large-v3":
        raise ValueError("此版本只允许使用 Whisper Large-v3，避免 Small 模型误识别逐字稿。")
    audio_quality = analyze_reference_audio(path)
    device = "cpu"
    compute_type = "int8"
    if torch.cuda.is_available():
        try:
            free_bytes, _total_bytes = torch.cuda.mem_get_info()
        except Exception:
            free_bytes = 0
        if int(free_bytes) >= 6 * 1024**3:
            device = "cuda"
            compute_type = "float16"
    model_source, download_root, bundled = resolve_whisper_model(model_size)
    model_options: dict[str, Any] = {"device": device, "compute_type": compute_type}
    if download_root is not None:
        model_options["download_root"] = str(download_root)
    cache_key = (str(model_source), device, compute_type)
    with _MODEL_LOCK:
        model = _MODEL_CACHE.get(cache_key)
        if model is None:
            model = WhisperModel(model_source, **model_options)
            _MODEL_CACHE.clear()
            _MODEL_CACHE[cache_key] = model
        segments, info = model.transcribe(
            str(path),
            language=language or None,
            vad_filter=False,
            beam_size=5,
            temperature=0,
            condition_on_previous_text=False,
            word_timestamps=True,
        )
    items = []
    srt_blocks = []
    for index, segment in enumerate(segments, start=1):
        start_ms = int(round(float(segment.start) * 1000))
        end_ms = int(round(float(segment.end) * 1000))
        text = str(segment.text).strip()
        items.append({
            "index": index,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": text,
            "avg_logprob": getattr(segment, "avg_logprob", None),
            "no_speech_probability": getattr(segment, "no_speech_prob", None),
            "compression_ratio": getattr(segment, "compression_ratio", None),
        })
        srt_blocks.append(
            f"{index}\n{_srt_timestamp(start_ms)} --> {_srt_timestamp(end_ms)}\n{text}"
        )
    return {
        "language": getattr(info, "language", language),
        "language_probability": getattr(info, "language_probability", None),
        "segments": items,
        "srt": "\n\n".join(srt_blocks) + ("\n" if srt_blocks else ""),
        "model_size": model_size,
        "device": device,
        "bundled_model": bundled,
        "draft_only": True,
        "vad_filter": False,
        "duration_seconds": getattr(info, "duration", None),
        "audio_quality": audio_quality,
        "warning": "Whisper 结果仅是草稿；用于声音克隆前必须与参考音频逐字核对。",
    }


def _srt_timestamp(milliseconds: int) -> str:
    milliseconds = max(0, int(milliseconds))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
