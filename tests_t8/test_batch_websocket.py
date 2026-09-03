from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from t8_runtime import server
from t8_runtime.workspace_store import (
    load_project,
    record_project_line_result,
    save_project,
)


class StubRuntime:
    def __init__(self, model_dir: Path, output_root: Path) -> None:
        self.model_dir = model_dir
        self.output_root = output_root
        self.calls: list[str] = []
        self.requests: list[object] = []
        self.block_on_text: set[str] = set()
        self.fail_on_text: set[str] = set()
        self.generate_hook: Callable[[str], None] | None = None

    def status(self) -> dict[str, object]:
        return {"loaded": True, "model_dir": str(self.model_dir)}

    def generate(
        self,
        request,
        *,
        cancel_event: threading.Event | None = None,
        on_chunk=None,
    ) -> tuple[Path, dict[str, object]]:
        self.calls.append(request.text)
        self.requests.append(request)
        if self.generate_hook is not None:
            hook, self.generate_hook = self.generate_hook, None
            hook(request.text)
        if request.text in self.fail_on_text:
            raise RuntimeError("stub generation failure")
        if request.text in self.block_on_text:
            assert cancel_event is not None
            cancel_event.wait(timeout=5)
            if cancel_event.is_set():
                raise InterruptedError("stub generation cancelled")
            raise AssertionError("test did not cancel the blocked stub generation")
        self.output_root.mkdir(parents=True, exist_ok=True)
        target = self.output_root / f"line-{len(self.calls)}.wav"
        sf.write(target, np.full(1_200, 0.1, dtype=np.float32), 24_000, subtype="PCM_16")
        return target, {"output": str(target), "text": request.text}


def _batch_app(monkeypatch, tmp_path: Path) -> tuple[object, StubRuntime, list[list[int]]]:
    data_root = tmp_path / "data"
    output_root = tmp_path / "outputs"
    model_root = tmp_path / "models"
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(data_root))
    monkeypatch.setenv("T8_BREEZE_OUTPUT_DIR", str(output_root))
    runtime = StubRuntime(model_root, output_root)
    monkeypatch.setattr(server, "RuntimeManager", lambda _model_dir: runtime)
    monkeypatch.setattr(
        server,
        "validate_model_dir",
        lambda *_args, **_kwargs: {"valid": True, "license_accepted": True},
    )
    merged_indexes: list[list[int]] = []

    def merge_stub(results, *, timeline: bool, timing_policy: str = "preserve"):
        merged_indexes.append([int(item["index"]) for item in results])
        target = output_root / "merged.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"merged stub wav")
        return target, {
            "output": str(target),
            "item_count": len(results),
            "timeline": timeline,
            "timing_policy": timing_policy,
        }

    monkeypatch.setattr(server, "merge_batch_outputs", merge_stub)
    return server.create_app(model_root), runtime, merged_indexes


def _payload(*texts: str) -> dict[str, object]:
    return {
        "defaults": {"mode": "design", "instruction": "平静、自然"},
        "items": [{"text": text} for text in texts],
    }


def _saved_project(client: TestClient, *texts: str) -> dict[str, object]:
    project = client.post("/api/projects/new").json()
    project["name"] = "WebSocket 单句重跑"
    project["lines"] = [
        {
            "text": text,
            "start_ms": index * 200,
            "end_ms": index * 200 + 100,
        }
        for index, text in enumerate(texts)
    ]
    response = client.put(
        f"/api/projects/{project['project_id']}",
        json={"project": project, "expected_revision": project["revision"]},
    )
    assert response.status_code == 200
    return response.json()


def _line_payload(project: dict[str, object], line_index: int) -> dict[str, object]:
    line = project["lines"][line_index]
    return {
        "project_id": project["project_id"],
        "project_revision": project["revision"],
        "defaults": {"mode": "design", "instruction": "平静、自然"},
        "items": [{
            **line,
            "subtitle": {
                "index": line["order"],
                "start_ms": line["start_ms"],
                "end_ms": line["end_ms"],
                "text": line["text"],
            },
        }],
        "timeline": True,
        "timing_policy": "overlay",
    }


