"""Desktop transport adapter; inference is provided by the vendored project."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import secrets
import subprocess
import time
from typing import Any

import soundfile as sf

from .config import project_root, user_data_dir
from .settings_store import load_settings

MODEL_FILES = ("Confucius4-R2T2-Q8_0.gguf", "mmproj-Confucius4-R2T2-Q8_0.gguf")
VAD_FILES = ("fireredvad_stream_vad_with_cache.onnx", "cmvn.ark")
LANGUAGES = {"zh": "Chinese", "en": "English", "yue": "Cantonese", "ja": "Japanese",
             "ko": "Korean", "de": "German", "fr": "French", "ru": "Russian",
             "pt": "Portuguese", "es": "Spanish", "it": "Italian"}


def bundled_model_dir() -> Path:
    packaged = project_root() / "models/Confucius4-R2T2-GGUF"
    return packaged if packaged.is_dir() else project_root() / ".runtime/confucius-models/Confucius4-R2T2-GGUF"


def model_dir() -> Path:
    custom = load_settings().get("confucius_model_dir")
    return Path(custom).expanduser().resolve() if custom else bundled_model_dir()


def runtime_dir() -> Path:
    packaged = project_root() / "confucius"
    return packaged if packaged.is_dir() else project_root() / ".runtime/confucius"


def vad_dir(models: Path) -> Path:
    custom = models / "FireRedVAD-ONNX"
    return custom if custom.is_dir() else bundled_model_dir() / "FireRedVAD-ONNX"


def validate_models(directory: Path) -> None:
    missing = [name for name in MODEL_FILES if not (directory / name).is_file() or (directory / name).stat().st_size <= 0]
    if missing:
        raise ValueError(f"Confucius 模型目录不完整：{directory}；缺少 {', '.join(missing)}。请选择直接包含 Q8 GGUF 和 projector 的目录。")
    vad = vad_dir(directory)
    if not all((vad / name).is_file() for name in VAD_FILES):
        raise ValueError(f"FireRedVAD 文件缺失：{vad}。请下载完整整合包或保留模型内的 FireRedVAD-ONNX 目录。")


def availability() -> dict[str, Any]:
    runtime = runtime_dir()
    reason = ""
    try:
        validate_models(model_dir())
        required = [runtime / "python/python.exe", runtime / "cuda/bin/cudart64_12.dll",
                    project_root() / "vendor/confucius-r2t2/bridge.py"]
        if not all(path.is_file() for path in required) or len(list((runtime / "native/python").rglob("qwen3asr_native*.pyd"))) != 1:
            reason = "独立 Confucius CUDA worker 缺失，请使用包含 Confucius 的完整整合包。"
    except ValueError as exc:
        reason = str(exc)
    return {"available": not reason, "reason": reason, "model_directory": str(model_dir()),
            "bundled_model": model_dir().resolve() == bundled_model_dir().resolve(),
            "requires_cuda": True, "model": "Confucius4-R2T2 Q8"}


def recognition_language(language: str | None) -> str:
    value = (language or "auto").strip()
    if value.lower() in {"auto", ""}:
        return "Auto"
    if value in LANGUAGES.values():
        return value
    key = value.lower().split("-")[0]
    if key not in LANGUAGES:
        raise ValueError("Confucius 不支持该识别语言；可选 auto、zh、en、yue、ja、ko、de、fr、ru、pt、es、it。")
    return LANGUAGES[key]


def _worker_manager(models: Path):
    source = project_root() / "vendor/confucius-r2t2"
    spec = importlib.util.spec_from_file_location("_t8_confucius_bridge", source / "bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = runtime_dir()

    class PortableWorkerManager(module.WorkerManager):
        def _start(self) -> None:
            self.port, self.token = self._port(), secrets.token_urlsafe(48)
            env = {key: os.environ[key] for key in
                   ("SYSTEMROOT", "WINDIR", "USERPROFILE", "TEMP", "TMP", "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER") if key in os.environ}
            # llama.cpp otherwise splits layers across every visible GPU. The
            # desktop uses CUDA device 0; expose only that same physical device.
            visible = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip()
            env["CUDA_VISIBLE_DEVICES"] = visible.split(",")[0].strip() if visible else "0"
            # No host Python, CUDA Toolkit or proxy is required by the child.
            env.update(R2T2_WORKER_TOKEN=self.token, PYTHONIOENCODING="utf-8",
                       PYTHONFAULTHANDLER="1", PYTHONNOUSERSITE="1",
                       CUDA_PATH=str(runtime / "cuda"), PATH=str(runtime / "cuda/bin") + os.pathsep + os.environ.get("SYSTEMROOT", r"C:\Windows") + r"\System32")
            log_path = user_data_dir() / "logs/confucius-worker.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = log_path.open("ab", buffering=0)
            self.process = subprocess.Popen(
                [str(runtime / "python/python.exe"), "-I", str(Path(__file__).with_name("confucius_worker.py")),
                 "--port", str(self.port), "--parent-pid", str(os.getpid()),
                 "--source", str(source), "--runtime", str(runtime),
                 "--models", str(models), "--vad", str(vad_dir(models))],
                cwd=str(runtime), env=env, stdin=subprocess.DEVNULL, stdout=self._log_file,
                stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            for _ in range(200):
                if self.process.poll() is not None:
                    raise module.WorkerError(f"Confucius worker 启动失败；请检查 NVIDIA 驱动和 {log_path}")
                try:
                    self.generation = self._request("GET", "/health", timeout=1, invalidate_on_failure=False)["generation"]
                    return
                except module.WorkerError:
                    time.sleep(0.1)
            raise module.WorkerError(f"Confucius worker 启动超时；见 {log_path}")

    # Importing the unchanged bridge creates an unused atexit manager. Avoid
    # accumulating registrations when a fresh short-lived worker is requested.
    module.atexit.unregister(module.manager.close)
    return PortableWorkerManager()


def transcribe(path: Path, *, language: str | None = None, hotwords: str = "", context: str = "") -> dict[str, Any]:
    language_name = recognition_language(language)
    if len(context) + len(hotwords) > 8192:
        raise ValueError("Confucius 上下文和热词合计不能超过 8192 字符。")
    available = availability()
    if not available["available"]:
        raise RuntimeError(available["reason"])
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    manager = _worker_manager(model_dir())
    try:
        return manager.transcribe(audio.astype("<f4", copy=False).tobytes(),
                                  {"sample_rate": int(rate), "channels": int(audio.shape[1]),
                                   "mode": "offline", "language": language_name,
                                   "hotwords": hotwords, "context": context, "auto_gain": False},
                                  {"gpu_layers": -1, "n_ctx": 8192, "n_batch": 1024,
                                   "n_threads": max(1, min(8, os.cpu_count() or 4))})
    finally:
        # Terminating the isolated process also releases native CUDA allocations
        # on errors; a warm model is not kept alongside the TTS model.
        manager.close()
