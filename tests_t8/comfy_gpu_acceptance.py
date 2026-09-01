"""Manual real-generation acceptance for a ComfyUI host environment."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--transformers-target", type=Path)
    args = parser.parse_args()
    sys.argv = [sys.argv[0]]
    if args.transformers_target:
        sys.path.insert(0, str(args.transformers_target.resolve()))

    import torch
    import transformers

    package = args.package_dir.resolve()
    name = f"t8_comfy_acceptance_{transformers.__version__.replace('.', '_')}"
    spec = importlib.util.spec_from_file_location(name, package / "__init__.py", submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    loader = sys.modules[f"{name}.loader"]
    nodes = sys.modules[f"{name}.nodes"]
    loader.resolve_model_dir = lambda *_: (args.model_dir.resolve(), "model.safetensors.index.json")

    bundle = loader.load_breeze_bundle(loader.BF16_LABEL, "auto", "cuda", "eager", False, "eager")
    base_settings = {
        "max_new_tokens": 64, "temperature": 0.9, "top_k": 50, "top_p": 1.0,
        "repetition_penalty": 1.1, "depth_temperature": 0.9, "depth_top_k": 50,
        "depth_top_p": 1.0, "seed": 4242,
    }
    cases = []

    def generate_case(mode: str, request: dict, seed: int):
        settings = {**base_settings, "seed": seed}
        audio, metrics = nodes._generate_audio(bundle, request, settings)
        waveform = audio["waveform"]
        if audio["sample_rate"] != 24_000 or tuple(waveform.shape[:2]) != (1, 1):
            raise AssertionError(f"invalid AUDIO output for {mode}: {waveform.shape}, {audio['sample_rate']}")
        if waveform.numel() <= 0 or not bool(torch.isfinite(waveform).all()):
            raise AssertionError(f"ComfyUI {mode} returned empty or non-finite audio")
        cases.append(
            {
                "mode": mode,
                "sample_rate": audio["sample_rate"],
                "shape": list(waveform.shape),
                "metrics": metrics,
            }
        )
        return audio

    try:
        reference_text = "Breeze TTS ComfyUI compatibility test."
        reference_audio = generate_case(
            "design",
            {
                "mode": "design",
                "text": reference_text,
                "instruction": "A clear neutral voice.",
                "cfg_scale": 4.0,
            },
            4242,
        )
        generate_case(
            "clone",
            {
                "mode": "clone",
                "text": "The cloned voice is working.",
                "instruction": "Speak clearly and naturally.",
                "reference_audio": reference_audio,
                "reference_text": reference_text,
                "cfg_scale": 1.0,
            },
            4243,
        )
        generate_case(
            "direction",
            {
                "mode": "direction",
                "text": "Please speak slowly and seriously.",
                "instruction": "Speak slowly with restrained seriousness.",
                "reference_audio": reference_audio,
                "reference_text": reference_text,
                "cfg_scale": 4.0,
            },
            4244,
        )
        if not cases[2]["metrics"]["reference_cache_hit"]:
            raise AssertionError("direction did not reuse the cached reference encoding")
        report = {
            "status": "passed",
            "timestamp_unix": int(time.time()),
            "python": sys.version.split()[0],
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "node_count": len(module.NODE_CLASS_MAPPINGS),
            "modes": [case["mode"] for case in cases],
            "cases": cases,
            "model_revision": loader.MODEL_REVISION,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        loader.unload_breeze_bundle(bundle, reason="acceptance complete", hard=True)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
