from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
from typing import Any


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
            "未安装可选的 faster-whisper。运行 packaging/install_whisper.ps1 后重启应用即可启用。"
        )
    import torch
    from faster_whisper import WhisperModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
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
    }


def _srt_timestamp(milliseconds: int) -> str:
    milliseconds = max(0, int(milliseconds))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
