"""Manual GPU acceptance with real ComfyUI memory management (Issue #2)."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comfy-source", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--decode-mode", choices=("eager", "cuda_graphs"), default="eager")
    parser.add_argument("--transformers-target", type=Path)
    parser.add_argument("--dynamic", action="store_true")
    args = parser.parse_args()
    sys.argv = [sys.argv[0]]
    sys.path.insert(0, str(args.comfy_source.resolve()))
    if args.transformers_target:
        sys.path.insert(0, str(args.transformers_target.resolve()))

    import torch
    import transformers
    import comfy_aimdo.control
    # AIMDO bindings snapshot control.lib during import, matching ComfyUI main.py.
    if args.dynamic:
        assert comfy_aimdo.control.init()
    import comfy.model_management as mm
    import comfy.model_patcher

    # Select legacy offloading or AIMDO without changing the host installation.
    mm.args.disable_dynamic_vram = not args.dynamic
    if args.dynamic:
        import comfy.memory_management
        assert comfy_aimdo.control.init_devices(d.index for d in mm.get_all_torch_devices())
        comfy.memory_management.aimdo_enabled = True
        comfy.model_patcher.CoreModelPatcher = comfy.model_patcher.ModelPatcherDynamic
    package_dir = Path(__file__).resolve().parents[1] / "comfyui-breeze-tts-T8"
    spec = importlib.util.spec_from_file_location(
        "breeze_offload_acceptance", package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    loader = sys.modules[f"{spec.name}.loader"]
    nodes = sys.modules[f"{spec.name}.nodes"]
    native = sys.modules[f"{spec.name}.native"]
    assert loader.mm is mm and loader._ComfyCorePatcher is not None

    # Exercise real cast/uncast with CPU weights and CUDA token indices first.
    embedding = native.T5Gemma2TextScaledWordEmbedding(16, 8, 0, 2.5, 15)
    ids = torch.tensor([[1, 15, 2]])
    expected = embedding(ids).detach().cuda()
    native.convert_modules_for_comfy(embedding)
    with torch.inference_mode():
        torch.testing.assert_close(embedding(ids.cuda()), expected)
    print("Real ComfyUI cast/uncast embedding: passed", flush=True)

    loader.resolve_model_dir = lambda *_: (args.model_dir.resolve(), "model.safetensors.index.json")
    bundle = loader.load_breeze_bundle(
        loader.BF16_LABEL, "bf16", "cuda", "sdpa", False, args.decode_mode,
    )
    assert len(bundle.patchers) == 2
    assert bundle.patchers[0].is_dynamic() == (args.dynamic and args.decode_mode == "eager")
    request = {
        "mode": "design", "text": "连续运行和显存卸载恢复测试。",
        "instruction": "A clear neutral voice.", "cfg_scale": 4.0,
    }
    settings = {
        "max_new_tokens": 64, "temperature": 0.9, "top_k": 50, "top_p": 1.0,
        "repetition_penalty": 1.1, "depth_temperature": 0.9, "depth_top_k": 50,
        "depth_top_p": 1.0, "seed": 4242,
    }
    cases = []
    reference_audio = None
    original_vram_state = mm.vram_state
    try:
        for index in range(3):
            offloaded = 0
            if index:
                mm.vram_state = original_vram_state
                mm.load_models_gpu(bundle.patchers, force_full_load=True)
                if bundle.patchers[0].is_dynamic():
                    # Dynamic detach retains the cached Loader bundle, unlike unload_all_models.
                    for patcher in bundle.patchers:
                        patcher.detach()
                else:
                    offloaded = bundle.patchers[0].partially_unload(
                        torch.device("cpu"), bundle.patchers[0].model_size()
                    )
                    bundle.patchers[1].partially_unload(
                        torch.device("cpu"), bundle.patchers[1].model_size()
                    )
                    assert offloaded > 0, "test did not offload any model weights"
                    if args.decode_mode == "eager":
                        # Leave castable weights on CPU to test inference under real low-VRAM paging.
                        mm.vram_state = mm.VRAMState.NO_VRAM
                cpu_weights = sum(p.device.type == "cpu" for p in bundle.model.parameters())
                assert cpu_weights, "test did not reproduce CPU-backed model state"
            else:
                cpu_weights = 0
            assert loader.try_begin_generation()
            try:
                current_request = request if not index else {
                    "mode": "clone" if index == 1 else "direction",
                    "text": "卸载后的声音克隆测试。" if index == 1 else "卸载后的声音导演测试。",
                    "instruction": "" if index == 1 else "Speak slowly and seriously.",
                    "reference_audio": reference_audio, "reference_text": request["text"],
                    "cfg_scale": 1.0 if index == 1 else 4.0,
                }
                audio, metrics = nodes._generate_audio(bundle, current_request, {**settings, "seed": 4242 + index})
            finally:
                loader.end_generation()
            if not index:
                reference_audio = audio
            waveform = audio["waveform"]
            assert waveform.numel() and bool(torch.isfinite(waveform).all())
            assert audio["sample_rate"] == 24_000
            assert bundle.model._breeze_runtime_device.type == "cuda"
            assert all(p.device.type == "cuda" for p in bundle.codec.parameters()), "codec was not restored"
            graphs = list(getattr(bundle.model, "_breeze_depth_runners", {}).values())
            if args.decode_mode == "cuda_graphs":
                assert graphs and all(r.use_graph and r._graph_prefill is not None for r in graphs)
            cases.append({
                "run": index + 1, "offloaded_bytes": offloaded,
                "mode": current_request["mode"],
                "captured_depth_graphs": sum(r._graph_prefill is not None for r in graphs),
                "cpu_parameters_before_resume": cpu_weights,
                "sample_rate": audio["sample_rate"], "shape": list(waveform.shape),
                "metrics": metrics,
            })
            print(json.dumps(cases[-1], ensure_ascii=False), flush=True)
        report = {
            "status": "passed", "issue": 2, "timestamp_unix": int(time.time()),
            "torch": torch.__version__, "transformers": transformers.__version__,
            "decode_mode": args.decode_mode, "patcher": type(bundle.patchers[0]).__name__,
            "real_comfy_cast": True, "cases": cases,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        mm.vram_state = original_vram_state
        loader.unload_breeze_bundle(bundle, reason="offload acceptance complete", hard=True)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
