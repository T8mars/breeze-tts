"""Focused RTX acceptance for long Voice Clone prompts in Fast All mode."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf


def _audio_facts(path: Path) -> dict:
    audio, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    return {
        "path": str(path),
        "sample_rate": int(sample_rate),
        "channels": int(audio.shape[1]),
        "samples": int(audio.shape[0]),
        "finite": bool(np.isfinite(audio).all()),
        "peak": float(np.abs(audio).max(initial=0.0)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--accept-license", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    output_dir = root / "outputs" / "fast-all-long-clone"
    output_dir.mkdir(parents=True, exist_ok=True)
    os.environ["T8_BREEZE_OUTPUT_DIR"] = str(output_dir)

    from t8_runtime.model_store import record_license_acceptance, validate_model_dir
    from t8_runtime.runtime_manager import GenerationRequest, RuntimeManager

    model_dir = args.model_dir.resolve()
    if args.accept_license:
        record_license_acceptance(model_dir)
    model_report = validate_model_dir(model_dir, verify_hashes=False)
    if not model_report["valid"] or not model_report["license_accepted"]:
        raise RuntimeError(f"model not ready/accepted: {model_report}")

    manager = RuntimeManager(model_dir)
    started = time.perf_counter()
    try:
        reference_text = "你好，这是用于验证快速模式长文本声音克隆的参考声音。"
        reference_path, reference_metadata = manager.generate(
            GenerationRequest(
                mode="design",
                text=reference_text,
                instruction="一位自然清晰的普通话说话者，语速适中。",
                cfg_scale=4.0,
                seed=3101,
                fast_all=False,
                max_new_tokens=96,
            )
        )

        phrase = "这是用于验证快速模式长文本声音克隆稳定性的连续中文内容，"
        long_text = phrase * 12
        if len(long_text) <= 130:
            raise AssertionError("long clone acceptance text must exceed 130 characters")
        output_path, metadata = manager.generate(
            GenerationRequest(
                mode="clone",
                text=long_text,
                ref_audio_path=reference_path,
                ref_text=reference_text,
                cfg_scale=1.0,
                seed=3102,
                fast_all=True,
                max_new_tokens=64,
            )
        )
        audio = _audio_facts(output_path)
        if (
            audio["sample_rate"] != 24_000
            or audio["channels"] != 1
            or not audio["finite"]
            or audio["samples"] <= 0
        ):
            raise AssertionError(f"invalid long-clone audio: {audio}")

        prefill_records = [
            segment.get("backbone_prefill", {}) for segment in metadata["segments"]
        ]
        exercised = [
            record
            for record in prefill_records
            if isinstance(record.get("bucket"), int) and record["bucket"] >= 288
        ]
        if not exercised:
            raise AssertionError(
                f"long clone did not exercise the former (1, 288)+ failure range: {prefill_records}"
            )
        if any(
            record.get("backend") not in {"cuda_graph", "eager_fallback"}
            for record in exercised
        ):
            raise AssertionError(f"unexpected prefill backend: {exercised}")

        report = {
            "status": "passed",
            "elapsed_seconds": time.perf_counter() - started,
            "gpu_runtime": manager.status(),
            "reference": {
                "audio": _audio_facts(reference_path),
                "metadata": reference_metadata,
            },
            "long_clone": {
                "characters": len(long_text),
                "audio": audio,
                "metadata": metadata,
                "exercised_prefill_records": exercised,
            },
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "status": "passed",
                    "report": str(args.report.resolve()),
                    "prefill": exercised,
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        manager.unload()


if __name__ == "__main__":
    raise SystemExit(main())