def _run_batch(client: TestClient, payload: dict[str, object]) -> list[dict[str, object]]:
    messages = []
    with client.websocket_connect(
        "/ws/batch", headers={"host": "127.0.0.1"}
    ) as websocket:
        websocket.send_json(payload)
        while True:
            message = websocket.receive_json()
            messages.append(message)
            if message["type"] in {"batch_complete", "cancelled", "error"}:
                break
    return messages


def test_batch_websocket_generates_first_item_without_model(monkeypatch, tmp_path: Path) -> None:
    app, runtime, merged_indexes = _batch_app(monkeypatch, tmp_path)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        with client.websocket_connect(
            "/ws/batch", headers={"host": "127.0.0.1"}
        ) as websocket:
            websocket.send_json(_payload("第一句"))
            started = websocket.receive_json()
            item_started = websocket.receive_json()
            item_completed = websocket.receive_json()
            completed = websocket.receive_json()

    assert started["type"] == "batch_start"
    assert started["total"] == 1
    assert item_started == {
        "type": "item_start", "index": 0, "total": 1,
        "text": "第一句", "role": None,
    }
    assert item_completed["type"] == "item_complete"
    assert completed["type"] == "batch_complete"
    assert completed["job"]["status"] == "completed"
    assert runtime.calls == ["第一句"]
    assert merged_indexes == [[0]]


def test_batch_voice_design_locks_following_lines_to_first_role_anchor(monkeypatch, tmp_path: Path) -> None:
    app, runtime, _merged_indexes = _batch_app(monkeypatch, tmp_path)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        completed = _run_batch(client, _payload("第一句", "第二句"))[-1]

    assert completed["type"] == "batch_complete"
    assert runtime.requests[0].mode == "design"
    assert runtime.requests[0].ref_audio_path is None
    assert runtime.requests[1].mode == "direction"
    assert runtime.requests[1].ref_audio_path == tmp_path / "outputs" / "line-1.wav"
    assert runtime.requests[1].ref_text == "第一句"
    assert completed["results"][1]["metadata"]["long_form_voice_lock"]["anchored"] is True


def test_batch_websocket_cancel_pauses_and_resume_merges_checkpointed_items(
    monkeypatch, tmp_path: Path
) -> None:
    app, runtime, merged_indexes = _batch_app(monkeypatch, tmp_path)
    runtime.block_on_text.add("第二句")

    with TestClient(app, base_url="http://127.0.0.1") as client:
        with client.websocket_connect(
            "/ws/batch", headers={"host": "127.0.0.1"}
        ) as websocket:
            websocket.send_json(_payload("第一句", "第二句"))
            batch_started = websocket.receive_json()
            assert websocket.receive_json()["type"] == "item_start"
            first_completed = websocket.receive_json()
            second_started = websocket.receive_json()
            websocket.send_json({"type": "cancel"})
            cancelled = websocket.receive_json()

        assert batch_started["total"] == 2
        assert first_completed["type"] == "item_complete"
        assert first_completed["index"] == 0
        assert second_started["type"] == "item_start"
        assert second_started["index"] == 1
        assert cancelled["type"] == "cancelled"
        assert cancelled["job"]["status"] == "paused"
        assert cancelled["job"]["completed_lines"] == [0]
        job_id = cancelled["job"]["job_id"]

        requeued = client.patch(
            f"/api/queue/{job_id}", json={"updates": {"status": "pending"}}
        )
        assert requeued.status_code == 200
        runtime.block_on_text.clear()

        with client.websocket_connect(
            "/ws/batch", headers={"host": "127.0.0.1"}
        ) as websocket:
            websocket.send_json({"resume_job_id": job_id})
            resumed = websocket.receive_json()
            resumed_item = websocket.receive_json()
            resumed_complete = websocket.receive_json()
            batch_complete = websocket.receive_json()

    assert resumed["type"] == "batch_start"
    assert resumed["total"] == 1
    assert resumed["original_total"] == 2
    assert resumed["resumed"] is True
    assert resumed["completed_lines"] == [0]
    assert resumed_item["type"] == "item_start"
    assert resumed_item["index"] == 1
    assert resumed_item["total"] == 1
    assert resumed_complete["type"] == "item_complete"
    assert batch_complete["type"] == "batch_complete"
    assert [item["index"] for item in batch_complete["results"]] == [0, 1]
    assert batch_complete["job"]["status"] == "completed"
    assert runtime.calls == ["第一句", "第二句", "第二句"]
    assert merged_indexes == [[0, 1]]


