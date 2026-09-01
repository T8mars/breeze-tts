from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import MODEL_REPOSITORY, MODEL_REVISION, project_root


MANIFEST_PATH = project_root() / "manifests" / "breeze-tts-2-model.json"
LICENSE_ACCEPTANCE_FILE = ".t8-license-accepted.json"
MODEL_COMPLETE_FILE = ".t8-model-complete.json"


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _sha256(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_sha256() -> str:
    return _sha256(MANIFEST_PATH)


def _license_marker_valid(model_dir: Path) -> bool:
    marker = model_dir / LICENSE_ACCEPTANCE_FILE
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        payload.get("model") == MODEL_REPOSITORY
        and payload.get("revision") == MODEL_REVISION
        and payload.get("scope") == "research-and-non-commercial"
    )


def _model_complete_marker_valid(model_dir: Path, manifest: dict[str, Any]) -> bool:
    marker = model_dir / MODEL_COMPLETE_FILE
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not (
        payload.get("model") == MODEL_REPOSITORY
        and payload.get("revision") == MODEL_REVISION
        and payload.get("verification") == "size-and-sha256"
        and payload.get("manifest_sha256") == _manifest_sha256()
    ):
        return False
    recorded = payload.get("files")
    if not isinstance(recorded, dict) or len(recorded) != len(manifest["files"]):
        return False
    for entry in manifest["files"]:
        relative = str(entry["path"])
        target = model_dir / relative
        details = recorded.get(relative)
        if not target.is_file() or not isinstance(details, dict):
            return False
        stat = target.stat()
        if (
            int(details.get("size", -1)) != stat.st_size
            or int(details.get("mtime_ns", -1)) != stat.st_mtime_ns
        ):
            return False
    return True


def validate_model_dir(model_dir: Path, *, verify_hashes: bool = False) -> dict[str, Any]:
    model_dir = Path(model_dir).expanduser().resolve()
    manifest = load_manifest()
    missing: list[str] = []
    size_mismatch: list[dict[str, Any]] = []
    hash_mismatch: list[dict[str, str]] = []
    checked_bytes = 0
    for entry in manifest["files"]:
        relative = entry["path"]
        target = model_dir / relative
        if not target.is_file():
            missing.append(relative)
            continue
        actual_size = target.stat().st_size
        expected_size = int(entry["size"])
        checked_bytes += actual_size
        if expected_size and actual_size != expected_size:
            size_mismatch.append(
                {"path": relative, "expected": expected_size, "actual": actual_size}
            )
            continue
        expected_hash = str(entry.get("sha256") or "")
        if verify_hashes and len(expected_hash) == 64:
            actual_hash = _sha256(target)
            if actual_hash.lower() != expected_hash.lower():
                hash_mismatch.append(
                    {"path": relative, "expected": expected_hash, "actual": actual_hash}
                )
    accepted = _license_marker_valid(model_dir)
    complete_marker_exists = (model_dir / MODEL_COMPLETE_FILE).is_file()
    complete_marker = _model_complete_marker_valid(model_dir, manifest)
    return {
        "path": str(model_dir),
        "valid": not missing and not size_mismatch and not hash_mismatch,
        "license_accepted": accepted,
        "download_complete_marker": complete_marker,
        "download_complete_marker_exists": complete_marker_exists,
        "integrity_verified": bool(complete_marker or (verify_hashes and not hash_mismatch)),
        "revision": manifest["revision"],
        "total_size": int(manifest["total_size"]),
        "checked_bytes": checked_bytes,
        "missing": missing,
        "size_mismatch": size_mismatch,
        "hash_mismatch": hash_mismatch,
    }


def record_license_acceptance(model_dir: Path) -> Path:
    model_dir = Path(model_dir).expanduser().resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    target = model_dir / LICENSE_ACCEPTANCE_FILE
    payload = {
        "model": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
        "accepted_at_unix": int(time.time()),
        "scope": "research-and-non-commercial",
    }
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def record_model_complete(model_dir: Path) -> Path:
    model_dir = Path(model_dir).expanduser().resolve()
    target = model_dir / MODEL_COMPLETE_FILE
    manifest = load_manifest()
    files = {}
    for entry in manifest["files"]:
        relative = str(entry["path"])
        stat = (model_dir / relative).stat()
        files[relative] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    payload = {
        "model": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
        "verified_at_unix": int(time.time()),
        "verification": "size-and-sha256",
        "manifest_sha256": _manifest_sha256(),
        "files": files,
    }
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def ensure_model_integrity(model_dir: Path, *, force: bool = False) -> dict[str, Any]:
    """Use the verified marker fast path, otherwise perform the one-time full hash pass."""
    model_dir = Path(model_dir).expanduser().resolve()
    report = validate_model_dir(model_dir, verify_hashes=False)
    if not report["valid"]:
        return report
    if report["integrity_verified"] and not force:
        return report
    report = validate_model_dir(model_dir, verify_hashes=True)
    if report["valid"]:
        record_model_complete(model_dir)
        report = validate_model_dir(model_dir, verify_hashes=False)
    return report


