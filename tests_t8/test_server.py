from __future__ import annotations

from pathlib import Path
import base64

import numpy as np
import soundfile as sf

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from t8_runtime.server import _decode_reference, create_app


class _SingleGenerationRuntime:
    def __init__(self, model_dir: Path, output_root: Path) -> None:
        self.model_dir = model_dir
        self.output_root = output_root
        self.received_stream_callbacks: list[bool] = []

    def status(self) -> dict[str, object]:
        return {"loaded": True, "model_dir": str(self.model_dir)}

    def generate(self, request, *, cancel_event=None, on_chunk=None):
        self.received_stream_callbacks.append(on_chunk is not None)
        audio = np.full(1_200, 0.1, dtype=np.float32)
        if on_chunk is not None:
            on_chunk(audio)
        self.output_root.mkdir(parents=True, exist_ok=True)
        target = self.output_root / "single.wav"
        sf.write(target, audio, 24_000, subtype="PCM_16")
        return target, {"output": str(target), "sample_rate": 24_000}


@pytest.mark.parametrize("requested_streaming", [None, False, True])
def test_single_generation_streaming_is_opt_in(monkeypatch, tmp_path, requested_streaming):
    data_root = tmp_path / "data"
    output_root = tmp_path / "outputs"
    model_root = tmp_path / "models"
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(data_root))
    monkeypatch.setenv("T8_BREEZE_OUTPUT_DIR", str(output_root))
    runtime = _SingleGenerationRuntime(model_root, output_root)
    monkeypatch.setattr("t8_runtime.server.RuntimeManager", lambda _model_dir: runtime)
    monkeypatch.setattr(
        "t8_runtime.server.validate_model_dir",
        lambda *_args, **_kwargs: {"valid": True, "license_accepted": True},
    )
    payload = {"mode": "design", "text": "测试", "instruction": "自然"}
    if requested_streaming is not None:
        payload["stream_audio"] = requested_streaming
    expected_streaming = requested_streaming is True

    with TestClient(create_app(model_root), base_url="http://127.0.0.1") as client:
        with client.websocket_connect(
            "/ws/generate", headers={"host": "127.0.0.1"}
        ) as websocket:
            websocket.send_json(payload)
            start = websocket.receive_json()
            assert start["type"] == "start"
            assert start["stream_audio"] is expected_streaming
            if expected_streaming:
                assert websocket.receive_bytes()
            complete = websocket.receive_json()
            assert complete["type"] == "complete"
            assert complete["metadata"]["stream_audio"] is expected_streaming

    assert runtime.received_stream_callbacks == [expected_streaming]


def test_health_and_license(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    app = create_app(tmp_path / "missing-model")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        license_response = client.get("/api/license")
        assert license_response.status_code == 200
        assert "NON-COMMERCIAL" in license_response.text
        assert "Version 1.1" in license_response.text


def test_path_traversal_output_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app(tmp_path / "missing-model"), base_url="http://127.0.0.1") as client:
        assert client.get("/api/outputs/..%2Fsecret.wav").status_code == 404


def test_dns_rebinding_host_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app(tmp_path / "missing-model"), base_url="http://attacker.example") as client:
        assert client.get("/api/health").status_code == 400


def test_cross_origin_websocket_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app(tmp_path / "missing-model"), base_url="http://127.0.0.1") as client:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(
                "/ws/generate",
                headers={"host": "127.0.0.1", "origin": "http://attacker.example"},
            ):
                pass
        assert (
            getattr(rejected.value, "code", None) == 1008
            or getattr(rejected.value, "status_code", None) == 403
        )


def test_settings_output_directory_is_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    output = tmp_path / "custom-output"
    with TestClient(create_app(tmp_path / "missing-model"), base_url="http://127.0.0.1") as client:
        response = client.post("/api/settings/output-directory", json={"path": str(output)})
        assert response.status_code == 200
        assert Path(response.json()["output_directory"]) == output.resolve()
        assert client.get("/api/settings").json()["output_directory"] == str(output.resolve())


