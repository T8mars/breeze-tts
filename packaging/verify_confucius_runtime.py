"""Verify portable Confucius assets without loading a model onto the GPU."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import platform
import struct
import sys
from importlib import metadata
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_asset(root: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ValueError(f"Unsafe manifest asset path: {name}")
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Asset escapes its root: {name}")
    if not path.is_file():
        raise ValueError(f"Asset is not a regular file: {name}")
    return path


def verify_source(root: Path, manifest: dict, errors: list[str], external_source: Path | None = None) -> None:
    source = manifest["source"]
    vendor = root / source["vendor_directory"]
    for name, expected in source["files"].items():
        try:
            actual = source_sha256(resolve_asset(vendor, name))
            if actual != expected:
                errors.append(f"source checksum mismatch: {name}")
            if external_source is not None:
                external_name = "vendor/" + name if name.startswith("r2t2_native/") else name
                if source_sha256(resolve_asset(external_source, external_name)) != expected:
                    errors.append(f"original source checksum mismatch: {name}")
        except (OSError, ValueError) as exc:
            errors.append(f"source {name}: {exc}")


def verify_models(model_root: Path, manifest: dict, errors: list[str]) -> None:
    for item in manifest["models"]["files"]:
        name = item["path"]
        try:
            path = resolve_asset(model_root, name)
            if path.stat().st_size != item["size"]:
                errors.append(f"model size mismatch: {name}")
                continue
            if name.endswith(".gguf"):
                with path.open("rb") as stream:
                    if struct.unpack("<4sI", stream.read(8)) != (b"GGUF", 3):
                        errors.append(f"model GGUF header mismatch: {name}")
            if sha256(path) != item["sha256"]:
                errors.append(f"model checksum mismatch: {name}")
        except (OSError, ValueError, struct.error) as exc:
            errors.append(f"model {name}: {exc}")


def runtime_files(runtime_root: Path, inventory_name: str):
    for path in sorted(runtime_root.rglob("*")):
        if path.is_file() and path.name not in (inventory_name, inventory_name + ".tmp") and "__pycache__" not in path.parts:
            yield path


def verify_inventory(runtime_root: Path, manifest: dict, errors: list[str]) -> int:
    inventory_name = manifest["paths"]["runtime_inventory"]
    try:
        inventory = json.loads((runtime_root / inventory_name).read_text(encoding="utf-8"))
        if inventory.get("source_commit") != manifest["source"]["commit"]:
            errors.append("runtime inventory source commit mismatch")
        if inventory.get("llama_cpp_commit") != manifest["native"]["llama_cpp_commit"]:
            errors.append("runtime inventory llama.cpp commit mismatch")
        if set(inventory.get("cuda_architectures", [])) != set(manifest["native"]["cuda_architectures"]):
            errors.append("runtime inventory CUDA architecture coverage mismatch")
        items = inventory["files"]
        expected_paths = {item["path"] for item in items}
        actual_paths = {path.relative_to(runtime_root).as_posix() for path in runtime_files(runtime_root, inventory_name)}
        if actual_paths != expected_paths:
            errors.append(f"runtime inventory file set mismatch: missing={len(expected_paths - actual_paths)}, extra={len(actual_paths - expected_paths)}")
        for item in items:
            try:
                path = resolve_asset(runtime_root, item["path"])
                if path.stat().st_size != item["size"] or sha256(path) != item["sha256"]:
                    errors.append(f"runtime checksum mismatch: {item['path']}")
            except (OSError, ValueError) as exc:
                errors.append(f"runtime {item['path']}: {exc}")
        return len(items)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"runtime inventory: {exc}")
        return 0


def write_inventory(runtime_root: Path, manifest: dict, architectures: list[str]) -> None:
    if set(architectures) != set(manifest["native"]["cuda_architectures"]):
        raise ValueError("Built CUDA architectures must match the manifest")
    name = manifest["paths"]["runtime_inventory"]
    inventory = {
        "schema_version": 1,
        "source_commit": manifest["source"]["commit"],
        "llama_cpp_commit": manifest["native"]["llama_cpp_commit"],
        "cuda_architectures": architectures,
        "files": [{"path": path.relative_to(runtime_root).as_posix(),
                   "size": path.stat().st_size, "sha256": sha256(path)}
                  for path in runtime_files(runtime_root, name)],
    }
    temporary = runtime_root / (name + ".tmp")
    temporary.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    temporary.replace(runtime_root / name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--write-inventory", action="store_true")
    parser.add_argument("--cuda-architectures", default="")
    args = parser.parse_args()
    root = args.project_root.resolve()
    manifest = json.loads((root / "manifests/confucius-runtime.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    verify_source(root, manifest, errors, args.source_root)
    if args.source_only:
        print(json.dumps({"source_commit": manifest["source"]["commit"], "errors": errors}, indent=2))
        return bool(errors)

    paths = manifest["paths"]
    runtime_root = (args.runtime_root or next((root / paths[key] for key in ("runtime_packaged", "runtime_source")
                                               if (root / paths[key] / "python/python.exe").is_file()),
                                              root / paths["runtime_source"])).resolve()
    model_root = (args.model_root or next((root / paths[key] for key in ("model_packaged", "model_source")
                                           if (root / paths[key]).is_dir()),
                                          root / paths["model_source"])).resolve()
    python_root = runtime_root / "python"
    if sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        errors.append(f"Worker must be 64-bit CPython 3.12, got {platform.python_version()}")
    if Path(sys.prefix).resolve() != python_root or Path(sys.base_prefix).resolve() != python_root:
        errors.append("Worker interpreter still depends on an external Python/venv")
    if (python_root / "pyvenv.cfg").exists():
        errors.append("Portable Python must not contain pyvenv.cfg")
    for package, version in manifest["python"]["dependencies"].items():
        try:
            if metadata.version(package) != version:
                errors.append(f"worker package version mismatch: {package}")
        except metadata.PackageNotFoundError:
            errors.append(f"worker package missing: {package}")
    for module in manifest["python"]["imports"]:
        try:
            importlib.import_module(module)
        except Exception as exc:
            errors.append(f"import {module}: {type(exc).__name__}: {exc}")
    for area, key in ((runtime_root / "native/bin/Release", "dlls"),
                      (runtime_root / "cuda/bin", "cuda_dlls"),
                      (python_root, "vc_dlls")):
        for name in manifest["native"][key]:
            if not (area / name).is_file():
                errors.append(f"native runtime missing: {name}")
    for name in ("CUDA-12.8-EULA.txt", "llama.cpp-LICENSE", "Microsoft-REDIST.txt", "Microsoft-ThirdPartyNotices.txt"):
        if not (runtime_root / "licenses" / name).is_file():
            errors.append(f"runtime license missing: {name}")
    verify_models(model_root, manifest, errors)
    native_import = False
    try:
        os.environ["CUDA_PATH"] = str(runtime_root / "cuda")
        sys.path.insert(0, str(root / manifest["source"]["vendor_directory"]))
        from r2t2_core.native import _load_extension

        native = _load_extension(runtime_root / "native")
        native_import = hasattr(native, "Qwen3ASRNative")
        if not native_import:
            errors.append("Native extension does not expose Qwen3ASRNative")
    except Exception as exc:
        errors.append(f"native import: {type(exc).__name__}: {exc}")
    if args.write_inventory and not errors:
        try:
            write_inventory(runtime_root, manifest, args.cuda_architectures.split(";"))
        except (OSError, ValueError) as exc:
            errors.append(f"write runtime inventory: {exc}")
    count = verify_inventory(runtime_root, manifest, errors)
    print(json.dumps({"python": platform.python_version(), "runtime_root": str(runtime_root),
                      "model_root": str(model_root), "native_import": native_import,
                      "source_commit": manifest["source"]["commit"],
                      "verified_runtime_files": count, "verified_model_files": len(manifest["models"]["files"]),
                      "errors": errors}, indent=2))
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
