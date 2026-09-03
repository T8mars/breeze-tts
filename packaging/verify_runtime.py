from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import platform
import sys
from importlib import metadata
from pathlib import Path


EXPECTED = {
    "torch": "2.9.1",
    "torchaudio": "2.9.1",
    "transformers": "4.57.3",
    "qwen-tts": "0.1.1",
    "faster-whisper": "1.2.1",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    sys.path.insert(0, str(root))

    errors: list[str] = []
    try:
        manifest = json.loads((root / "manifests" / "desktop-runtime.json").read_text(encoding="utf-8"))
        lock = root / manifest["resolved_lock"]
        lock_bytes = lock.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        lock_hash = hashlib.sha256(lock_bytes).hexdigest()
        if lock_hash != manifest["resolved_lock_sha256"]:
            errors.append(
                f"runtime lock hash: expected {manifest['resolved_lock_sha256']}, got {lock_hash}"
            )
    except Exception as exc:
        errors.append(f"runtime manifest: {type(exc).__name__}: {exc}")
    if sys.version_info[:2] != (3, 10):
        errors.append(f"Python must be 3.10, got {platform.python_version()}")
    if platform.architecture()[0] != "64bit":
        errors.append("Python must be 64-bit")
    for package, expected in EXPECTED.items():
        try:
            actual = metadata.version(package)
        except metadata.PackageNotFoundError:
            errors.append(f"missing package: {package}")
            continue
        if actual.split("+")[0] != expected:
            errors.append(f"{package}: expected {expected}, got {actual}")

    for module in ("torch", "torchaudio", "transformers", "qwen_tts", "fastapi", "uvicorn", "websockets", "soundfile", "faster_whisper"):
        try:
            importlib.import_module(module)
        except Exception as exc:
            errors.append(f"import {module}: {type(exc).__name__}: {exc}")

    try:
        import torch
        cuda = {
            "available": torch.cuda.is_available(),
            "runtime": torch.version.cuda,
            "device_count": torch.cuda.device_count(),
        }
    except Exception:
        cuda = {"available": False}
    print(json.dumps({"python": platform.python_version(), "cuda": cuda, "errors": errors}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