def test_saved_project_single_line_rerun_updates_line_and_returns_full_mix(
    monkeypatch, tmp_path: Path
) -> None:
    app, _runtime, _merged_indexes = _batch_app(monkeypatch, tmp_path)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        project = _saved_project(client, "需要重跑", "保留的第二句")
        second_audio = tmp_path / "outputs" / "second-old.wav"
        second_audio.parent.mkdir(parents=True, exist_ok=True)
        sf.write(second_audio, np.full(1_200, 0.2, dtype=np.float32), 24_000)
        project = record_project_line_result(
            project["project_id"], project["lines"][1]["line_id"],
            status="completed", audio_file=second_audio.name,
            metadata={"source": "existing"}, expected_revision=project["revision"],
        )

        messages = _run_batch(client, _line_payload(project, 0))
        completed = messages[-1]

        assert completed["type"] == "batch_complete"
        assert completed["full_project_mix"] is True
        assert completed["remix"] == {
            "status": "completed",
            "reason": "",
            "output": completed["merged_output"],
        }
        assert completed["project_revision"] == completed["project"]["revision"]
        assert completed["project"]["revision"] > project["revision"]
        assert completed["merged_metadata"]["item_count"] == 2
        assert (tmp_path / "outputs" / completed["merged_output"]).is_file()
        rerun_line = completed["project"]["lines"][0]
        assert rerun_line["status"] == "completed"
        assert rerun_line["audio_file"]
        assert rerun_line["error"] == ""
        assert rerun_line["error_type"] == ""
        persisted = client.get(f"/api/projects/{project['project_id']}").json()
        assert persisted == completed["project"]


def test_saved_project_single_line_rerun_returns_reason_when_full_mix_is_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    app, _runtime, merged_indexes = _batch_app(monkeypatch, tmp_path)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        project = _saved_project(client, "本句可生成", "另一句缺少音频")
        completed = _run_batch(client, _line_payload(project, 0))[-1]

        assert completed["type"] == "batch_complete"
        assert completed["full_project_mix"] is False
        assert completed["remix"]["status"] == "unavailable"
        assert "尚无可重混音频" in completed["remix"]["reason"]
        assert project["lines"][1]["line_id"] in completed["remix"]["reason"]
        assert completed["remix"]["output"] is None
        assert completed["merged_output"] == "merged.wav"
        assert merged_indexes == [[0]]
        assert completed["project_revision"] == completed["project"]["revision"]
        persisted = load_project(project["project_id"])
        assert persisted == completed["project"]
        assert persisted["lines"][0]["status"] == "completed"
        assert persisted["lines"][1]["status"] == "pending"


def test_failed_saved_project_single_line_rerun_persists_error_and_keeps_old_audio(
    monkeypatch, tmp_path: Path
) -> None:
    app, runtime, _merged_indexes = _batch_app(monkeypatch, tmp_path)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        project = _saved_project(client, "失败重跑")
        old_audio = tmp_path / "outputs" / "last-good.wav"
        old_audio.parent.mkdir(parents=True, exist_ok=True)
        sf.write(old_audio, np.full(1_200, 0.3, dtype=np.float32), 24_000)
        project = record_project_line_result(
            project["project_id"], project["lines"][0]["line_id"],
            status="completed", audio_file=old_audio.name,
            metadata={"source": "last-good"}, expected_revision=project["revision"],
        )
        runtime.fail_on_text.add("失败重跑")

        failed = _run_batch(client, _line_payload(project, 0))[-1]

        assert failed["type"] == "error"
        assert failed["error_type"] == "RuntimeError"
        assert failed["full_project_mix"] is False
        assert failed["remix"]["status"] == "unavailable"
        assert "生成失败" in failed["remix"]["reason"]
        assert failed["project_revision"] == failed["project"]["revision"]
        assert failed["project"]["revision"] > project["revision"]
        failed_line = failed["project"]["lines"][0]
        assert failed_line["status"] == "failed"
        assert failed_line["error"] == "stub generation failure"
        assert failed_line["error_type"] == "RuntimeError"
        assert failed_line["audio_file"] == old_audio.name
        assert failed_line["generation_metadata"] == {"source": "last-good"}
        assert old_audio.is_file()
        persisted = client.get(f"/api/projects/{project['project_id']}").json()
        assert persisted == failed["project"]
        assert persisted["checkpoint"]["last_error"]["error_type"] == "RuntimeError"


