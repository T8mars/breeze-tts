"""Real GPU acceptance of both desktop ASR engines and subsequent TTS.

Uses an isolated settings/output directory. Does not upload test recordings.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import shutil
import tempfile
import time


@contextmanager
def preserve_worker_log(data: Path, report: Path):
    """Keep local native diagnostics even if an acceptance assertion fails."""
    try:
        yield
    finally:
        source = data / "logs/confucius-worker.log"
        if source.is_file():
            report.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, report.with_suffix(".worker.log"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--long-audio", type=Path, required=True)
    parser.add_argument("--tts-model", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.project_root.resolve()))
    # This isolation also prevents smoke tests from changing the user's paths.
    with tempfile.TemporaryDirectory(prefix="t8-asr-smoke-") as temporary, preserve_worker_log(Path(temporary), args.report):
        data = Path(temporary)
        os.environ["T8_BREEZE_DATA_DIR"] = str(data)
        os.environ["T8_BREEZE_OUTPUT_DIR"] = str(data / "outputs")
        from fastapi.testclient import TestClient
        import torch
        from t8_runtime.confucius import bundled_model_dir
        from t8_runtime.transcription import bundled_whisper_model_dir
        from t8_runtime.server import create_app

        assert torch.cuda.is_available(), "GPU acceptance requires NVIDIA CUDA"
        before = int(torch.cuda.mem_get_info()[0])
        audio_bytes = args.audio.read_bytes()
        audio_hash = hashlib.sha256(audio_bytes).hexdigest()
        report = {"gpu": torch.cuda.get_device_name(), "engines": [], "source_audio_unchanged": False}
        app = create_app(args.tts_model or data / "no-tts-model")
        with TestClient(app, base_url="http://127.0.0.1") as client:
            for engine, directory in (("whisper", bundled_whisper_model_dir()), ("confucius", bundled_model_dir())):
                # A real custom directory containing spaces and shared immutable
                # files exercises the saved path, not merely the default resolver.
                custom = data / f"custom {engine} models"
                custom.mkdir()
                for name in directory.iterdir():
                    if name.is_file():
                        os.link(name, custom / name.name) if directory.drive == custom.drive else __import__("shutil").copy2(name, custom / name.name)
                # Custom Confucius can intentionally reuse bundled VAD.
                saved = client.post("/api/settings/transcription-directory", json={"engine": engine, "path": str(custom)})
                assert saved.status_code == 200, saved.text
                assert client.get("/api/settings").json()[f"{engine}_model_custom"] is True
                started = time.perf_counter()
                response = client.post("/api/tools/transcribe", json={"engine": engine,
                    "reference_filename": args.audio.name, "reference_audio_base64": base64.b64encode(audio_bytes).decode(),
                    "language": "zh", "hotwords": "语音合成,欢迎", "context": "中文语音转录测试"})
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["text"].strip(), result
                assert result["device"] == "cuda", result
                assert result["bundled_model"] is False, "custom model path was ignored"
                report["engines"].append({"engine": engine, "text": result["text"], "device": result["device"],
                    "custom_model": True, "wall_seconds": round(time.perf_counter() - started, 3),
                    "srt_present": bool(result["srt"]), "draft_only": result["draft_only"]})
                reset = client.post("/api/settings/transcription-directory", json={"engine": engine, "path": ""})
                assert reset.status_code == 200
            long_bytes = args.long_audio.read_bytes()
            started = time.perf_counter()
            response = client.post("/api/tools/transcribe", json={"engine": "confucius",
                "reference_filename": args.long_audio.name, "reference_audio_base64": base64.b64encode(long_bytes).decode(), "language": "zh"})
            assert response.status_code == 200, response.text
            long = response.json()
            assert long["duration_seconds"] > 30 and long["text"].strip(), long
            assert long["mode_executed"] == "segmented_offline", long
            assert not long["srt"] and not long["timestamps_available"]
            report["long_audio"] = {"duration_seconds": long["duration_seconds"], "mode": long["mode_executed"],
                "boundaries": len(long["recognition_boundaries"]), "text_chars": len(long["text"]),
                "wall_seconds": round(time.perf_counter() - started, 3), "status": long["status"]}
            after = int(torch.cuda.mem_get_info()[0])
            report["free_vram_before_bytes"], report["free_vram_after_asr_bytes"] = before, after
            assert before - after < 512 * 1024**2, "ASR left substantial VRAM allocated"
            report["source_audio_unchanged"] = hashlib.sha256(args.audio.read_bytes()).hexdigest() == audio_hash
            assert report["source_audio_unchanged"]
            if args.tts_model:
                from t8_runtime.runtime_manager import GenerationRequest
                target, meta = app.state.runtime.generate(GenerationRequest(mode="design", text="你好，欢迎使用。",
                    instruction="自然清晰的中文声音", cfg_scale=1, seed=7))
                assert target.is_file() and meta["duration_seconds"] > 0
                report["tts_after_asr"] = {"duration_seconds": meta["duration_seconds"],
                    "device": app.state.runtime.status().get("device_report"), "success": True}
                app.state.runtime.unload()
        # Desktop Windows shutdown uses TerminateProcess, not Python atexit.
        # Verify the worker's parent-death watcher against that actual failure.
        probe_code = "import sys,time; sys.path.insert(0,sys.argv[1]); from t8_runtime.confucius import _worker_manager,model_dir; m=_worker_manager(model_dir()); m.ensure(); print(m.process.pid,flush=True); time.sleep(90)"
        probe = subprocess.Popen([sys.executable, "-c", probe_code, str(args.project_root.resolve())],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.WaitForSingleObject.restype = ctypes.c_ulong
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        child_handle = None
        try:
            worker_line = probe.stdout.readline().strip()
            assert worker_line.isdigit(), f"parent-death probe failed: {worker_line}; {probe.stderr.read()}"
            child_handle = kernel.OpenProcess(0x00100000, 0, int(worker_line))
            assert child_handle, "probe worker already exited unexpectedly"
            probe.terminate()
            probe.wait(timeout=15)
            assert kernel.WaitForSingleObject(child_handle, 15000) == 0, "Confucius worker outlived its terminated parent"
            report["worker_exits_on_parent_death"] = True
        finally:
            if probe.poll() is None:
                probe.terminate()
                probe.wait(timeout=15)
            if child_handle:
                kernel.CloseHandle(child_handle)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
