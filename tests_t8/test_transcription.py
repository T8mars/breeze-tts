from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from t8_runtime import transcription


def _write_bundled_large(root: Path) -> Path:
    target = root / "models" / "faster-whisper-large-v3"
    target.mkdir(parents=True)
    for name in (
        "config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"
    ):
        (target / name).write_bytes(b"test")
    return target


def test_default_large_uses_bundled_model_without_network(monkeypatch, tmp_path):
    bundled = _write_bundled_large(tmp_path / "project")
    monkeypatch.setattr(transcription, "project_root", lambda: tmp_path / "project")
    monkeypatch.setattr(transcription, "user_data_dir", lambda: tmp_path / "data")

    source, cache, is_bundled = transcription.resolve_whisper_model("large-v3")

    assert Path(source) == bundled
    assert cache is None
    assert is_bundled is True


def test_legacy_whisper_sizes_are_not_reported_as_bundled(monkeypatch, tmp_path):
    monkeypatch.setattr(transcription, "project_root", lambda: tmp_path / "project")
    monkeypatch.setattr(transcription, "user_data_dir", lambda: tmp_path / "data")

    source, cache, is_bundled = transcription.resolve_whisper_model("small")

    assert source == "small"
    assert cache == tmp_path / "data" / "models" / "whisper"
    assert cache.is_dir()
    assert is_bundled is False


def test_reference_audio_quality_flags_short_quiet_recording(tmp_path):
    path = tmp_path / "quiet.wav"
    sf.write(path, np.full(16_000, 0.001, dtype=np.float32), 16_000)

    report = transcription.analyze_reference_audio(path)

    assert report["duration_seconds"] == 1.0
    assert report["rms_dbfs"] < -36
    assert any("短于 3 秒" in warning for warning in report["warnings"])
    assert any("音量过低" in warning for warning in report["warnings"])
    assert "混响" in report["recommended"]


def test_reference_audio_quality_accepts_clean_dry_level(tmp_path):
    path = tmp_path / "clean.wav"
    time = np.arange(16_000 * 5, dtype=np.float32) / 16_000
    sf.write(path, 0.2 * np.sin(2 * np.pi * 220 * time), 16_000)

    report = transcription.analyze_reference_audio(path)

    assert report["warnings"] == []
    assert report["channels"] == 1