def test_saved_project_rerun_with_unknown_line_never_claims_full_project_mix(
    monkeypatch, tmp_path: Path
) -> None:
    app, _runtime, merged_indexes = _batch_app(monkeypatch, tmp_path)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        project = _saved_project(client, "工程中的原句")
        old_audio = tmp_path / "outputs" / "existing-full-project.wav"
        old_audio.parent.mkdir(parents=True, exist_ok=True)
        sf.write(old_audio, np.full(1_200, 0.4, dtype=np.float32), 24_000)
        project = record_project_line_result(
            project["project_id"], project["lines"][0]["line_id"],
            status="completed", audio_file=old_audio.name,
            expected_revision=project["revision"],
        )
        payload = _line_payload(project, 0)
        payload["items"][0]["line_id"] = "missing-line-id"

        completed = _run_batch(client, payload)[-1]

        assert completed["type"] == "batch_complete"
        assert completed["full_project_mix"] is False
        assert completed["remix"]["status"] == "unavailable"
        assert "未能回填" in completed["remix"]["reason"]
        assert "台词不存在" in completed["remix"]["reason"]
        assert completed["project_revision"] == project["revision"]
        assert completed["project"] == project
        assert completed["merged_output"] == "merged.wav"
        assert merged_indexes == [[0]]


def test_saved_project_revision_conflict_during_generation_preserves_concurrent_edit(
    monkeypatch, tmp_path: Path
) -> None:
    app, runtime, merged_indexes = _batch_app(monkeypatch, tmp_path)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        project = _saved_project(client, "生成开始前的文本")
        old_audio = tmp_path / "outputs" / "before-concurrent-edit.wav"
        old_audio.parent.mkdir(parents=True, exist_ok=True)
        sf.write(old_audio, np.full(1_200, 0.5, dtype=np.float32), 24_000)
        project = record_project_line_result(
            project["project_id"], project["lines"][0]["line_id"],
            status="completed", audio_file=old_audio.name,
            metadata={"source": "before-edit"}, expected_revision=project["revision"],
        )
        concurrently_saved: list[dict[str, object]] = []

        def save_concurrent_edit(_text: str) -> None:
            current = load_project(project["project_id"])
            assert current is not None
            current["lines"][0]["text"] = "生成期间保存的新文本"
            current["lines"][0]["dirty_fields"] = ["text"]
            concurrently_saved.append(
                save_project(current, expected_revision=current["revision"])
            )

        runtime.generate_hook = save_concurrent_edit
        completed = _run_batch(client, _line_payload(project, 0))[-1]

        assert completed["type"] == "batch_complete"
        assert completed["full_project_mix"] is False
        assert completed["remix"]["status"] == "unavailable"
        assert "版本冲突" in completed["remix"]["reason"]
        assert f"expected_revision={project['revision']}" in completed["remix"]["reason"]
        assert f"current_revision={concurrently_saved[0]['revision']}" in completed["remix"]["reason"]
        assert completed["project_revision"] == concurrently_saved[0]["revision"]
        assert completed["project"] == concurrently_saved[0]
        line = completed["project"]["lines"][0]
        assert line["text"] == "生成期间保存的新文本"
        assert line["dirty_fields"] == ["text"]
        assert line["audio_file"] == old_audio.name
        assert line["generation_metadata"] == {"source": "before-edit"}
        assert completed["merged_output"] == "merged.wav"
        assert merged_indexes == [[0]]


def test_saved_project_revision_advances_after_each_line_before_remix(
    monkeypatch, tmp_path: Path
) -> None:
    app, _runtime, _merged_indexes = _batch_app(monkeypatch, tmp_path)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        project = _saved_project(client, "第一句", "第二句")
        payload = _line_payload(project, 0)
        payload["items"].append(_line_payload(project, 1)["items"][0])
        messages = _run_batch(client, payload)
        item_completions = [
            message for message in messages if message["type"] == "item_complete"
        ]
        completed = messages[-1]

        assert [item["project_revision"] for item in item_completions] == [
            project["revision"] + 1,
            project["revision"] + 2,
        ]
        assert completed["type"] == "batch_complete"
        assert completed["full_project_mix"] is True
        assert completed["project_revision"] == project["revision"] + 3
        assert completed["remix"]["output"] == completed["merged_output"]


