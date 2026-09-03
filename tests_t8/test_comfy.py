from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import random
import sys
import wave
import zipfile
from pathlib import Path

import pytest
import numpy as np
import torch


ROOT = Path(__file__).resolve().parents[1]


def _load_package(name: str):
    package_dir = ROOT / "comfyui-breeze-tts-T8"
    spec = importlib.util.spec_from_file_location(
        name,
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _wav_bytes(*, sample_rate: int = 8_000, frames: int = 800) -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * frames)
    return target.getvalue()


def _write_voice_bundle(path: Path, *, payload: bytes | None = None, digest: str | None = None) -> None:
    files = []
    voice = {
        "schema_version": 2,
        "id": "voice-test",
        "name": "测试音色",
        "mode": "clone" if payload is not None else "design",
        "language": "zh",
        "instruction": "语气自然、清晰。",
        "reference_text": "这是一段准确的参考文本。" if payload is not None else "",
    }
    if payload is not None:
        member = "assets/reference.wav"
        voice["reference_member"] = member
        files.append(
            {
                "path": member,
                "size": len(payload),
                "sha256": digest or hashlib.sha256(payload).hexdigest(),
            }
        )
    manifest = {"schema_version": 1, "type": "t8voice", "voice": voice, "files": files}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        if payload is not None:
            archive.writestr("assets/reference.wav", payload)


