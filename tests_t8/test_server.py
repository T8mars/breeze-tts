from __future__ import annotations

from pathlib import Path
import base64

import numpy as np
import soundfile as sf

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from t8_runtime.server import _decode_reference, create_app


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


def test_capabilities_are_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app(tmp_path / "missing-model"), base_url="http://127.0.0.1") as client:
        capabilities = client.get("/api/capabilities").json()
        assert capabilities["long_text"] is True
        assert capabilities["batch"] is True
        assert capabilities["srt"] is True
        assert isinstance(capabilities["whisper"], bool)
        assert isinstance(capabilities["whisper_small_bundled"], bool)