def test_saved_project_without_revision_generates_one_shot_without_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    app, _runtime, merged_indexes = _batch_app(monkeypatch, tmp_path)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        project = _saved_project(client, "必须保留的工程行")
        old_audio = tmp_path / "outputs" / "revision-required.wav"
        old_audio.parent.mkdir(parents=True, exist_ok=True)
        sf.write(old_audio, np.full(1_200, 0.6, dtype=np.float32), 24_000)
        project = record_project_line_result(
            project["project_id"], project["lines"][0]["line_id"],
            status="completed", audio_file=old_audio.name,
            metadata={"source": "protected"}, expected_revision=project["revision"],
        )
        payload = _line_payload(project, 0)
        payload.pop("project_revision")

        completed = _run_batch(client, payload)[-1]

        assert completed["type"] == "batch_complete"
        assert completed["full_project_mix"] is False
        assert completed["remix"]["status"] == "unavailable"
        assert "缺少 project_revision" in completed["remix"]["reason"]
        assert "one-shot" in completed["remix"]["reason"]
        assert completed["project_revision"] == project["revision"]
        assert completed["project"] == project
        assert load_project(project["project_id"]) == project
        assert completed["merged_output"] == "merged.wav"
        assert merged_indexes == [[0]]


def test_saved_project_item_without_line_id_generates_one_shot_without_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    app, _runtime, merged_indexes = _batch_app(monkeypatch, tmp_path)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        project = _saved_project(client, "不能被无 ID 项覆盖")
        old_audio = tmp_path / "outputs" / "line-id-required.wav"
        old_audio.parent.mkdir(parents=True, exist_ok=True)
        sf.write(old_audio, np.full(1_200, 0.7, dtype=np.float32), 24_000)
        project = record_project_line_result(
            project["project_id"], project["lines"][0]["line_id"],
            status="completed", audio_file=old_audio.name,
            metadata={"source": "protected"}, expected_revision=project["revision"],
        )
        payload = _line_payload(project, 0)
        payload["items"][0].pop("line_id")

        completed = _run_batch(client, payload)[-1]

        assert completed["type"] == "batch_complete"
        assert completed["full_project_mix"] is False
        assert completed["remix"]["status"] == "unavailable"
        assert "必须全部提供 line_id" in completed["remix"]["reason"]
        assert "one-shot" in completed["remix"]["reason"]
        assert completed["project_revision"] == project["revision"]
        assert completed["project"] == project
        assert load_project(project["project_id"]) == project
        assert completed["merged_output"] == "merged.wav"
        assert merged_indexes == [[0]]


def test_failed_saved_project_request_without_revision_does_not_write_failure_state(
    monkeypatch, tmp_path: Path
) -> None:
    app, runtime, _merged_indexes = _batch_app(monkeypatch, tmp_path)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        project = _saved_project(client, "失败也不能污染工程")
        old_audio = tmp_path / "outputs" / "failure-protected.wav"
        old_audio.parent.mkdir(parents=True, exist_ok=True)
        sf.write(old_audio, np.full(1_200, 0.8, dtype=np.float32), 24_000)
        project = record_project_line_result(
            project["project_id"], project["lines"][0]["line_id"],
            status="completed", audio_file=old_audio.name,
            metadata={"source": "protected"}, expected_revision=project["revision"],
        )
        payload = _line_payload(project, 0)
        payload.pop("project_revision")
        runtime.fail_on_text.add("失败也不能污染工程")

        failed = _run_batch(client, payload)[-1]

        assert failed["type"] == "error"
        assert failed["error_type"] == "RuntimeError"
        assert failed["full_project_mix"] is False
        assert failed["remix"]["status"] == "unavailable"
        assert "缺少 project_revision" in failed["remix"]["reason"]
        assert failed["project_revision"] == project["revision"]
        assert failed["project"] == project
        assert load_project(project["project_id"]) == project
        line = failed["project"]["lines"][0]
        assert line["status"] == "completed"
        assert line["error"] == ""
        assert line["error_type"] == ""
        assert line["audio_file"] == old_audio.name
