from __future__ import annotations

from pathlib import Path

from t8_runtime import transcription


def _write_bundled_small(root: Path) -> Path:
    target = root / "models" / "faster-whisper-small"
    target.mkdir(parents=True)
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        (target / name).write_bytes(b"test")
    return target


def test_default_small_uses_bundled_model_without_network(monkeypatch, tmp_path):
    bundled = _write_bundled_small(tmp_path / "project")
    monkeypatch.setattr(transcription, "project_root", lambda: tmp_path / "project")
    monkeypatch.setattr(transcription, "user_data_dir", lambda: tmp_path / "data")

    source, cache, is_bundled = transcription.resolve_whisper_model("small")

    assert Path(source) == bundled
    assert cache is None
    assert is_bundled is True


def test_other_whisper_sizes_use_dedicated_user_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(transcription, "project_root", lambda: tmp_path / "project")
    monkeypatch.setattr(transcription, "user_data_dir", lambda: tmp_path / "data")

    source, cache, is_bundled = transcription.resolve_whisper_model("base")

    assert source == "base"
    assert cache == tmp_path / "data" / "models" / "whisper"
    assert cache.is_dir()
    assert is_bundled is False
