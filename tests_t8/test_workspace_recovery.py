from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from t8_runtime import server
from t8_runtime.dialogue import normalize_project
from t8_runtime.voice_library import VoiceInUseError, delete_voice, save_voice
from t8_runtime.workspace_store import (
    ProjectRevisionConflict,
    QueueRevisionConflict,
    delete_project,
    export_project,
    import_project,
    load_project,
    project_mix_results,
    queue_checkpoint,
    queue_claim,
    queue_put,
    queue_resume_payload,
    queue_snapshot,
    queue_update,
    record_project_line_result,
    recover_interrupted_jobs,
    remix_project,
    save_project,
)


@pytest.fixture()
def isolated_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    data = tmp_path / "data"
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(data))
    monkeypatch.setenv("T8_BREEZE_OUTPUT_DIR", str(outputs))
    outputs.mkdir(parents=True)
    return tmp_path


def test_queue_restart_recovery_requeue_and_revision(isolated_workspace: Path) -> None:
    project = save_project(normalize_project({
        "name": "恢复测试",
        "lines": [{"text": "第一句"}, {"text": "第二句"}],
    }))
    line_ids = [line["line_id"] for line in project["lines"]]
    # Caller-controlled reserved values must never forge persisted queue state.
    job = queue_put({
        "project_id": project["project_id"], "total": 2,
        "job_id": "forged", "status": "completed", "revision": 999,
    })
    assert job["job_id"] != "forged"
    assert job["status"] == "pending"
    assert job["revision"] == 0
    assert job["total"] == 2
    assert job["recoverable"] is True
    assert len(queue_resume_payload(job["job_id"])["items"]) == 2

    claimed = queue_claim(queue_resume_payload(job["job_id"]), job_id=job["job_id"])
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    checkpoint = queue_checkpoint(job["job_id"], line_ids[0], {"output": "line-1.wav"})
    assert checkpoint["completed_lines"] == [line_ids[0]]

    recovered = recover_interrupted_jobs()
    assert [item["job_id"] for item in recovered] == [job["job_id"]]
    paused = queue_snapshot()[0]
    assert paused["status"] == "paused"
    assert paused["error_type"] == "ProcessRestart"
    assert paused["recovery"]["completed_lines"] == [line_ids[0]]

    with pytest.raises(QueueRevisionConflict, match=r"expected_revision=.*current_revision"):
        queue_update(job["job_id"], {"status": "pending", "expected_revision": paused["revision"] - 1})
    pending = queue_update(
        job["job_id"], {"status": "pending", "expected_revision": paused["revision"]}
    )
    assert pending["status"] == "pending"
    assert pending["completed_lines"] == [line_ids[0]]
    assert pending["error"] == ""

    failed = queue_update(job["job_id"], {"status": "failed", "failed_line": line_ids[1]})
    assert failed["error_type"] == "UnknownTaskFailure"
    assert "没有提供错误详情" in failed["error"]
    requeued = queue_update(job["job_id"], {"status": "pending"})
    assert requeued["failed_line"] is None
    assert requeued["completed_lines"] == [line_ids[0]]

    # A one-line redo in a larger project is attached when its WebSocket
    # payload arrives; it must not accidentally resume the whole project.
    single = queue_put({"project_id": project["project_id"], "total": 1})
    assert single["recoverable"] is False
    single_payload = {
        "project_id": project["project_id"],
        "defaults": {},
        "items": [{"line_id": line_ids[1], "text": "第二句"}],
        "timeline": True,
    }
    claimed_single = queue_claim(single_payload)
    assert claimed_single["job_id"] == single["job_id"]
    assert queue_resume_payload(single["job_id"])["items"] == single_payload["items"]


def _write_audio(path: Path, samples: int, value: float) -> None:
    sf.write(path, np.full(samples, value, dtype=np.float32), 24_000, subtype="PCM_16")


