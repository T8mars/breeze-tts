from __future__ import annotations

import base64
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from t8_runtime import confucius, settings_store, transcription
from t8_runtime.runtime_manager import RuntimeManager
from t8_runtime.server import create_app


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "userdata"))


def model_fixture(root: Path, engine: str) -> Path:
    root.mkdir(parents=True)
    files = transcription._BUNDLED_LARGE_FILES if engine == "whisper" else confucius.MODEL_FILES
    for name in files:
        (root / name).write_bytes(b"fixture")
    if engine == "confucius":
        vad = root / "FireRedVAD-ONNX"
        vad.mkdir()
        for name in confucius.VAD_FILES:
            (vad / name).write_bytes(b"fixture")
    return root


@pytest.mark.parametrize("engine", ["whisper", "confucius"])
def test_custom_directories_persist_validate_and_reset(tmp_path, engine):
    directory = model_fixture(tmp_path / "custom model with spaces", engine)
    with TestClient(create_app(tmp_path / "missing-tts"), base_url="http://127.0.0.1") as client:
        result = client.post("/api/settings/transcription-directory", json={"engine": engine, "path": str(directory)})
        assert result.status_code == 200
        assert Path(result.json()[f"{engine}_model_directory"]) == directory
        assert settings_store.load_settings()[f"{engine}_model_dir"] == str(directory)
        invalid = client.post("/api/settings/transcription-directory", json={"engine": engine, "path": str(tmp_path / "invalid")})
        assert invalid.status_code == 400
        assert settings_store.load_settings()[f"{engine}_model_dir"] == str(directory)
        result = client.post("/api/settings/transcription-directory", json={"engine": engine, "path": ""})
        assert result.status_code == 200
        assert f"{engine}_model_dir" not in settings_store.load_settings()


def test_invalid_custom_whisper_never_silently_downloads(tmp_path):
    settings_store.update_settings(whisper_model_dir=tmp_path / "missing")
    with pytest.raises(ValueError, match="目录不完整"):
        transcription.resolve_whisper_model("large-v3")


def test_valid_custom_whisper_resolves_without_download(tmp_path):
    directory = model_fixture(tmp_path / "Whisper custom", "whisper")
    settings_store.update_settings(whisper_model_dir=directory)
    assert transcription.resolve_whisper_model("large-v3") == (str(directory), None, False)


@pytest.mark.parametrize("language, expected", [(None, "Auto"), ("zh-CN", "Chinese"), ("en", "English"), ("yue", "Cantonese"), ("Japanese", "Japanese")])
def test_language_mapping(language, expected):
    assert confucius.recognition_language(language) == expected


def test_invalid_language():
    with pytest.raises(ValueError, match="不支持"):
        confucius.recognition_language("xxx")


def test_context_limit_checked_before_worker_start(tmp_path):
    with pytest.raises(ValueError, match="8192"):
        confucius.transcribe(tmp_path / "unused", context="x" * 8193)


def test_confucius_result_does_not_invent_subtitles(monkeypatch, tmp_path):
    audio = tmp_path / "source.wav"
    sf.write(audio, np.zeros(16000, dtype=np.float32), 16000)
    original = audio.read_bytes()
    monkeypatch.setattr(confucius, "transcribe", lambda *_args, **_kwargs: {
        "text": "草稿文字", "language": "Chinese", "segments": [{"start_sample": 0, "end_sample": 16000}],
        "truncated": True, "quality_status": "requires_review", "elapsed_ms": 123,
    })
    monkeypatch.setattr(confucius, "availability", lambda: {"bundled_model": False})
    result = transcription.transcribe_audio(audio, engine="confucius")
    assert result["text"] == "草稿文字"
    assert result["srt"] == "" and result["segments"] == []
    assert result["timestamps_available"] is False
    assert result["recognition_boundaries"]
    assert result["truncated"] and result["draft_only"]
    assert result["language_probability"] is None
    assert audio.read_bytes() == original


@pytest.mark.parametrize("fail", [False, True])
def test_worker_always_closes_after_request(monkeypatch, tmp_path, fail):
    audio = tmp_path / "source.wav"
    sf.write(audio, np.zeros(16000, dtype=np.float32), 16000)
    calls = []

    class Manager:
        def transcribe(self, pcm, options, config):
            calls.append((options, config))
            assert len(pcm) == 16000 * 4
            if fail:
                raise RuntimeError("worker failed")
            return {"text": "ok"}

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(confucius, "availability", lambda: {"available": True})
    monkeypatch.setattr(confucius, "_worker_manager", lambda _models: Manager())
    if fail:
        with pytest.raises(RuntimeError, match="worker failed"):
            confucius.transcribe(audio)
    else:
        assert confucius.transcribe(audio, language="zh", hotwords="术语")["text"] == "ok"
        assert calls[0][0]["language"] == "Chinese"
        assert calls[0][0]["hotwords"] == "术语"
    assert calls[0][1]["gpu_layers"] == -1
    assert calls[-1] == "closed"


def test_auxiliary_operation_excludes_generation_and_releases_lock(monkeypatch, tmp_path):
    runtime = RuntimeManager(tmp_path / "missing")
    calls = []
    monkeypatch.setattr(runtime, "_unload_state", lambda: calls.append("unloaded"))
    with pytest.raises(ValueError):
        with runtime.auxiliary_operation():
            assert not runtime._generation_lock.acquire(blocking=False)
            raise ValueError("request failed")
    assert calls == ["unloaded"]
    assert runtime._generation_lock.acquire(blocking=False)
    try:
        with pytest.raises(RuntimeError, match="正在生成或转录"):
            with runtime.auxiliary_operation():
                pass
    finally:
        runtime._generation_lock.release()


@pytest.mark.parametrize("engine", ["whisper", "confucius"])
def test_endpoint_dispatches_and_deletes_temporary_audio(monkeypatch, tmp_path, engine):
    audio = tmp_path / "source.wav"
    sf.write(audio, np.zeros(8000, dtype=np.float32), 8000)
    seen = []

    def fake_transcribe(path, **options):
        assert path.is_file()
        seen.append((path, options))
        return {"text": "ok", "engine": options["engine"]}

    monkeypatch.setattr("t8_runtime.server.transcribe_audio", fake_transcribe)
    with TestClient(create_app(tmp_path / "missing-tts"), base_url="http://127.0.0.1") as client:
        result = client.post("/api/tools/transcribe", json={"engine": engine,
            "reference_filename": "source.wav", "reference_audio_base64": base64.b64encode(audio.read_bytes()).decode(),
            "hotwords": "test", "context": "context", "language": "en"})
        assert result.status_code == 200
        assert result.json()["engine"] == engine
        assert seen[0][1]["hotwords"] == "test"
        assert not seen[0][0].exists()
        invalid = client.post("/api/tools/transcribe", json={"engine": "arbitrary", "reference_filename": "source.wav", "reference_audio_base64": ""})
        assert invalid.status_code == 422
