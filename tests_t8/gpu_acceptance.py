"""Manual full GPU acceptance for the isolated Windows desktop runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf


def audio_facts(path: Path) -> dict:
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    return {
        "path": str(path),
        "sample_rate": int(rate),
        "channels": int(audio.shape[1]),
        "samples": int(audio.shape[0]),
        "finite": bool(np.isfinite(audio).all()),
        "peak": float(np.abs(audio).max(initial=0.0)),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def deterministic_facts(first: Path, second: Path) -> dict:
    a, rate_a = sf.read(first, dtype="float32", always_2d=True)
    b, rate_b = sf.read(second, dtype="float32", always_2d=True)
    if rate_a != rate_b or a.shape != b.shape:
        return {"passed": False, "reason": "shape-or-rate-mismatch", "shape_a": list(a.shape), "shape_b": list(b.shape)}
    flat_a = a.reshape(-1)
    flat_b = b.reshape(-1)
    mae = float(np.mean(np.abs(flat_a - flat_b)))
    maximum = float(np.max(np.abs(flat_a - flat_b), initial=0.0))
    correlation = float(np.corrcoef(flat_a, flat_b)[0, 1])
    return {
        "passed": bool(mae <= 0.005 and correlation >= 0.995),
        "mae": mae,
        "max_abs_error": maximum,
        "correlation": correlation,
        "tolerance": {"mae_max": 0.005, "correlation_min": 0.995},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--accept-license", action="store_true")
    parser.add_argument("--stability-runs", type=int, default=20)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    output = root / "outputs" / "gpu-acceptance"
    output.mkdir(parents=True, exist_ok=True)
    os.environ["T8_BREEZE_OUTPUT_DIR"] = str(output)

    import torch
    from t8_runtime.model_store import record_license_acceptance, validate_model_dir
    from t8_runtime.runtime_manager import GenerationRequest, RuntimeManager

    if args.accept_license:
        record_license_acceptance(args.model_dir)
    model_report = validate_model_dir(args.model_dir, verify_hashes=False)
    if not model_report["valid"] or not model_report["license_accepted"]:
        raise RuntimeError(f"model not ready/accepted: {model_report}")

    manager = RuntimeManager(args.model_dir)
    cases: list[dict] = []

    def run(name: str, request: GenerationRequest) -> tuple[Path, dict]:
        path, metadata = manager.generate(request)
        facts = audio_facts(path)
        if facts["sample_rate"] != 24_000 or facts["channels"] != 1 or not facts["finite"] or facts["samples"] <= 0:
            raise AssertionError(f"invalid audio for {name}: {facts}")
        cases.append({"name": name, "audio": facts, "metadata": metadata})
        return path, metadata

    zh_ref_text = "你好，这是用于测试的合成参考声音。"
    en_ref_text = "Hello, this is a synthetic reference voice for testing."
    zh_ref, _ = run("design_zh", GenerationRequest(
        mode="design", text=zh_ref_text,
        instruction="一位温暖自然的年轻女性，声音清晰，语速适中。",
        cfg_scale=4.0, seed=101, max_new_tokens=192,
    ))
    en_ref, _ = run("design_en", GenerationRequest(
        mode="design", text=en_ref_text,
        instruction="A warm, natural young woman with clear speech and a moderate pace.",
        cfg_scale=4.0, seed=102, max_new_tokens=192,
    ))
    run("clone_zh", GenerationRequest(
        mode="clone", text="今天的语音克隆测试顺利完成。", ref_audio_path=zh_ref,
        ref_text=zh_ref_text, cfg_scale=1.0, seed=103, max_new_tokens=192,
    ))
    run("clone_en", GenerationRequest(
        mode="clone", text="The English voice clone test completed successfully.", ref_audio_path=en_ref,
        ref_text=en_ref_text, cfg_scale=1.0, seed=104, max_new_tokens=192,
    ))
    run("direction_zh", GenerationRequest(
        mode="direction", text="请用克制而严肃的语气说出这句话。", instruction="语速放慢，语气克制而严肃。",
        ref_audio_path=zh_ref, ref_text=zh_ref_text, cfg_scale=4.0, seed=105, max_new_tokens=192,
    ))
    run("direction_en", GenerationRequest(
        mode="direction", text="Please deliver this line slowly with restrained seriousness.",
        instruction="Speak slowly with a restrained, serious tone.", ref_audio_path=en_ref,
        ref_text=en_ref_text, cfg_scale=4.0, seed=106, max_new_tokens=192,
    ))

    deterministic_request = GenerationRequest(
        mode="design", text="Deterministic seed check.", instruction="A clear neutral voice.",
        cfg_scale=4.0, seed=9001, max_new_tokens=96,
    )
    first, _ = run("determinism_a", deterministic_request)
    second, _ = run("determinism_b", deterministic_request)
    deterministic = deterministic_facts(first, second)
    if not deterministic["passed"]:
        raise AssertionError(f"same-seed output exceeded deterministic tolerance: {deterministic}")

    cancelled = False
    cancel_event = threading.Event()
    cancel_event.set()
    before_cancel = set(output.glob("*.wav"))
    try:
        manager.generate(GenerationRequest(
            mode="design", text="This generation must be cancelled safely.",
            instruction="A neutral voice.", seed=77, max_new_tokens=96,
        ), cancel_event=cancel_event)
    except InterruptedError:
        cancelled = True
    after_cancel = set(output.glob("*.wav"))
    if not cancelled or after_cancel != before_cancel:
        raise AssertionError("cancelled generation left a partial WAV or did not cancel")

    oom_recovered = False
    try:
        torch.empty((256, 1024, 1024, 1024), dtype=torch.float32, device="cuda")
    except torch.OutOfMemoryError:
        torch.cuda.empty_cache()
        run("post_oom_recovery", GenerationRequest(
            mode="design", text="Recovered after a controlled out of memory test.",
            instruction="A clear neutral voice.", seed=78, max_new_tokens=96,
        ))
        oom_recovered = True
    if not oom_recovered:
        raise AssertionError("controlled OOM did not raise torch.OutOfMemoryError")

    memory_samples = []
    for index in range(args.stability_runs):
        run(f"stability_{index + 1:02d}", GenerationRequest(
            mode="design", text=f"Stability run {index + 1}.", instruction="A short clear voice.",
            seed=2000 + index, max_new_tokens=64,
        ))
        memory_samples.append({
            "run": index + 1,
            "allocated": int(torch.cuda.memory_allocated()),
            "reserved": int(torch.cuda.memory_reserved()),
        })
    reserved_growth = max(item["reserved"] for item in memory_samples) - min(item["reserved"] for item in memory_samples)
    if reserved_growth > 512 * 1024 * 1024:
        raise AssertionError(f"reserved VRAM grew by {reserved_growth} bytes across stability runs")

    report = {
        "status": "passed",
        "timestamp_unix": int(time.time()),
        "torch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0),
        "model": model_report,
        "cases": cases,
        "deterministic_tolerance": deterministic,
        "cancel_recovered": cancelled,
        "oom_recovered": oom_recovered,
        "stability_runs": args.stability_runs,
        "reserved_growth_bytes": reserved_growth,
        "memory_samples": memory_samples,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    manager.unload()
    print(json.dumps({"status": "passed", "report": str(args.report), "cases": len(cases)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