def test_line_regeneration_updates_checkpoint_and_full_project_remix(
    isolated_workspace: Path,
) -> None:
    outputs = isolated_workspace / "outputs"
    _write_audio(outputs / "line-a.wav", 2_400, 0.1)
    _write_audio(outputs / "line-b.wav", 2_400, 0.2)
    project = save_project(normalize_project({
        "name": "单句重做",
        "timing": {"policy": "overlay", "gap_ms": 100},
        "lines": [
            {
                "text": "甲", "start_ms": 0, "end_ms": 100,
                "dirty_fields": ["text", "timing"],
            },
            {"text": "乙", "start_ms": 200, "end_ms": 300, "dirty_fields": ["voice_id"]},
        ],
    }))
    first_id, second_id = [line["line_id"] for line in project["lines"]]

    first = record_project_line_result(
        project["project_id"], first_id, status="completed", audio_file="line-a.wav",
        metadata={"seed": 11}, expected_revision=project["revision"],
    )
    first_line = next(line for line in first["lines"] if line["line_id"] == first_id)
    assert first_line["audio_file"] == "line-a.wav"
    assert first_line["dirty_fields"] == ["timing"]
    assert first_line["generation_metadata"] == {"seed": 11}
    assert first["checkpoint"]["completed_line_ids"] == [first_id]

    second = record_project_line_result(
        project["project_id"], second_id, status="completed", audio_file="line-b.wav",
        metadata={"seed": 12}, expected_revision=first["revision"],
    )
    with pytest.raises(ProjectRevisionConflict, match=r"expected_revision=.*current_revision"):
        record_project_line_result(
            project["project_id"], first_id, status="completed", audio_file="line-a.wav",
            expected_revision=project["revision"],
        )

    mixed = remix_project(project["project_id"], expected_revision=second["revision"])
    assert mixed["metadata"]["item_count"] == 2
    assert (outputs / mixed["output"]).is_file()
    assert mixed["project"]["checkpoint"]["last_mix"]["output"] == mixed["output"]

    bundle = isolated_workspace / "portable.t8project.zip"
    export_project(mixed["project"], bundle)
    imported = import_project(bundle)
    assert imported["project_id"] != project["project_id"]
    assert all((outputs / line["audio_file"]).is_file() for line in imported["lines"])
    imported_mix = remix_project(imported["project_id"], expected_revision=imported["revision"])
    assert imported_mix["metadata"]["item_count"] == 2

    # A failed redo keeps the last known-good clip for remix while recording a
    # complete failure checkpoint and line status.
    failed = record_project_line_result(
        project["project_id"], first_id, status="failed",
        error="CUDA allocation failed", error_type="OutOfMemoryError",
        expected_revision=mixed["project"]["revision"],
    )
    failed_line = next(line for line in failed["lines"] if line["line_id"] == first_id)
    assert failed_line["audio_file"] == "line-a.wav"
    assert failed_line["status"] == "failed"
    assert failed_line["error"] == "CUDA allocation failed"
    assert failed_line["error_type"] == "OutOfMemoryError"
    assert failed["checkpoint"]["last_error"] == {
        "line_id": first_id,
        "message": "CUDA allocation failed",
        "error_type": "OutOfMemoryError",
        "at": failed["checkpoint"]["last_error"]["at"],
    }
    _, cached_results = project_mix_results(project["project_id"])
    assert [item["line_id"] for item in cached_results] == [first_id, second_id]


def test_voice_delete_is_blocked_by_project_and_resumable_queue(
    isolated_workspace: Path,
) -> None:
    voice = save_voice(name="被引用音色", mode="design", instruction="自然")
    project = save_project(normalize_project({
        "defaults": {"voice_id": voice["id"]},
        "lines": [{"text": "使用音色", "voice_id": voice["id"]}],
    }))
    with pytest.raises(VoiceInUseError, match="正被引用") as project_error:
        delete_voice(voice["id"])
    assert any(item["kind"] == "project" for item in project_error.value.references)

    job = queue_put({"project_id": project["project_id"]})
    project["defaults"]["voice_id"] = ""
    project["lines"][0]["voice_id"] = ""
    project = save_project(project, expected_revision=project["revision"])
    with pytest.raises(VoiceInUseError) as queue_error:
        delete_voice(voice["id"])
    assert any(item["kind"] == "queue" for item in queue_error.value.references)

    queue_update(job["job_id"], {"status": "completed"})
    assert delete_voice(voice["id"]) is True
    assert delete_project(project["project_id"]) is True


