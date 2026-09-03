from __future__ import annotations

import json
import hashlib
import threading
import time
from pathlib import Path

import pytest
import numpy as np
import soundfile as sf

from t8_runtime.config import MODEL_REVISION
from t8_runtime.batch_audio import merge_batch_outputs
from t8_runtime.model_store import (
    ensure_model_integrity,
    ModelDownloadManager,
    load_manifest,
    record_license_acceptance,
    validate_model_dir,
)
from t8_runtime.runtime_manager import GenerationRequest, RuntimeManager
from t8_runtime.script_tools import parse_multi_role_script, parse_srt
from t8_runtime.text_processing import split_text_for_model


ROOT = Path(__file__).resolve().parents[1]


def test_manifest_is_pinned_and_fully_hashed():
    manifest = load_manifest()
    assert manifest["revision"] == MODEL_REVISION
    assert len(manifest["files"]) == 17
    assert sum(item["size"] for item in manifest["files"]) == manifest["total_size"]
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])


@pytest.mark.model
def test_downloaded_model_full_hashes():
    model = ROOT / "models" / "Breeze-TTS-2"
    if not model.is_dir():
        pytest.skip("model not downloaded")
    report = validate_model_dir(model, verify_hashes=True)
    assert report["valid"], json.dumps(report, ensure_ascii=False)


@pytest.mark.parametrize("mode", ["design", "clone", "direction"])
def test_generation_request_modes(mode, tmp_path):
    reference = None
    ref_text = None
    if mode != "design":
        reference = tmp_path / "reference.wav"
        reference.write_bytes(b"test")
        ref_text = "reference"
    request = GenerationRequest(
        mode=mode,
        text="hello",
        instruction="calm",
        ref_audio_path=reference,
        ref_text=ref_text,
    )
    request.validate()


def test_clone_requires_reference():
    with pytest.raises(ValueError, match="参考音频"):
        GenerationRequest(mode="clone", text="hello").validate()


def test_generation_request_rejects_unbounded_text():
    with pytest.raises(ValueError, match="20000"):
        GenerationRequest(mode="design", text="x" * 20_001, instruction="calm").validate()


def test_model_download_requires_license_acceptance(tmp_path):
    with pytest.raises(PermissionError, match="许可证"):
        ModelDownloadManager().start(tmp_path / "model", accepted=False)


def test_license_marker_is_bound_to_pinned_revision(tmp_path):
    model = tmp_path / "model"
    record_license_acceptance(model)
    assert validate_model_dir(model)["license_accepted"] is True
    marker = model / ".t8-license-accepted.json"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["revision"] = "stale-revision"
    marker.write_text(json.dumps(payload), encoding="utf-8")
    assert validate_model_dir(model)["license_accepted"] is False


def test_desktop_security_contract():
    main = (ROOT / "desktop" / "src" / "main.js").read_text(encoding="utf-8")
    html = (ROOT / "desktop" / "src" / "index.html").read_text(encoding="utf-8")
    assert "contextIsolation: true" in main
    assert "nodeIntegration: false" in main
    assert "sandbox: true" in main
    assert "allowedOpenTarget" in main
    assert 'webContents.on("will-navigate"' in main
    assert '"127.0.0.1"' in main
    assert "Content-Security-Policy" in html
    assert "ws://127.0.0.1:*" in html
    assert "connect-src 'self' ws: wss:" not in html
    assert "unsafe-eval" not in html


def test_desktop_registers_visibility_handler_before_loading():
    main = (ROOT / "desktop" / "src" / "main.js").read_text(encoding="utf-8")
    ready_handler = 'mainWindow.once("ready-to-show"'
    load_call = "await mainWindow.loadURL(baseUrl)"
    assert main.index(ready_handler) < main.index(load_call)
    assert "!mainWindow.isVisible()" in main
    assert "mainWindow.show()" in main


def test_desktop_runtime_declares_websocket_transport():
    runtime_input = (ROOT / "requirements-desktop.in").read_text(encoding="utf-8").lower()
    runtime_lock = (ROOT / "requirements-desktop.lock.txt").read_text(encoding="utf-8").lower()
    verify_script = (ROOT / "packaging" / "verify_runtime.py").read_text(encoding="utf-8").lower()
    assert "websockets>=" in runtime_input
    assert "websockets==" in runtime_lock
    assert '"websockets"' in verify_script


