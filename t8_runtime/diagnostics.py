from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import CORE_REVISION, MODEL_REVISION, PROJECT_VERSION


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _gpu_report() -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total,memory.free,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        devices = []
        for index, line in enumerate(completed.stdout.splitlines()):
            values = [item.strip() for item in line.split(",")]
            if len(values) >= 5:
                devices.append(
                    {
                        "index": index,
                        "name": values[0],
                        "driver": values[1],
                        "memory_total_mib": int(float(values[2])),
                        "memory_free_mib": int(float(values[3])),
                        "compute_capability": values[4],
                    }
                )
        return {"available": bool(devices), "devices": devices}
    except Exception as exc:
        return {"available": False, "devices": [], "reason": str(exc)}


def collect_diagnostics() -> dict[str, Any]:
    package_names = [
        "torch",
        "torchaudio",
        "triton-windows",
        "transformers",
        "qwen-tts",
        "accelerate",
        "numpy",
        "soundfile",
        "fastapi",
        "websockets",
    ]
    return {
        "project_version": PROJECT_VERSION,
        "core_revision": CORE_REVISION,
        "model_revision": MODEL_REVISION,
        "platform": platform.platform(),
        "python": {
            "version": sys.version.split()[0],
            "executable": str(Path(sys.executable).resolve()),
            "architecture": platform.architecture()[0],
        },
        "packages": {name: _package_version(name) for name in package_names},
        "gpu": _gpu_report(),
        "environment": {
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "offline": os.environ.get("HF_HUB_OFFLINE") == "1",
        },
    }


def write_redacted_report(path: Path) -> Path:
    report = collect_diagnostics()
    report["python"]["executable"] = Path(report["python"]["executable"]).name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
