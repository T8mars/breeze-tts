from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is required to verify a pinned source object")
def test_source_pin_ignores_dirty_python_but_rejects_dirty_native(tmp_path):
    script = Path(__file__).resolve().parents[1] / "packaging/verify_confucius_runtime.py"
    spec = importlib.util.spec_from_file_location("verify_confucius_source_pin", script)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    original = tmp_path / "original"
    snapshot = tmp_path / "desktop/vendor/confucius-r2t2"
    original.mkdir()
    files = {"bridge.py": "# pinned bridge\n",
             "r2t2_core/worker.py": "# pinned worker\n",
             "r2t2_native/native_ext.cpp": "// pinned native\n"}
    for name, contents in files.items():
        local_name = "vendor/" + name if name.startswith("r2t2_native/") else name
        for destination in (original / local_name, snapshot / name):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(contents, encoding="utf-8")

    def git(*args):
        return subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(original), *args],
                              check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()

    git("init")
    git("add", ".")
    git("-c", "user.name=Portable Pin Test", "-c", "user.email=pin-test@example.invalid",
        "commit", "-m", "Pinned fixture")
    commit = git("rev-parse", "HEAD")
    manifest = {"source": {"commit": commit, "vendor_directory": "vendor/confucius-r2t2",
                            "files": {name: verifier.source_text_sha256(contents)
                                      for name, contents in files.items()}}}
    (original / "r2t2_core/worker.py").write_text("# unrelated in-progress Python edit\n", encoding="utf-8")
    errors = []
    verifier.verify_source(tmp_path / "desktop", manifest, errors, original, commit)
    assert errors == []
    assert (snapshot / "r2t2_core/worker.py").read_text(encoding="utf-8") == files["r2t2_core/worker.py"]
    (original / "vendor/r2t2_native/native_ext.cpp").write_text("// changed build input\n", encoding="utf-8")
    verifier.verify_source(tmp_path / "desktop", manifest, errors, original, commit)
    assert errors == ["original source checksum mismatch: r2t2_native/native_ext.cpp"]
