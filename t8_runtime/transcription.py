from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
from typing import Any

from .config import project_root, user_data_dir


_BUNDLED_SMALL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")


def bundled_whisper_model_dir() -> Path:
    return project_root() / "models" / "faster-whisper-small"


def bundled_whisper_small_available() -> bool:
    root = bundled_whisper_model_dir()
    return all((root / name).is_file() and (root / name).stat().st_size > 0 for name in _BUNDLED_SMALL_FILES)


def resolve_whisper_model(model_size: str) -> tuple[str, Path | None, bool]:
    if model_size == "small" and bundled_whisper_small_available():
        return str(bundled_whisper_model_dir()), None, True
    cache = user_data_dir() / "models" / "whisper"
    cache.mkdir(parents=True, exist_ok=True)
    return model_size, cache, False


def whisper_available() -> bool:
    return find_spec("faster_whisper") is not None


def transcribe_audio(
    path: Path,
    *,
    model_size: str = "small",
    language: str | None = None,
) -> dict[str, Any]:
    if not whisper_available():
        raise RuntimeError(
            "内置 faster-whisper 组件缺失。请重新下载完整整合包，或运行 packaging/install_whisper.ps1 修复。"
        )
    import torch
    from faster_whisper import WhisperModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    model_source, download_root, bundled = resolve_whisper_model(model_size)
    model_options: dict[str, Any] = {"device": device, "compute_type": compute_type}
    if download_root is not None:
        model_options["download_root"] = str(download_root)
    model = WhisperModel(model_source, **model_options)
    segments, info = model.transcribe(
        str(path), language=language or None, vad_filter=True, beam_size=5
    )
    items = []
    srt_blocks = []
    for index, segment in enumerate(segments, start=1):
        start_ms = int(round(float(segment.start) * 1000))
        end_ms = int(round(float(segment.end) * 1000))
        text = str(segment.text).strip()
        items.append({"index": index, "start_ms": start_ms, "end_ms": end_ms, "text": text})
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
    }


def _srt_timestamp(milliseconds: int) -> str:
    milliseconds = max(0, int(milliseconds))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