def test_reference_rejects_unsupported_extension(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(ValueError, match="WAV"):
        _decode_reference(
            {
                "reference_filename": "voice.m4a",
                "reference_audio_base64": base64.b64encode(b"not audio").decode("ascii"),
            }
        )


def test_reference_duration_is_checked_before_generation(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    source = tmp_path / "long.wav"
    sf.write(source, np.zeros(61 * 8_000, dtype=np.float32), 8_000)
    with pytest.raises(ValueError, match="60"):
        _decode_reference(
            {
                "reference_filename": "voice.wav",
                "reference_audio_base64": base64.b64encode(source.read_bytes()).decode("ascii"),
            }
        )
    cache = tmp_path / "data" / "cache" / "references"
    assert not list(cache.glob("ref_*"))


def test_voice_library_and_content_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app(tmp_path / "missing-model"), base_url="http://127.0.0.1") as client:
        created = client.post(
            "/api/voices",
            json={"name": "旁白", "mode": "design", "instruction": "温柔自然"},
        )
        assert created.status_code == 200
        voice_id = created.json()["id"]
        assert client.get("/api/voices").json()["voices"][0]["name"] == "旁白"
        parsed = client.post(
            "/api/tools/parse-multi-role", json={"text": "[旁白] 开始\n角色：你好"}
        )
        assert parsed.status_code == 200
        assert len(parsed.json()["segments"]) == 2
        srt = client.post(
            "/api/tools/parse-srt",
            json={"text": "1\n00:00:00,000 --> 00:00:01,000\n测试"},
        )
        assert srt.status_code == 200
        assert srt.json()["segments"][0]["end_ms"] == 1000
        assert client.delete(f"/api/voices/{voice_id}").json()["deleted"] is True


def test_voice_library_serves_its_private_reference_for_local_preview(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    reference = tmp_path / "role.wav"
    sf.write(reference, np.zeros(8_000, dtype=np.float32), 8_000)
    payload = base64.b64encode(reference.read_bytes()).decode("ascii")
    with TestClient(create_app(tmp_path / "missing-model"), base_url="http://127.0.0.1") as client:
        created = client.post(
            "/api/voices",
            json={
                "name": "参考音色",
                "mode": "clone",
                "reference_text": "这是一段参考音频。",
                "reference_transcript_verified": True,
                "reference_filename": "role.wav",
                "reference_audio_base64": payload,
            },
        )
        assert created.status_code == 200
        voice_id = created.json()["id"]
        preview = client.get(f"/api/voices/{voice_id}/reference")
        assert preview.status_code == 200
        assert preview.headers["content-type"].startswith("audio/")
        assert preview.content.startswith(b"RIFF")
        assert client.get("/api/voices/missing/reference").status_code == 404


def test_clone_voice_rejects_unverified_reference_transcript(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    reference = tmp_path / "role.wav"
    sf.write(reference, np.zeros(8_000, dtype=np.float32), 8_000)
    payload = base64.b64encode(reference.read_bytes()).decode("ascii")
    with TestClient(create_app(tmp_path / "missing-model"), base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/voices",
            json={
                "name": "未核对音色",
                "mode": "clone",
                "reference_text": "Whisper 草稿",
                "reference_filename": "role.wav",
                "reference_audio_base64": payload,
            },
        )
        assert response.status_code == 400
        assert "逐字" in response.json()["detail"]


def test_raw_clone_websocket_rejects_unverified_reference_before_generation(
    monkeypatch, tmp_path
):
    data_root = tmp_path / "data"
    output_root = tmp_path / "outputs"
    model_root = tmp_path / "models"
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(data_root))
    monkeypatch.setenv("T8_BREEZE_OUTPUT_DIR", str(output_root))
    runtime = _SingleGenerationRuntime(model_root, output_root)
    monkeypatch.setattr("t8_runtime.server.RuntimeManager", lambda _model_dir: runtime)
    reference = tmp_path / "role.wav"
    sf.write(reference, np.zeros(8_000, dtype=np.float32), 8_000)
    payload = base64.b64encode(reference.read_bytes()).decode("ascii")

    with TestClient(create_app(model_root), base_url="http://127.0.0.1") as client:
        with client.websocket_connect(
            "/ws/generate", headers={"host": "127.0.0.1"}
        ) as websocket:
            websocket.send_json({
                "mode": "clone",
                "text": "测试",
                "reference_text": "未经核对的草稿",
                "reference_filename": "role.wav",
                "reference_audio_base64": payload,
            })
            error = websocket.receive_json()
            assert error["type"] == "error"
            assert "逐字修正" in error["message"]

    assert runtime.received_stream_callbacks == []


def test_capabilities_are_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app(tmp_path / "missing-model"), base_url="http://127.0.0.1") as client:
        capabilities = client.get("/api/capabilities").json()
        assert capabilities["long_text"] is True
        assert capabilities["batch"] is True
        assert capabilities["srt"] is True
        assert isinstance(capabilities["whisper"], bool)
        assert isinstance(capabilities["whisper_large_bundled"], bool)