@pytest.mark.comfy
def test_comfy_package_registers_eight_nodes():
    package_dir = ROOT / "comfyui-breeze-tts-T8"
    module = _load_package("comfyui_breeze_tts_T8_test")
    assert module.__version__ == "0.2.9"
    assert 'version = "0.2.9"' in (package_dir / "pyproject.toml").read_text(encoding="utf-8")
    assert len(module.NODE_CLASS_MAPPINGS) == 8
    assert set(module.NODE_CLASS_MAPPINGS) == {
        "T8_BreezeTTS_ModelLoader",
        "T8_BreezeTTS_DesignRequest",
        "T8_BreezeTTS_CloneRequest",
        "T8_BreezeTTS_DirectionRequest",
        "T8_BreezeTTS_VoiceBundleRequest",
        "T8_BreezeTTS_LineDirection",
        "T8_BreezeTTS_GenerationSettings",
        "T8_BreezeTTS_Generate",
    }
    for node in module.NODE_CLASS_MAPPINGS.values():
        schema = node.INPUT_TYPES()
        assert "required" in schema

    expected_contracts = {
        "T8_BreezeTTS_ModelLoader": (("BREEZE_T8_MODEL", "STRING"), ("model", "model_info")),
        "T8_BreezeTTS_DesignRequest": (("BREEZE_T8_REQUEST",), ("request",)),
        "T8_BreezeTTS_CloneRequest": (("BREEZE_T8_REQUEST",), ("request",)),
        "T8_BreezeTTS_DirectionRequest": (("BREEZE_T8_REQUEST",), ("request",)),
        "T8_BreezeTTS_VoiceBundleRequest": (
            ("BREEZE_T8_REQUEST", "AUDIO", "STRING"),
            ("request", "reference_audio", "voice_info"),
        ),
        "T8_BreezeTTS_LineDirection": (("BREEZE_T8_REQUEST",), ("request",)),
        "T8_BreezeTTS_GenerationSettings": (("BREEZE_T8_SETTINGS",), ("settings",)),
        "T8_BreezeTTS_Generate": (("AUDIO", "STRING"), ("audio", "generation_info")),
    }
    for name, (return_types, return_names) in expected_contracts.items():
        node = module.NODE_CLASS_MAPPINGS[name]
        assert node.RETURN_TYPES == return_types
        assert node.RETURN_NAMES == return_names
        assert isinstance(node.FUNCTION, str) and node.FUNCTION
        assert node.CATEGORY == "T8star-Aix/Audio/Breeze TTS"
    assert module.NODE_CLASS_MAPPINGS["T8_BreezeTTS_Generate"].OUTPUT_NODE is True

    workflows = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in (package_dir / "examples").glob("*_api.json")
    }
    assert set(workflows) == {
        "voice_design_api.json",
        "voice_clone_api.json",
        "voice_direction_api.json",
        "voice_bundle_api.json",
    }
    for workflow in workflows.values():
        t8_types = {
            node["class_type"]
            for node in workflow.values()
            if node["class_type"].startswith("T8_BreezeTTS_")
        }
        assert t8_types <= set(module.NODE_CLASS_MAPPINGS)
        assert "SaveAudio" in {node["class_type"] for node in workflow.values()}
    assert "PreviewAudio" in {
        node["class_type"] for node in workflows["voice_design_api.json"].values()
    }
    requirements = (package_dir / "requirements.txt").read_text(encoding="utf-8").lower()
    declared = {
        line.split("=", 1)[0].split(">", 1)[0].strip()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert declared.isdisjoint({"torch", "torchaudio", "torchvision", "transformers", "tokenizers", "numpy"})
    assert "accelerate" in declared
    assert not (package_dir / "install.py").exists()

    nodes = sys.modules[f"{module.__name__}.nodes"]
    class Bundle:
        device = torch.device("cpu")

    py_state = random.getstate()
    np_state = np.random.get_state()
    torch_state = torch.get_rng_state().clone()
    with nodes._isolated_rng(Bundle(), 1234) as actual_seed:
        assert actual_seed == 1234
        _ = (random.random(), np.random.random(), torch.rand(1))
    assert random.getstate() == py_state
    assert np.array_equal(np.random.get_state()[1], np_state[1])
    assert torch.equal(torch.get_rng_state(), torch_state)


@pytest.mark.comfy
def test_comfy_ships_frontend_loadable_workflows():
    package_dir = ROOT / "comfyui-breeze-tts-T8"
    module = _load_package("comfyui_breeze_tts_T8_ui_workflows")
    workflows = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in (package_dir / "examples").glob("*_workflow.json")
    }
    assert set(workflows) == {
        "voice_design_workflow.json",
        "voice_clone_workflow.json",
        "voice_direction_workflow.json",
        "voice_bundle_workflow.json",
    }

    builtin_types = {"LoadAudio", "PreviewAudio", "SaveAudio"}
    canonical_events = ("[笑]", "[咳嗽]", "[清嗓子]", "[叹气]", "(laugh)", "(cough)", "(clears throat)", "(sigh)")
    for filename, workflow in workflows.items():
        assert workflow["version"] == 0.4
        assert workflow["extra"]["t8_example_kind"] == "ui_workflow"
        assert workflow["extra"]["t8_node_version"] == module.__version__
        assert isinstance(workflow["nodes"], list) and workflow["nodes"]
        assert isinstance(workflow["links"], list) and workflow["links"]
        assert workflow["last_node_id"] == max(node["id"] for node in workflow["nodes"])
        assert workflow["last_link_id"] == max(link[0] for link in workflow["links"])

        nodes = {node["id"]: node for node in workflow["nodes"]}
        assert len(nodes) == len(workflow["nodes"])
        node_types = {node["type"] for node in nodes.values()}
        assert node_types <= set(module.NODE_CLASS_MAPPINGS) | builtin_types
        assert {"T8_BreezeTTS_ModelLoader", "T8_BreezeTTS_Generate", "PreviewAudio", "SaveAudio"} <= node_types
        group_titles = " ".join(group["title"] for group in workflow["groups"])
        assert all(event in group_titles for event in canonical_events)

        loader_node = next(node for node in nodes.values() if node["type"] == "T8_BreezeTTS_ModelLoader")
        assert loader_node["widgets_values"][-1] is False
        assert "许可证" in loader_node["title"]
        for core_type in ("PreviewAudio", "SaveAudio"):
            core_node = next(node for node in nodes.values() if node["type"] == core_type)
            assert core_node["outputs"] == [
                {"name": "audio", "type": "AUDIO", "links": None, "slot_index": 0}
            ]

        seen_link_ids = set()
        for link_id, origin_id, origin_slot, target_id, target_slot, data_type in workflow["links"]:
            assert link_id not in seen_link_ids
            seen_link_ids.add(link_id)
            assert origin_id in nodes and target_id in nodes
            origin = nodes[origin_id]
            target = nodes[target_id]
            assert origin["outputs"][origin_slot]["type"] == data_type
            assert link_id in origin["outputs"][origin_slot]["links"]
            assert target["inputs"][target_slot]["type"] == data_type
            assert target["inputs"][target_slot]["link"] == link_id

        # API prompt examples remain separate; users must not be told to drag
        # those API-only dictionaries into the frontend canvas.
        api_name = filename.replace("_workflow.json", "_api.json")
        api_prompt = json.loads((package_dir / "examples" / api_name).read_text(encoding="utf-8"))
        assert "nodes" not in api_prompt and "links" not in api_prompt

    generator = (package_dir / "scripts" / "generate_ui_workflows.py").read_text(encoding="utf-8")
    assert f'PACKAGE_VERSION = "{module.__version__}"' in generator


