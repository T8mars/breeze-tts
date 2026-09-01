"""Submit all shipped API workflows to a real ComfyUI server and verify audio outputs."""

from __future__ import annotations

import argparse
import json
import shutil
import time
import urllib.request
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf


NODE_IDS = {
    "T8_BreezeTTS_ModelLoader",
    "T8_BreezeTTS_DesignRequest",
    "T8_BreezeTTS_CloneRequest",
    "T8_BreezeTTS_DirectionRequest",
    "T8_BreezeTTS_GenerationSettings",
    "T8_BreezeTTS_Generate",
}


def request_json(url: str, *, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def audio_path(base: Path, item: dict) -> Path:
    roots = {"output": base / "output", "temp": base / "temp", "input": base / "input"}
    root = roots.get(item.get("type"))
    if root is None:
        raise AssertionError(f"unknown ComfyUI audio type: {item}")
    return root / str(item.get("subfolder") or "") / item["filename"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8290")
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()

    object_info = request_json(f"{args.url}/object_info")
    discovered = NODE_IDS & set(object_info)
    if discovered != NODE_IDS:
        raise AssertionError(f"missing T8 nodes: {sorted(NODE_IDS - discovered)}")

    def configure_loader(workflow: dict) -> None:
        workflow["1"]["inputs"].update(
            {
                "dtype": "bf16",
                "device": "cuda",
                "attention": "eager",
                "download_if_missing": False,
                "accept_model_license": True,
            }
        )

    def submit(workflow: dict) -> tuple[str, dict]:
        queued = request_json(
            f"{args.url}/prompt",
            payload={"prompt": workflow, "client_id": f"t8-{uuid.uuid4().hex}"},
        )
        if queued.get("node_errors"):
            raise AssertionError(f"workflow validation failed: {queued['node_errors']}")
        prompt_id = queued["prompt_id"]
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            history = request_json(f"{args.url}/history/{prompt_id}")
            history_item = history.get(prompt_id)
            if history_item is not None:
                status = history_item.get("status", {})
                if status.get("status_str") != "success" or not status.get("completed"):
                    raise AssertionError(f"ComfyUI workflow failed: {status}")
                return prompt_id, history_item
            time.sleep(1.0)
        raise TimeoutError(f"ComfyUI workflow did not finish within {args.timeout} seconds")

    def verify_outputs(mode: str, workflow: dict, history_item: dict, node_ids: tuple[str, ...]) -> list[dict]:
        facts = []
        for node_id in node_ids:
            node_output = history_item.get("outputs", {}).get(node_id, {})
            audio_items = node_output.get("audio", [])
            if not audio_items:
                raise AssertionError(f"output node {node_id} returned no audio metadata: {node_output}")
            target = audio_path(args.base_dir.resolve(), audio_items[0])
            audio, rate = sf.read(target, dtype="float32", always_2d=True)
            if rate != 24_000 or audio.shape[1] != 1 or audio.shape[0] <= 0 or not np.isfinite(audio).all():
                raise AssertionError(f"invalid ComfyUI audio from node {node_id}: {target}, {rate}, {audio.shape}")
            facts.append(
                {
                    "mode": mode,
                    "node": node_id,
                    "class_type": workflow[node_id]["class_type"],
                    "path": str(target),
                    "sample_rate": rate,
                    "samples": int(audio.shape[0]),
                }
            )
        return facts

    reference_text = "ComfyUI API integration test."
    design = json.loads(args.workflow.read_text(encoding="utf-8"))
    configure_loader(design)
    design["2"]["inputs"].update(
        {"text": reference_text, "voice_description": "A clear neutral voice."}
    )
    design["3"]["inputs"].update({"max_new_tokens": 64, "seed": 4242})
    design_prompt, design_history = submit(design)
    output_facts = verify_outputs("design", design, design_history, ("5", "6"))

    reference_name = "t8_api_reference.flac"
    reference_target = args.base_dir.resolve() / "input" / reference_name
    reference_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(output_facts[0]["path"]), reference_target)

    clone_path = args.workflow.with_name("voice_clone_api.json")
    clone = json.loads(clone_path.read_text(encoding="utf-8"))
    configure_loader(clone)
    clone["2"]["inputs"].update({"audio": reference_name, "audioUI": ""})
    clone["3"]["inputs"].update(
        {
            "text": "The cloned voice is working.",
            "reference_text": reference_text,
            "cfg_scale": 1.0,
        }
    )
    clone["4"]["inputs"].update({"max_new_tokens": 64, "seed": 4243})
    clone_prompt, clone_history = submit(clone)
    output_facts.extend(verify_outputs("clone", clone, clone_history, ("6",)))

    direction_path = args.workflow.with_name("voice_direction_api.json")
    direction = json.loads(direction_path.read_text(encoding="utf-8"))
    configure_loader(direction)
    direction["2"]["inputs"].update({"audio": reference_name, "audioUI": ""})
    direction["3"]["inputs"].update(
        {
            "text": "Please speak slowly and seriously.",
            "reference_text": reference_text,
            "direction": "Speak slowly with restrained seriousness.",
            "cfg_scale": 4.0,
        }
    )
    direction["4"]["inputs"].update({"max_new_tokens": 64, "seed": 4244})
    direction_prompt, direction_history = submit(direction)
    output_facts.extend(verify_outputs("direction", direction, direction_history, ("6",)))

    report = {
        "status": "passed",
        "timestamp_unix": int(time.time()),
        "server": args.url,
        "prompt_ids": {
            "design": design_prompt,
            "clone": clone_prompt,
            "direction": direction_prompt,
        },
        "node_count": len(discovered),
        "category": object_info["T8_BreezeTTS_Generate"]["category"],
        "modes": ["design", "clone", "direction"],
        "outputs": output_facts,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
