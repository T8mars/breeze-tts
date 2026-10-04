"""Browser acceptance of the desktop ASR controls using the real backend."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--report-directory", type=Path, required=True)
    parser.add_argument("--engine", choices=("whisper", "confucius"), default="confucius")
    parser.add_argument("--browser-channel", default=None,
        help="Optional installed browser channel; defaults to Playwright Chromium.")
    parser.add_argument("--isolate-runtime", action="store_true",
        help="Remove host Python/CUDA settings and keep only bundled Python plus Windows system PATH.")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    root = args.project_root.resolve()
    args.report_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="t8-asr-ui-") as temporary:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {**os.environ, "T8_BREEZE_DATA_DIR": temporary, "PYTHONPATH": str(root), "PYTHONUTF8": "1"}
        env.pop("PYTHONHOME", None)
        if args.isolate_runtime:
            for name in list(env):
                if name.startswith("CUDA_PATH") or name in ("VIRTUAL_ENV", "PYTHONUSERBASE"):
                    env.pop(name)
            windows = Path(env.get("SYSTEMROOT", r"C:\Windows"))
            env["PATH"] = os.pathsep.join(map(str, (args.python.resolve().parent, windows / "System32", windows)))
            env["PYTHONNOUSERSITE"] = "1"
        log = (args.report_directory / "backend.log").open("w", encoding="utf-8")
        server = subprocess.Popen([str(args.python), "-m", "t8_runtime.server", "--port", str(port)],
            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        base = f"http://127.0.0.1:{port}"
        try:
            http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(120):
                try:
                    with http.open(base + "/api/health", timeout=1) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError("UI backend exited; inspect backend.log")
                    time.sleep(0.5)
            else:
                raise RuntimeError("UI backend startup timed out")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel=args.browser_channel, headless=True)
                page = browser.new_page(viewport={"width": 1366, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base)
                page.wait_for_function("engine => state.capabilities[engine] === true", arg=args.engine)
                # Only expose the ASR workbench for this component test; no TTS
                # generation or license acknowledgement is bypassed in the app.
                page.evaluate("() => { document.getElementById('studioView').hidden=false; document.getElementById('launcherView').hidden=true; }")
                page.locator('[data-mode="clone"]').click()
                page.locator("#referenceAudio").set_input_files(args.audio)
                page.locator("#whisperModel").select_option("large-v3" if args.engine == "whisper" else "confucius")
                page.locator("#transcribeButton").click()
                page.wait_for_function("() => !document.getElementById('whisperDraftPanel').hidden", timeout=180000)
                assert page.locator("#whisperDraftText").input_value().strip()
                assert page.locator("#referenceText").input_value() == ""
                assert not page.locator("#referenceTranscriptVerified").is_checked()
                assert "语言置信度 0%" not in page.locator("#whisperDraftQuality").inner_text()
                page.locator("#applyWhisperDraftButton").click()
                assert page.locator("#referenceText").input_value().strip()
                assert not page.locator("#referenceTranscriptVerified").is_checked()
                page.screenshot(path=str(args.report_directory / f"generation-{args.engine}.png"), full_page=True)
                page.locator("#workspaceTabVoices").click()
                page.locator("#voiceReferenceAudio").set_input_files(args.audio)
                page.locator("#voiceAsrEngine").select_option(args.engine)
                page.locator("#voiceTranscribeButton").click()
                page.wait_for_function("() => !document.getElementById('voiceWhisperDraftPanel').hidden", timeout=180000)
                assert page.locator("#voiceWhisperDraftText").input_value().strip()
                assert page.locator("#voiceReferenceText").input_value() == ""
                assert not page.locator("#voiceTranscriptVerified").is_checked()
                page.locator("#workspaceTabSettings").click()
                model_field = "#whisperModelPath" if args.engine == "whisper" else "#confuciusModelPath"
                save_button = "#saveWhisperPathButton" if args.engine == "whisper" else "#saveConfuciusPathButton"
                custom_path = page.locator(model_field).input_value()
                page.locator(save_button).click()
                page.wait_for_function("() => document.getElementById('asrSettingsStatus').textContent.includes('路径已保存')")
                assert page.locator(model_field).input_value() == custom_path
                page.screenshot(path=str(args.report_directory / "settings-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 2")
                page.screenshot(path=str(args.report_directory / "settings-mobile.png"), full_page=True)
                assert not errors, errors
                browser.close()
                report = {f"generation_{args.engine}": True, f"voice_library_{args.engine}": True,
                    "server_runtime_isolated": args.isolate_runtime,
                    "manual_transcript_verification_preserved": True, "path_save": True,
                    "mobile_no_horizontal_overflow": True, "page_errors": errors}
                (args.report_directory / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(json.dumps(report, indent=2))
        finally:
            server.terminate()
            server.wait(timeout=15)
            log.close()


if __name__ == "__main__":
    main()