def test_missing_project_revision_conflict_is_complete(isolated_workspace: Path) -> None:
    project = normalize_project({"project_id": "missing-project", "lines": [{"text": "甲"}]})
    with pytest.raises(ProjectRevisionConflict) as error:
        save_project(project, expected_revision=7)
    assert error.value.project_id == "missing-project"
    assert error.value.expected == 7
    assert error.value.current is None
    assert "current_revision=missing" in str(error.value)


def _reset_error(winerror: int) -> ConnectionResetError:
    error = ConnectionResetError(winerror, "connection reset")
    error.winerror = winerror
    return error


def test_windows_proactor_reset_filter_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    benign = {
        "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)",
        "exception": _reset_error(10054),
    }
    assert server._is_benign_windows_proactor_reset(benign, platform_name="nt") is True
    assert server._is_benign_windows_proactor_reset(benign, platform_name="posix") is False
    assert server._is_benign_windows_proactor_reset(
        {**benign, "message": "Task exception was never retrieved"}, platform_name="nt"
    ) is False
    assert server._is_benign_windows_proactor_reset(
        {**benign, "exception": _reset_error(10053)}, platform_name="nt"
    ) is False

    forwarded = []

    class FakeLoop:
        def __init__(self):
            self.handler = lambda loop, context: forwarded.append((loop, context))

        def get_exception_handler(self):
            return self.handler

        def set_exception_handler(self, handler):
            self.handler = handler

        def default_exception_handler(self, context):
            forwarded.append((self, context))

    loop = FakeLoop()
    monkeypatch.setattr(server, "_is_benign_windows_proactor_reset", lambda context: context.get("benign", False))
    previous, installed = server._install_asyncio_exception_handler(loop)
    assert previous is not None and loop.handler is installed
    installed(loop, {"benign": True})
    assert forwarded == []
    installed(loop, {"benign": False, "exception": RuntimeError("real failure")})
    assert len(forwarded) == 1


def test_recovery_conflict_and_voice_guards_are_exposed_by_api(
    isolated_workspace: Path,
) -> None:
    app = server.create_app(isolated_workspace / "models")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        voice = client.post("/api/voices", json={
            "name": "API 音色", "mode": "design", "instruction": "自然",
        }).json()
        project = save_project(normalize_project({
            "name": "API 工程",
            "lines": [{"text": "台词", "voice_id": voice["id"]}],
        }))
        conflict = client.put(
            f"/api/projects/{project['project_id']}",
            json={"project": project, "expected_revision": project["revision"] - 1},
        )
        assert conflict.status_code == 409
        assert f"project_id={project['project_id']}" in conflict.json()["detail"]
        assert "expected_revision=" in conflict.json()["detail"]
        assert "current_revision=" in conflict.json()["detail"]

        blocked = client.delete(f"/api/voices/{voice['id']}")
        assert blocked.status_code == 409
        assert "API 工程" in blocked.json()["detail"]

        queued = client.post("/api/queue", json={
            "payload": {"project_id": project["project_id"]},
        }).json()
        failed = client.patch(f"/api/queue/{queued['job_id']}", json={
            "updates": {"status": "failed", "error": "simulated", "error_type": "TestFailure"},
        }).json()
        requeued = client.patch(f"/api/queue/{queued['job_id']}", json={
            "updates": {"status": "pending", "expected_revision": failed["revision"]},
        }).json()
        assert requeued["status"] == "pending"
        resumed = client.get(f"/api/queue/{queued['job_id']}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["payload"]["items"][0]["line_id"] == project["lines"][0]["line_id"]
