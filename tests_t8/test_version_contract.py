from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

from t8_runtime.config import PROJECT_VERSION


ROOT = Path(__file__).resolve().parents[1]


def _toml_project_version(text: str) -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', text, re.MULTILINE)
    assert match, "ComfyUI pyproject.toml is missing a project version"
    return match.group(1)


def test_release_version_is_consistent_across_desktop_backend_and_comfy() -> None:
    desktop_package = json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
    desktop_version = desktop_package["version"]
    lock_package = json.loads((ROOT / "desktop" / "package-lock.json").read_text(encoding="utf-8"))
    comfy_version = _toml_project_version(
        (ROOT / "comfyui-breeze-tts-T8" / "pyproject.toml").read_text(encoding="utf-8")
    )
    comfy_entrypoint = (ROOT / "comfyui-breeze-tts-T8" / "__init__.py").read_text(encoding="utf-8")
    desktop_html = (ROOT / "desktop" / "src" / "index.html").read_text(encoding="utf-8")

    assert desktop_version == PROJECT_VERSION == comfy_version
    assert lock_package["version"] == desktop_version
    assert lock_package["packages"][""]["version"] == desktop_version
    assert f'__version__ = "{comfy_version}"' in comfy_entrypoint
    assert f"DESKTOP {desktop_version}" in desktop_html
    assert f"styles.css?v={desktop_version}" in desktop_html
    assert f"renderer.js?v={desktop_version}" in desktop_html


def test_runtime_lock_hash_is_canonical_across_line_endings() -> None:
    manifest = json.loads((ROOT / "manifests" / "desktop-runtime.json").read_text(encoding="utf-8"))
    lock_bytes = (ROOT / manifest["resolved_lock"]).read_bytes()
    canonical = lock_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    assert hashlib.sha256(canonical).hexdigest() == manifest["resolved_lock_sha256"]