@pytest.mark.comfy
def test_inline_vocal_events_are_documented_and_preserved_by_request_nodes():
    module = _load_package("comfyui_breeze_tts_T8_inline_events")
    nodes = sys.modules[f"{module.__name__}.nodes"]
    text = "[清嗓子] 现在开始。(laugh) That was unexpected."
    audio = {"waveform": torch.zeros(1, 1, 24), "sample_rate": 24_000}

    design, = nodes.BreezeT8DesignRequest().build(text, "温和清晰。", 4.0)
    clone, = nodes.BreezeT8CloneRequest().build(text, audio, "准确逐字稿。")
    direction, = nodes.BreezeT8DirectionRequest().build(
        text, audio, "准确逐字稿。", "先严肃，随后轻笑。", 4.0
    )
    assert design["text"] == clone["text"] == direction["text"] == text

    for request_node in (
        nodes.BreezeT8DesignRequest,
        nodes.BreezeT8CloneRequest,
        nodes.BreezeT8DirectionRequest,
        nodes.BreezeT8VoiceBundleRequest,
    ):
        tooltip = request_node.INPUT_TYPES()["required"]["text"][1]["tooltip"]
        assert "[清嗓子]" in tooltip
        assert "(clears throat)" in tooltip


@pytest.mark.comfy
def test_comfy_oom_releases_serial_lock(monkeypatch):
    module = _load_package("comfyui_breeze_tts_T8_oom")
    nodes = sys.modules[f"{module.__name__}.nodes"]
    generator = nodes.BreezeT8Generate()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(nodes, "_generate_audio", lambda *_: (_ for _ in ()).throw(torch.OutOfMemoryError("test")))
    with pytest.raises(RuntimeError, match="显存不足"):
        generator.generate(object(), {"mode": "design"}, {"seed": 1})

    audio = {"waveform": torch.zeros(1, 1, 24), "sample_rate": 24_000}
    metrics = {"elapsed_seconds": 0.1}
    monkeypatch.setattr(nodes, "_generate_audio", lambda *_: (audio, metrics))
    result_audio, info = generator.generate(object(), {"mode": "design"}, {"seed": 1})
    assert result_audio is audio
    assert '"mode": "design"' in info