@dataclass
class DownloadState:
    status: str = "idle"
    message: str = "尚未开始下载。"
    downloaded_bytes: int = 0
    total_bytes: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    model_dir: str | None = None
    cancel_requested: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        elapsed = (self.finished_at or time.time()) - self.started_at if self.started_at else 0.0
        speed = self.downloaded_bytes / elapsed if elapsed > 0 else 0.0
        remaining = max(0, self.total_bytes - self.downloaded_bytes)
        return {
            **self.__dict__,
            "elapsed_seconds": elapsed,
            "bytes_per_second": speed,
            "eta_seconds": remaining / speed if speed > 0 else None,
        }


class ModelDownloadManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = DownloadState()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._state.as_dict()

    def start(self, model_dir: Path, *, accepted: bool) -> dict[str, Any]:
        if not accepted:
            raise PermissionError("必须先阅读并接受 BreezeBlue 模型许可证。")
        model_dir = Path(model_dir).expanduser().resolve()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("模型下载已经在运行。")
            manifest = load_manifest()
            model_dir.mkdir(parents=True, exist_ok=True)
            visible = self._visible_bytes(model_dir)
            required = max(0, int(manifest["total_size"]) - visible) + 1024**3
            free = shutil.disk_usage(model_dir).free
            if free < required:
                raise OSError(
                    f"磁盘空间不足：至少需要 {required / 1024**3:.2f} GiB，"
                    f"当前可用 {free / 1024**3:.2f} GiB。"
                )
            record_license_acceptance(model_dir)
            self._state = DownloadState(
                status="running",
                message="正在从 Hugging Face 下载并校验官方模型…",
                total_bytes=int(manifest["total_size"]),
                started_at=time.time(),
                model_dir=str(model_dir),
            )
            self._thread = threading.Thread(
                target=self._worker, args=(model_dir,), name="breeze-model-download", daemon=True
            )
            self._thread.start()
            return self._state.as_dict()

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            if self._state.status == "running":
                self._state.cancel_requested = True
                self._state.message = "正在取消当前下载；已完成内容可继续使用。"
            return self._state.as_dict()

    def _visible_bytes(self, model_dir: Path) -> int:
        manifest = load_manifest()
        total = 0
        for entry in manifest["files"]:
            target = model_dir / entry["path"]
            if target.is_file():
                total += min(target.stat().st_size, int(entry["size"]))
        cache = model_dir / ".cache" / "huggingface" / "download"
        if cache.is_dir():
            total += sum(path.stat().st_size for path in cache.rglob("*.incomplete") if path.is_file())
        return min(total, int(manifest["total_size"]))

    def _worker(self, model_dir: Path) -> None:
        try:
            manifest = load_manifest()
            for index, entry in enumerate(manifest["files"], start=1):
                with self._lock:
                    if self._state.cancel_requested:
                        self._state.status = "cancelled"
                        self._state.message = "下载已取消；已下载内容可在下次继续。"
                        self._state.finished_at = time.time()
                        return
                    self._state.message = f"下载 {index}/{len(manifest['files'])}：{entry['path']}"
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "t8_runtime.download_worker",
                        "--repo-id",
                        str(manifest["repo_id"]),
                        "--filename",
                        str(entry["path"]),
                        "--revision",
                        str(manifest["revision"]),
                        "--local-dir",
                        str(model_dir),
                    ],
                    cwd=str(project_root()),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                with self._lock:
                    self._process = process
                while process.poll() is None:
                    time.sleep(0.2)
                    with self._lock:
                        self._state.downloaded_bytes = self._visible_bytes(model_dir)
                        if self._state.cancel_requested:
                            process.terminate()
                            try:
                                process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=5)
                            self._process = None
                            self._state.status = "cancelled"
                            self._state.message = "下载已取消；已下载内容可在下次继续。"
                            self._state.finished_at = time.time()
                            return
                with self._lock:
                    self._process = None
                if process.returncode != 0:
                    raise RuntimeError(
                        f"模型文件下载失败（退出代码 {process.returncode}）：{entry['path']}"
                    )
                with self._lock:
                    self._state.downloaded_bytes = self._visible_bytes(model_dir)
            report = validate_model_dir(model_dir, verify_hashes=True)
            if not report["valid"]:
                raise RuntimeError(f"模型下载后校验失败：{report}")
            record_model_complete(model_dir)
            with self._lock:
                self._state.status = "completed"
                self._state.message = "模型下载和 SHA-256 校验完成。"
                self._state.downloaded_bytes = self._state.total_bytes
                self._state.finished_at = time.time()
                self._state.details = report
        except Exception as exc:
            with self._lock:
                self._process = None
                self._state.status = "failed"
                self._state.message = "模型下载失败；可修复后继续下载。"
                self._state.error = f"{type(exc).__name__}: {exc}"
                self._state.downloaded_bytes = self._visible_bytes(model_dir)
                self._state.finished_at = time.time()


DOWNLOAD_MANAGER = ModelDownloadManager()
