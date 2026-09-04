from __future__ import annotations

import json
import hashlib
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import numpy as np
import soundfile as sf

from t8_runtime.config import MODEL_REVISION
from t8_runtime.batch_audio import merge_batch_outputs
from t8_runtime.audio_effects import EFFECT_PRESETS, apply_audio_effect, extract_inline_audio_effect
from t8_runtime.model_store import (
    ensure_model_integrity,
    ModelDownloadManager,
    load_manifest,
    record_license_acceptance,
    validate_model_dir,
)
from t8_runtime.runtime_manager import (
    GenerationRequest,
    RuntimeManager,
    fast_all_package_status,
    flash_attention_package_status,
)
from t8_runtime.script_tools import parse_multi_role_script, parse_srt
from t8_runtime.text_processing import split_text_for_model
from t8_runtime.pronunciation import apply_pronunciation_aliases


ROOT = Path(__file__).resolve().parents[1]


def test_manifest_is_pinned_and_fully_hashed():
    manifest = load_manifest()
    assert manifest["revision"] == MODEL_REVISION
    assert len(manifest["files"]) == 17
    assert sum(item["size"] for item in manifest["files"]) == manifest["total_size"]
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])


def test_desktop_runtime_manifest_matches_locked_requirements():
    manifest = json.loads((ROOT / "manifests" / "desktop-runtime.json").read_text(encoding="utf-8"))
    lock_bytes = (ROOT / "requirements-desktop.lock.txt").read_bytes()
    lock_hash = hashlib.sha256(lock_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()
    assert manifest["resolved_lock_sha256"] == lock_hash


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


def test_long_design_uses_first_segment_as_fixed_seed_voice_anchor(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_OUTPUT_DIR", str(tmp_path / "output"))
    manager = RuntimeManager(tmp_path / "model")
    manager._tokenizer = object()
    manager._model = object()
    manager._audio_tokenizer = object()
    manager._runtime = SimpleNamespace(
        sample_rate=24_000,
        iter_audio_chunks=lambda _inputs, request_id: iter([
            SimpleNamespace(audio=np.full(1200, 0.1, dtype=np.float32))
        ]),
    )
    monkeypatch.setattr(manager, "load", lambda **_kwargs: None)
    monkeypatch.setattr(
        "t8_runtime.runtime_manager.split_text_for_model",
        lambda _text, _tokenizer: ["第一段。", "第二段。"],
    )
    templates = []
    seeds = []
    encoded_paths = []
    runtime_module = ModuleType("breeze_infer.runtime")
    runtime_module.set_all_seeds = lambda seed: seeds.append(seed)
    templates_module = ModuleType("breeze_infer.templates")
    templates_module.get_template = lambda name: name
    templates_module.prepare_inputs = lambda *_args, **_kwargs: templates.append(_args[4]) or {}
    audio_module = ModuleType("breeze_infer.audio")

    def encode_anchor(_tokenizer, path):
        encoded_paths.append(Path(path))
        assert Path(path).is_file()
        return [1, 2, 3]

    audio_module.encode_prompt_audio = encode_anchor
    monkeypatch.setitem(sys.modules, "breeze_infer.runtime", runtime_module)
    monkeypatch.setitem(sys.modules, "breeze_infer.templates", templates_module)
    monkeypatch.setitem(sys.modules, "breeze_infer.audio", audio_module)

    output, metadata = manager.generate(GenerationRequest(
        mode="design", text="需要拆分的长文本。", instruction="同一个讲述者。", seed=77,
    ))

    assert output.is_file()
    assert seeds == [77, 77]
    assert templates == ["tts_instruction", "ref_edit_tata"]
    assert len(encoded_paths) == 1
    assert not encoded_paths[0].exists()
    assert metadata["voice_lock"] == {
        "enabled": True,
        "anchor_created": True,
        "strategy": "first_segment_reference",
        "seed_strategy": "fixed",
    }


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


def test_desktop_runtime_pins_windows_triton_for_fast_all():
    runtime_input = (ROOT / "requirements-desktop.in").read_text(encoding="utf-8").lower()
    runtime_lock = (ROOT / "requirements-desktop.lock.txt").read_text(encoding="utf-8").lower()
    verify_script = (ROOT / "packaging" / "verify_runtime.py").read_text(encoding="utf-8").lower()
    assert 'triton-windows==3.5.1.post24; platform_system == "windows"' in runtime_input
    assert "triton-windows==3.5.1.post24" in runtime_lock
    assert '"triton-windows": "3.5.1.post24"' in verify_script


def test_fast_all_package_status_reports_the_pinned_runtime():
    status = fast_all_package_status()
    assert isinstance(status["available"], bool)
    if status["available"]:
        assert status["version"] == "3.5.1.post24"


def test_desktop_runtime_pins_prebuilt_flash_attention_wheel():
    runtime_input = (ROOT / "requirements-desktop.in").read_text(encoding="utf-8").lower()
    runtime_lock = (ROOT / "requirements-desktop.lock.txt").read_text(encoding="utf-8").lower()
    verify_script = (ROOT / "packaging" / "verify_runtime.py").read_text(encoding="utf-8").lower()
    wheel_sha256 = "d64f636f491d2b0347a3464640282f5a016088d516f86ad5e47b37a8b87bb8af"
    assert "github.com/kingbri1/flash-attention/releases/download/v2.8.3/" in runtime_input
    assert "cp310-cp310-win_amd64.whl" in runtime_input
    assert f"sha256={wheel_sha256}" in runtime_input
    assert "flash_attn @ https://github.com/" in runtime_lock
    assert f"sha256={wheel_sha256}" in runtime_lock
    assert '"flash-attn": "2.8.3"' in verify_script


def test_flash_attention_package_status_reports_the_pinned_runtime():
    status = flash_attention_package_status()
    assert isinstance(status["available"], bool)
    if status["available"]:
        assert status["version"] == "2.8.3"


def test_runtime_status_exposes_verified_compute_device(monkeypatch, tmp_path):
    runtime = RuntimeManager(tmp_path / "model")
    runtime._runtime = SimpleNamespace(sample_rate=24_000)
    runtime._device_report = {
        "backend": "CUDA",
        "verified": True,
        "device": "cuda:0",
        "gpu_name": "Test GPU",
    }
    monkeypatch.setattr(runtime, "_compute_device_status", lambda: dict(runtime._device_report))

    status = runtime.status()

    assert status["device"] == "cuda:0"
    assert status["device_verified"] is True
    assert status["gpu_name"] == "Test GPU"
    assert status["compute_device"]["backend"] == "CUDA"


def test_runtime_has_a_hard_cuda_device_guard():
    source = (ROOT / "t8_runtime" / "runtime_manager.py").read_text(encoding="utf-8")
    assert "_verify_cuda_runtime(runtime, model, audio_tokenizer)" in source
    assert "CUDA 设备校验失败，已阻止 CPU 静默生成" in source
    assert '"compute_device": self._compute_device_status()' in source


def test_windows_fast_all_disables_pytorch_static_cuda_launcher():
    runtime_source = (ROOT / "t8_runtime" / "runtime_manager.py").read_text(encoding="utf-8")
    assert 'os.environ["TORCHINDUCTOR_USE_STATIC_CUDA_LAUNCHER"] = "0"' in runtime_source


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


def test_pronunciation_aliases_keep_original_separate_from_spoken_text():
    spoken, replacements = apply_pronunciation_aliases(
        "甄宓拜访单于。",
        [
            {"source": "甄宓", "spoken": "真伏", "language": "zh"},
            {"source": "单于", "spoken": "蝉于", "language": "auto"},
        ],
        language="zh",
    )
    assert spoken == "真伏拜访蝉于。"
    assert [(item["source"], item["count"]) for item in replacements] == [("甄宓", 1), ("单于", 1)]


def test_inline_spatial_effect_is_removed_before_tts_and_applied_afterward():
    spoken, effect = extract_inline_audio_effect("你要不要过来吃饭？（山间回音）")
    assert spoken == "你要不要过来吃饭？"
    assert effect["preset"] == "mountain_echo"
    dry = np.ones(2_400, dtype=np.float32) * 0.1
    processed = apply_audio_effect(dry, 24_000, effect)
    assert processed.size > dry.size
    assert np.max(np.abs(processed)) <= 0.98


def test_every_categorized_audio_effect_produces_safe_finite_audio():
    rate = 24_000
    time_axis = np.arange(rate, dtype=np.float32) / rate
    dry = (0.18 * np.sin(2 * np.pi * 220 * time_axis) + 0.08 * np.sin(2 * np.pi * 2200 * time_axis)).astype(np.float32)
    categories = {preset["category"] for preset in EFFECT_PRESETS.values()}
    assert categories == {"基础", "室内空间", "大型空间", "户外／特殊空间", "设备／传播", "质感／创意"}

    rendered = {}
    for preset_id in EFFECT_PRESETS:
        processed = apply_audio_effect(dry, rate, {"preset": preset_id, "mix": 0.55})
        assert processed.size >= dry.size, preset_id
        assert np.isfinite(processed).all(), preset_id
        assert float(np.max(np.abs(processed), initial=0.0)) <= 0.98001, preset_id
        rendered[preset_id] = processed

    assert not np.allclose(rendered["telephone"][: dry.size], dry)
    assert not np.allclose(rendered["robot"][: dry.size], dry)
    assert rendered["valley"].size > rendered["mountain_echo"].size


@pytest.mark.parametrize(
    ("tag", "preset"),
    [
        ("（电话通话）", "telephone"),
        ("（峡谷远回声）", "valley"),
        ("[FX:walkie_talkie]", "walkie_talkie"),
        ("[FX:robot]", "robot"),
    ],
)
def test_inline_audio_effect_tags_cover_new_categories(tag, preset):
    spoken, effect = extract_inline_audio_effect(f"测试台词{tag}")
    assert spoken == "测试台词"
    assert effect["preset"] == preset


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


def test_multi_role_parser_preserves_official_and_experimental_vocal_events():
    roles = parse_multi_role_script(
        "[笑] 开场。\n[抽泣] 仍是旁白。\n[小蓝] 角色登场。\n[喘息] 仍是小蓝。"
    )
    assert roles == [
        {"role": "旁白", "text": "[笑] 开场。\n[抽泣] 仍是旁白。"},
        {"role": "小蓝", "text": "角色登场。\n[喘息] 仍是小蓝。"},
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
        {"metadata": {"output": str(second)}, "subtitle": {"start_ms": 500, "end_ms": 600}, "audio_effect": {"preset": "small_room", "mix": 0.35}},
    ]
    sequential, sequential_meta = merge_batch_outputs(results, timeline=False)
    assert sequential.is_file()
    assert sequential_meta["samples"] > 2400 + 4800 + 2400
    assert sequential_meta["audio_effects"][1]["preset"] == "small_room"
    timeline, timeline_meta = merge_batch_outputs(results, timeline=True)
    assert timeline.is_file()
    assert timeline_meta["samples"] > 12_000 + 2400


def test_batch_audio_applies_effect_from_dry_source_once(monkeypatch, tmp_path):
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("T8_BREEZE_OUTPUT_DIR", str(tmp_path / "output"))
    rendered = tmp_path / "rendered.wav"
    dry = tmp_path / "dry.wav"
    sf.write(rendered, np.full(2400, 0.8, dtype=np.float32), 24_000)
    sf.write(dry, np.full(2400, 0.1, dtype=np.float32), 24_000)

    merged, _metadata = merge_batch_outputs([{
        "metadata": {"output": str(rendered), "dry_output": str(dry)},
        "audio_effect": {"preset": "none", "mix": 0.35},
    }], timeline=False)
    audio, _rate = sf.read(merged, dtype="float32")

    assert np.max(np.abs(audio - 0.1)) < 0.001