def test_node_registry_metadata_preserves_host_scientific_stack():
    package_dir = ROOT / "comfyui-breeze-tts-T8"
    requirements = (package_dir / "requirements.txt").read_text(encoding="utf-8").lower()
    declared = {
        line.split("=", 1)[0].split(">", 1)[0].strip()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    protected = {"torch", "torchaudio", "torchvision", "transformers", "tokenizers", "numpy"}
    assert declared.isdisjoint(protected)
    pyproject = (package_dir / "pyproject.toml").read_text(encoding="utf-8").lower()
    dependency_block = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
    assert all(f'"{name}' not in dependency_block for name in protected)
    assert 'publisherid = "t8star"' in pyproject
    assert 'repository = "https://github.com/t8mars/comfyui-breeze-tts"' in pyproject
    assert "preinstall" not in pyproject
    assert not (package_dir / "install.py").exists()
    assert "accelerate" in declared


def test_runtime_refuses_unload_while_generation_is_locked(tmp_path):
    runtime = RuntimeManager(tmp_path / "model")
    runtime._generation_lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="正在生成"):
            runtime.unload()
    finally:
        runtime._generation_lock.release()


def test_runtime_allows_reconfirming_active_model_while_generation_is_locked(tmp_path):
    model = tmp_path / "model"
    runtime = RuntimeManager(model)
    runtime._generation_lock.acquire()
    try:
        runtime.select_model_dir(model)
        assert runtime.model_dir == model.resolve()
    finally:
        runtime._generation_lock.release()


def test_token_aware_long_text_splitter_preserves_text():
    class CharacterTokenizer:
        def __call__(self, text, **_kwargs):
            return {"input_ids": list(text)}

    text = "第一句话。第二句话很长，需要自动切分！Third sentence is here."
    parts = split_text_for_model(text, CharacterTokenizer(), max_tokens=12)
    assert len(parts) > 1
    assert all(len(part) <= 12 for part in parts)
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")


def test_token_aware_splitter_handles_huggingface_mapping_results():
    from collections.abc import Mapping

    class BatchEncodingLike(Mapping):
        def __init__(self, values):
            self.values = values

        def __getitem__(self, key):
            return self.values[key]

        def __iter__(self):
            return iter(self.values)

        def __len__(self):
            return len(self.values)

    class HuggingFaceTokenizerLike:
        def __call__(self, text, **_kwargs):
            return BatchEncodingLike({"input_ids": list(text)})

    parts = split_text_for_model("第一句。第二句。第三句。", HuggingFaceTokenizerLike(), max_tokens=6)
    assert len(parts) >= 2
    assert all(len(part) <= 6 for part in parts)


def test_srt_and_multi_role_parsers():
    srt = "1\n00:00:00,000 --> 00:00:01,250\n你好。\n\n2\n00:00:01,500 --> 00:00:03,000\nHello."
    subtitles = parse_srt(srt)
    assert subtitles == [
        {"index": 1, "start_ms": 0, "end_ms": 1250, "text": "你好。"},
        {"index": 2, "start_ms": 1500, "end_ms": 3000, "text": "Hello."},
    ]
    roles = parse_multi_role_script("[小蓝] 你好\n小明：早上好\n继续说")
    assert roles == [
        {"role": "小蓝", "text": "你好"},
        {"role": "小明", "text": "早上好\n继续说"},
    ]


def test_verified_model_marker_detects_same_size_changes(monkeypatch, tmp_path):
    import t8_runtime.model_store as model_store

    model = tmp_path / "model"
    model.mkdir()
    payload = b"good"
    (model / "weights.bin").write_bytes(payload)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "repo_id": model_store.MODEL_REPOSITORY,
                "revision": model_store.MODEL_REVISION,
                "total_size": len(payload),
                "files": [
                    {
                        "path": "weights.bin",
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(model_store, "MANIFEST_PATH", manifest_path)
    verified = ensure_model_integrity(model, force=True)
    assert verified["valid"] and verified["integrity_verified"]
    time.sleep(0.01)
    (model / "weights.bin").write_bytes(b"evil")
    quick = validate_model_dir(model)
    assert quick["valid"] and not quick["integrity_verified"]
    full = ensure_model_integrity(model)
    assert not full["valid"]
    assert full["hash_mismatch"]


def test_batch_audio_merges_sequential_and_srt_timeline(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("T8_BREEZE_OUTPUT_DIR", str(tmp_path / "output"))
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    sf.write(first, np.ones(2400, dtype=np.float32) * 0.1, 24_000)
    sf.write(second, np.ones(2400, dtype=np.float32) * 0.2, 24_000)
    results = [
        {"metadata": {"output": str(first)}, "subtitle": {"start_ms": 0, "end_ms": 100}},
        {"metadata": {"output": str(second)}, "subtitle": {"start_ms": 500, "end_ms": 600}},
    ]
    sequential, sequential_meta = merge_batch_outputs(results, timeline=False)
    assert sequential.is_file()
    assert sequential_meta["samples"] == 2400 + 4800 + 2400
    timeline, timeline_meta = merge_batch_outputs(results, timeline=True)
    assert timeline.is_file()
    assert timeline_meta["samples"] == 12_000 + 2400