@pytest.mark.comfy
def test_desktop_voice_bundle_builds_standard_audio_and_line_override(tmp_path):
    module = _load_package("comfyui_breeze_tts_T8_voice_bundle")
    nodes = sys.modules[f"{module.__name__}.nodes"]
    bundle = tmp_path / "role.t8voice.zip"
    _write_voice_bundle(bundle, payload=_wav_bytes())

    request, audio, info = nodes.BreezeT8VoiceBundleRequest().build(
        str(bundle), "[咳嗽] 新的台词。", "inherit", "", 0.0
    )
    assert request["mode"] == "clone"
    assert request["reference_audio"] is audio
    assert request["reference_text"] == "这是一段准确的参考文本。"
    assert request["voice_id"] == "voice-test"
    assert request["text"] == "[咳嗽] 新的台词。"
    assert audio["sample_rate"] == 8_000
    assert audio["waveform"].shape == (1, 1, 800)
    assert audio["waveform"].dtype == torch.float32
    assert json.loads(info)["name"] == "测试音色"

    directed, _, _ = nodes.BreezeT8VoiceBundleRequest().build(
        str(bundle), "第二句。", "override", "压低声音，语速稍慢。", 3.5
    )
    assert directed["mode"] == "direction"
    assert directed["instruction"] == "压低声音，语速稍慢。"
    assert directed["cfg_scale"] == 3.5

    original = dict(request)
    neutral, = nodes.BreezeT8LineDirection().apply(request, "neutral", "unused", 0.0)
    assert neutral["mode"] == "clone"
    assert neutral["instruction"] == nodes.runtime.DEFAULT_INSTRUCTION
    assert request == original


@pytest.mark.comfy
def test_voice_bundle_rejects_hash_tampering_unsafe_paths_and_limits(tmp_path, monkeypatch):
    module = _load_package("comfyui_breeze_tts_T8_voice_security")
    voice_bundle = sys.modules[f"{module.__name__}.voice_bundle"]
    tampered = tmp_path / "tampered.t8voice.zip"
    _write_voice_bundle(tampered, payload=_wav_bytes(), digest="0" * 64)
    with pytest.raises(ValueError, match="SHA-256"):
        voice_bundle.load_voice_bundle(tampered)

    unsafe = tmp_path / "unsafe.t8voice.zip"
    manifest = {
        "schema_version": 1,
        "type": "t8voice",
        "voice": {"mode": "design", "instruction": "clear"},
        "files": [],
    }
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("../outside.wav", b"bad")
    with pytest.raises(ValueError, match="路径穿越"):
        voice_bundle.load_voice_bundle(unsafe)

    duplicate = tmp_path / "duplicate.t8voice.zip"
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("assets/Voice.wav", b"one")
        archive.writestr("assets/voice.WAV", b"two")
    with pytest.raises(ValueError, match="大小写重复路径"):
        voice_bundle.load_voice_bundle(duplicate)

    valid = tmp_path / "size-limit.t8voice.zip"
    _write_voice_bundle(valid)
    monkeypatch.setattr(voice_bundle, "MAX_ARCHIVE_BYTES", 1)
    with pytest.raises(ValueError, match="超过 132 MiB"):
        voice_bundle.load_voice_bundle(valid)

    with pytest.raises(ValueError, match="超过 60 秒"):
        voice_bundle.decode_reference_audio(
            _wav_bytes(sample_rate=10, frames=601), "assets/reference.wav"
        )


@pytest.mark.comfy
def test_line_direction_requires_text_and_preserves_transformers_host():
    module = _load_package("comfyui_breeze_tts_T8_direction_helper")
    nodes = sys.modules[f"{module.__name__}.nodes"]
    base = {"mode": "design", "text": "line", "instruction": "voice", "cfg_scale": 4.0}
    with pytest.raises(ValueError, match="direction 不能为空"):
        nodes.BreezeT8LineDirection().apply(base, "override", "", 0.0)
    changed, = nodes.BreezeT8LineDirection().apply(base, "override", "更有力量。", 5.0)
    assert changed == {
        "mode": "design",
        "text": "line",
        "instruction": "更有力量。",
        "cfg_scale": 5.0,
        "line_direction_mode": "override",
    }
    requirements = (ROOT / "comfyui-breeze-tts-T8" / "requirements.txt").read_text(encoding="utf-8")
    protected = {"torch", "torchaudio", "torchvision", "transformers", "tokenizers", "numpy"}
    names = {
        line.split("=", 1)[0].split(">", 1)[0].strip().lower()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert names.isdisjoint(protected)
