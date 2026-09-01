from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from t8_runtime.dialogue import (
    apply_timeline_edit,
    effective_generation,
    normalize_project,
    parse_dialogue,
    to_srt,
)
from t8_runtime.workspace_store import (
    export_project,
    import_project,
    list_history,
    load_project,
    queue_put,
    queue_snapshot,
    queue_update,
    save_project,
)


@pytest.fixture()
def isolated_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_srt_roundtrip_preserves_millisecond_timeline() -> None:
    source = "1\n00:00:00,125 --> 00:00:02,345\n第一句\n\n2\n00:00:03,000 --> 00:00:04,010\nSecond"
    project = parse_dialogue("srt", source)["project"]
    assert project["lines"][0]["start_ms"] == 125
    assert project["lines"][0]["end_ms"] == 2345
    assert "00:00:00,125 --> 00:00:02,345" in to_srt(project)


def test_txt_maps_index_text_but_rejects_vector() -> None:
    result = parse_dialogue("txt", "旁白 | 你好 | zh | emotion=text:克制而悲伤")
    line = result["project"]["lines"][0]
    assert line["direction_mode"] == "override"
    assert line["direction_text"] == "克制而悲伤"
    assert result["warnings"]
    with pytest.raises(ValueError, match="emotion=vector"):
        parse_dialogue("txt", "旁白 | 你好 | zh | emotion=vector:0,0,0,0,0,0,1,0")


def test_line_ids_survive_reorder_and_timeline_edit_checks_revision() -> None:
    project = normalize_project({"revision": 3, "lines": [{"text": "甲"}, {"text": "乙"}]})
    ids = [line["line_id"] for line in project["lines"]]
    project["lines"].reverse()
    project = normalize_project(project)
    assert {line["line_id"] for line in project["lines"]} == set(ids)
    updated = apply_timeline_edit(
        project,
        line_id=ids[0],
        start_ms=250,
        end_ms=1250,
        revision=3,
    )
    assert updated["revision"] == 4
    assert next(line for line in updated["lines"] if line["line_id"] == ids[0])["start_ms"] == 250
    with pytest.raises(RuntimeError, match="版本冲突"):
        apply_timeline_edit(project, line_id=ids[0], start_ms=0, end_ms=100, revision=2)


def test_clone_override_routes_to_native_direction() -> None:
    line = normalize_project({"lines": [{"text": "台词", "direction_mode": "override", "direction_text": "低声悲伤"}]})["lines"][0]
    request = effective_generation(
        line,
        {"mode": "clone", "instruction": "自然", "reference_text": "参考"},
        {"seed": 42},
    )
    assert request["mode"] == "direction"
    assert request["instruction"] == "低声悲伤"


def test_project_store_revision_bundle_and_tamper_rejection(isolated_data: Path) -> None:
    project = normalize_project({"name": "测试工程", "lines": [{"text": "你好"}]})
    saved = save_project(project)
    assert load_project(saved["project_id"])["revision"] == saved["revision"]
    with pytest.raises(RuntimeError, match="版本冲突"):
        save_project(saved, expected_revision=999)
    bundle = isolated_data / "project.t8project.zip"
    export_project(saved, bundle)
    imported = import_project(bundle)
    assert imported["name"] == "测试工程"
    assert imported["project_id"] != saved["project_id"]

    tampered = isolated_data / "tampered.zip"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            target.writestr(info.filename, b"{}" if info.filename == "project.json" else data)
    with pytest.raises(ValueError, match="哈希"):
        import_project(tampered)


def test_project_bundle_rejects_traversal(isolated_data: Path) -> None:
    archive = isolated_data / "unsafe.t8project.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("../escape.json", "{}")
        target.writestr("manifest.json", json.dumps({"project_file": "project.json"}))
    with pytest.raises(ValueError, match="不安全"):
        import_project(archive)


def test_persistent_queue_checkpoint(isolated_data: Path) -> None:
    job = queue_put({"project_id": "project-123", "total": 3})
    updated = queue_update(job["job_id"], {"status": "paused", "completed_lines": [0, 1]})
    assert updated["completed_lines"] == [0, 1]
    assert queue_snapshot()[0]["status"] == "paused"
    assert list_history() == []
