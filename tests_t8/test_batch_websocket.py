from __future__ import annotations

import threading
from pathlib import Path

from fastapi.testclient import TestClient

from t8_runtime import server


class StubRuntime:
    def __init__(self, model_dir: Path, output_root: Path) -> None:
        self.model_dir = model_dir
        self.output_root = output_root
        self.calls: list[str] = []
        self.block_on_text: set[str] = set()

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
        if request.text in self.block_on_text:
            assert cancel_event is not None
            cancel_event.wait(timeout=5)
            if cancel_event.is_set():
                raise InterruptedError("stub generation cancelled")
            raise AssertionError("test did not cancel the blocked stub generation")
        self.output_root.mkdir(parents=True, exist_ok=True)
        target = self.output_root / f"line-{len(self.calls)}.wav"
        target.write_bytes(b"stub wav")
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
