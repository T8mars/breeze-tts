from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

from .config import output_dir, user_data_dir
from .dialogue import normalize_project


_LOCK = threading.RLock()
MAX_BUNDLE_FILES = 600
MAX_MEMBER_BYTES = 150 * 1024 * 1024
MAX_BUNDLE_BYTES = 1024 * 1024 * 1024
QUEUE_STATUSES = {"pending", "running", "paused", "failed", "completed", "cancelled"}
ACTIVE_QUEUE_STATUSES = {"pending", "running", "paused", "failed"}
_QUEUE_RESERVED = {
    "job_id", "status", "created_at", "updated_at", "started_at", "finished_at",
    "completed_lines", "error", "error_type", "failed_line", "revision", "attempts",
    "request", "recoverable", "results", "recovery",
}
_GENERATION_DIRTY_FIELDS = {
    "text", "role", "voice_id", "language", "direction_mode", "direction_text",
    "cfg_scale", "seed", "instruction", "reference",
}


class ProjectRevisionConflict(RuntimeError):
    def __init__(self, project_id: str, expected: int | None, current: int | None):
        self.project_id = project_id
        self.expected = expected
        self.current = current
        super().__init__(
            f"工程版本冲突：project_id={project_id}，expected_revision={expected}，"
            f"current_revision={current if current is not None else 'missing'}。"
        )


class QueueRevisionConflict(RuntimeError):
    def __init__(self, job_id: str, expected: int, current: int):
        self.job_id = job_id
        self.expected = expected
        self.current = current
        super().__init__(
            f"队列任务版本冲突：job_id={job_id}，expected_revision={expected}，"
            f"current_revision={current}。"
        )


def _root() -> Path:
    return user_data_dir() / "workspace"


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _json_copy(value: Any, *, max_bytes: int = 32 * 1024 * 1024) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("持久化数据必须是有效 JSON。") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError("持久化数据超过大小限制。")
    return json.loads(encoded)


def _project_path(project_id: str) -> Path | None:
    safe = Path(str(project_id)).name
    if safe != project_id or not safe:
        return None
    return _root() / "projects" / f"{safe}.json"


def list_projects() -> list[dict[str, Any]]:
    root = _root() / "projects"
    result = []
    with _LOCK:
        for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                project = normalize_project(json.loads(path.read_text(encoding="utf-8")))
                result.append({key: project[key] for key in ("project_id", "name", "revision", "updated_at")})
            except (OSError, ValueError, json.JSONDecodeError):
                continue
    return result


def load_project(project_id: str) -> dict[str, Any] | None:
    path = _project_path(project_id)
    if path is None:
        return None
    with _LOCK:
        try:
            return normalize_project(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None


def save_project(payload: dict[str, Any], *, expected_revision: int | None = None) -> dict[str, Any]:
    incoming = normalize_project(payload)
    with _LOCK:
        current = load_project(incoming["project_id"])
        if expected_revision is not None and (
            current is None or current["revision"] != int(expected_revision)
        ):
            raise ProjectRevisionConflict(
                incoming["project_id"], int(expected_revision),
                current["revision"] if current is not None else None,
            )
        incoming["revision"] = (current["revision"] if current else incoming["revision"]) + 1
        incoming["updated_at"] = int(time.time())
        _atomic_json(_root() / "projects" / f"{incoming['project_id']}.json", incoming)
    return incoming


def delete_project(project_id: str) -> bool:
    path = _project_path(project_id)
    if path is None:
        return False
    with _LOCK:
        existed = path.is_file()
        path.unlink(missing_ok=True)
    return existed


def append_history(record: dict[str, Any]) -> dict[str, Any]:
    item = {
        "history_id": uuid.uuid4().hex,
        "created_at": int(time.time()),
        **{key: value for key, value in record.items() if key not in {"reference_audio_base64"}},
    }
    with _LOCK:
        path = _root() / "history.json"
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            values = []
        values = ([item] + [value for value in values if isinstance(value, dict)])[:500]
        _atomic_json(path, values)
    return item


def list_history(limit: int = 100) -> list[dict[str, Any]]:
    try:
        values = json.loads((_root() / "history.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [value for value in values if isinstance(value, dict)][: max(1, min(int(limit), 500))]


def _completed_lines(value: Any, total: int = 0) -> list[int | str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[int | str] = []
    seen: set[tuple[type, Any]] = set()
    for raw in value:
        if isinstance(raw, bool):
            continue
        if isinstance(raw, int) or (isinstance(raw, str) and raw.strip().isdigit()):
            parsed: int | str = int(raw)
            if parsed < 0 or (total > 0 and parsed >= total):
                continue
        else:
            parsed = str(raw or "").strip()
            if not parsed or len(parsed) > 80:
                continue
        marker = (type(parsed), parsed)
        if marker not in seen:
            seen.add(marker)
            result.append(parsed)
    return result


def _normalise_job(raw: dict[str, Any]) -> dict[str, Any]:
    now = int(time.time())
    item = dict(raw)
    job_id = str(raw.get("job_id") or "").strip()
    if not job_id or Path(job_id).name != job_id:
        job_id = uuid.uuid4().hex
    try:
        total = max(0, int(raw.get("total") or 0))
    except (TypeError, ValueError):
        total = 0
    status = str(raw.get("status") or "pending").lower()
    if status not in QUEUE_STATUSES:
        status = "failed"
        item["error"] = f"持久化任务状态无效：{raw.get('status')!r}。"
        item["error_type"] = "InvalidQueueStatus"
    request = raw.get("request") if isinstance(raw.get("request"), dict) else None
    item.update({
        "job_id": job_id,
        "status": status,
        "total": total,
        "created_at": int(raw.get("created_at") or now),
        "updated_at": int(raw.get("updated_at") or raw.get("created_at") or now),
        "started_at": int(raw.get("started_at") or 0),
        "finished_at": int(raw.get("finished_at") or 0),
        "completed_lines": _completed_lines(raw.get("completed_lines"), total),
        "error": str(raw.get("error") or "")[:8_000],
        "error_type": str(raw.get("error_type") or "")[:200],
        "failed_line": raw.get("failed_line"),
        "revision": max(0, int(raw.get("revision") or 0)),
        "attempts": max(0, int(raw.get("attempts") or 0)),
        "request": deepcopy(request),
        "recoverable": bool(request) and bool(raw.get("recoverable", True)),
        "results": deepcopy(raw.get("results") if isinstance(raw.get("results"), dict) else {}),
        "recovery": deepcopy(raw.get("recovery") if isinstance(raw.get("recovery"), dict) else {}),
    })
    return item


def _read_queue_unlocked() -> list[dict[str, Any]]:
    try:
        values = json.loads((_root() / "queue.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(values, list):
        return []
    return [_normalise_job(value) for value in values if isinstance(value, dict)]


def _write_queue_unlocked(values: list[dict[str, Any]]) -> None:
    _atomic_json(_root() / "queue.json", values[-200:])


def queue_snapshot() -> list[dict[str, Any]]:
    with _LOCK:
        return deepcopy(_read_queue_unlocked())


def _project_resume_payload(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": project["project_id"],
        "project_revision": project["revision"],
        "defaults": deepcopy(project.get("defaults") or {}),
        "items": [
            {
                **{
                    key: deepcopy(line.get(key))
                    for key in (
                        "line_id", "order", "role", "voice_id", "language", "text",
                        "direction_mode", "direction_text", "cfg_scale", "seed",
                    )
                },
                "subtitle": {
                    "index": line["order"], "start_ms": line["start_ms"],
                    "end_ms": line["end_ms"], "text": line["text"],
                },
            }
            for line in project["lines"]
        ],
        "timeline": True,
        "timing_policy": project.get("timing", {}).get("policy", "preserve"),
    }


def _sanitise_resume_payload(payload: Any) -> tuple[dict[str, Any] | None, bool, str]:
    if not isinstance(payload, dict):
        return None, False, "任务没有可恢复的请求快照。"
    removed_secret = False

    def scrub(value: Any) -> Any:
        nonlocal removed_secret
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if str(key).lower().endswith("audio_base64"):
                    if str(child or "").strip():
                        removed_secret = True
                    continue
                result[str(key)] = scrub(child)
            return result
        if isinstance(value, list):
            return [scrub(child) for child in value]
        return value

    try:
        clean = _json_copy(scrub(payload))
    except ValueError as exc:
        return None, False, str(exc)
    if removed_secret:
        return clean, False, "内嵌参考音频不会写入队列；请改用已保存的 voice_id 后重试。"
    return clean, True, ""


def queue_put(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("队列任务必须是 JSON 对象。")
    now = int(time.time())
    public_payload = {
        str(key): _json_copy(value, max_bytes=4 * 1024 * 1024)
        for key, value in payload.items()
        if str(key) not in _QUEUE_RESERVED
    }
    request_source = payload.get("request")
    project_id = str(public_payload.get("project_id") or "").strip()
    if request_source is None and project_id:
        project = load_project(project_id)
        requested_total = int(public_payload.get("total") or 0)
        if project is not None and requested_total in {0, len(project["lines"])}:
            request_source = _project_resume_payload(project)
            public_payload["total"] = len(project["lines"])
    request, recoverable, recovery_error = _sanitise_resume_payload(request_source)
    item = {
        **public_payload,
        "job_id": uuid.uuid4().hex,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "started_at": 0,
        "finished_at": 0,
        "completed_lines": [],
        "error": recovery_error,
        "error_type": "" if recoverable or not recovery_error else "UnrecoverableQueueJob",
        "failed_line": None,
        "revision": 0,
        "attempts": 0,
        "request": request,
        "recoverable": recoverable,
        "results": {},
        "recovery": {},
    }
    item = _normalise_job(item)
    with _LOCK:
        values = _read_queue_unlocked()
        values.append(item)
        _write_queue_unlocked(values)
    return deepcopy(item)


def queue_update(job_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(updates, dict):
        raise ValueError("任务更新必须是 JSON 对象。")
    allowed = {"status", "completed_lines", "error", "error_type", "failed_line", "position"}
    with _LOCK:
        values = _read_queue_unlocked()
        item = next((value for value in values if value.get("job_id") == job_id), None)
        if item is None:
            raise KeyError("任务不存在。")
        expected = updates.get("expected_revision")
        if expected is not None and int(expected) != item["revision"]:
            raise QueueRevisionConflict(job_id, int(expected), item["revision"])
        previous_status = item["status"]
        next_status = str(updates.get("status") or previous_status).lower()
        if next_status not in QUEUE_STATUSES:
            raise ValueError(f"不支持的任务状态：{next_status}。")
        if next_status == "pending" and previous_status in {"paused", "failed"}:
            if not item.get("recoverable") or not isinstance(item.get("request"), dict):
                raise ValueError(item.get("error") or "任务缺少可恢复请求，不能重新排队。")
            item.update({
                "error": "", "error_type": "", "failed_line": None,
                "finished_at": 0, "requeued_at": int(time.time()),
            })
        elif next_status == "pending" and previous_status not in {"pending", "paused", "failed"}:
            raise ValueError(f"{previous_status} 任务不能直接重新排队。")
        item["status"] = next_status
        if "completed_lines" in updates:
            item["completed_lines"] = _completed_lines(
                [*item["completed_lines"], *(
                    updates["completed_lines"] if isinstance(updates["completed_lines"], list) else []
                )],
                item["total"],
            )
        if "error" in updates:
            item["error"] = str(updates["error"] or "")[:8_000]
        if "error_type" in updates:
            item["error_type"] = str(updates["error_type"] or "")[:200]
        if "failed_line" in updates:
            item["failed_line"] = updates["failed_line"]
        if next_status == "failed" and not item["error"]:
            item["error"] = "任务失败，但执行器没有提供错误详情。"
            item["error_type"] = item["error_type"] or "UnknownTaskFailure"
        if next_status in {"completed", "cancelled"}:
            item["finished_at"] = int(time.time())
            if next_status == "completed":
                item["error"] = ""
                item["error_type"] = ""
                item["failed_line"] = None
        item["updated_at"] = int(time.time())
        item["revision"] += 1
        if "position" in updates:
            values.remove(item)
            values.insert(max(0, min(int(updates["position"]), len(values))), item)
        _write_queue_unlocked(values)
        return deepcopy(item)


def queue_claim(payload: dict[str, Any], *, job_id: str | None = None) -> dict[str, Any]:
    """Attach an executable snapshot and atomically claim a pending job."""
    request, recoverable, recovery_error = _sanitise_resume_payload(payload)
    project_id = str(payload.get("project_id") or "").strip()
    total = len(payload.get("items") or []) if isinstance(payload.get("items"), list) else 0
    with _LOCK:
        values = _read_queue_unlocked()
        item = None
        if job_id:
            item = next((value for value in values if value.get("job_id") == job_id), None)
        else:
            candidates = [
                value for value in values
                if value.get("status") == "pending"
                and str(value.get("project_id") or "") == project_id
                and (not total or int(value.get("total") or 0) == total)
            ]
            item = candidates[-1] if candidates else None
        if item is None:
            # The WebSocket can be used without the queue REST call.
            return queue_claim(payload, job_id=queue_put({
                "kind": "dialogue", "project_id": project_id, "total": total,
                "request": payload,
            })["job_id"])
        if item["status"] != "pending":
            raise ValueError(f"任务状态为 {item['status']}，不能开始执行。")
        if request is not None:
            item["request"] = request
            item["recoverable"] = recoverable
            if recovery_error:
                item["error"] = recovery_error
                item["error_type"] = "UnrecoverableQueueJob"
            else:
                item["error"] = ""
                item["error_type"] = ""
        item.update({
            "status": "running", "started_at": int(time.time()), "finished_at": 0,
            "updated_at": int(time.time()), "attempts": item["attempts"] + 1,
            "revision": item["revision"] + 1,
        })
        _write_queue_unlocked(values)
        return deepcopy(item)


def queue_checkpoint(job_id: str, line_key: int | str, result: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        values = _read_queue_unlocked()
        item = next((value for value in values if value.get("job_id") == job_id), None)
        if item is None:
            raise KeyError("任务不存在。")
        completed = _completed_lines([*item["completed_lines"], line_key], item["total"])
        item["completed_lines"] = completed
        item["results"][str(line_key)] = _json_copy(result, max_bytes=256 * 1024)
        item["status"] = "running"
        item["error"] = ""
        item["error_type"] = ""
        item["failed_line"] = None
        item["updated_at"] = int(time.time())
        item["revision"] += 1
        _write_queue_unlocked(values)
        return deepcopy(item)


def queue_resume_payload(job_id: str) -> dict[str, Any]:
    with _LOCK:
        item = next((value for value in _read_queue_unlocked() if value.get("job_id") == job_id), None)
        if item is None:
            raise KeyError("任务不存在。")
        if not item.get("recoverable") or not isinstance(item.get("request"), dict):
            raise ValueError(item.get("error") or "任务缺少可恢复请求。")
        return deepcopy(item["request"])


def recover_interrupted_jobs() -> list[dict[str, Any]]:
    """Convert crash-left running jobs to explicit resumable paused jobs."""
    recovered: list[dict[str, Any]] = []
    with _LOCK:
        values = _read_queue_unlocked()
        changed = False
        for item in values:
            if item["status"] == "running":
                item["status"] = "paused"
                if item.get("recoverable"):
                    item["error"] = "应用在任务执行期间退出；已保留完成行和请求快照，可重新排队。"
                    item["error_type"] = "ProcessRestart"
                else:
                    item["error"] = (
                        "应用在任务执行期间退出，但任务含未持久化的内嵌参考音频；"
                        "请改用已保存的 voice_id 后新建任务。"
                    )
                    item["error_type"] = "UnrecoverableProcessRestart"
                item["recovery"] = {
                    "reason": "process_restart", "recovered_at": int(time.time()),
                    "completed_lines": deepcopy(item["completed_lines"]),
                }
                item["updated_at"] = int(time.time())
                item["revision"] += 1
                recovered.append(deepcopy(item))
                changed = True
            elif item["status"] == "pending" and not item.get("recoverable"):
                item["status"] = "failed"
                item["error"] = item.get("error") or "重启后无法恢复：任务没有可执行请求快照。"
                item["error_type"] = item.get("error_type") or "UnrecoverableQueueJob"
                item["updated_at"] = int(time.time())
                item["revision"] += 1
                changed = True
        if changed:
            _write_queue_unlocked(values)
    return recovered


def record_project_line_result(
    project_id: str,
    line_id: str,
    *,
    status: str,
    audio_file: str = "",
    error: str = "",
    error_type: str = "",
    metadata: dict[str, Any] | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Persist one line result and the project checkpoint atomically."""
    if status not in {"completed", "failed", "pending"}:
        raise ValueError("单句状态必须是 completed、failed 或 pending。")
    with _LOCK:
        project = load_project(project_id)
        if project is None:
            raise KeyError("工程不存在。")
        if expected_revision is not None and project["revision"] != int(expected_revision):
            raise ProjectRevisionConflict(project_id, int(expected_revision), project["revision"])
        line = next((value for value in project["lines"] if value["line_id"] == line_id), None)
        if line is None:
            raise KeyError("台词不存在。")
        checkpoint = deepcopy(project.get("checkpoint") or {})
        completed = {
            str(value) for value in checkpoint.get("completed_line_ids", []) if str(value).strip()
        }
        failed = {
            str(value) for value in checkpoint.get("failed_line_ids", []) if str(value).strip()
        }
        now = int(time.time())
        if status == "completed":
            safe_audio = Path(str(audio_file or "")).name
            if not safe_audio or safe_audio != str(audio_file):
                raise ValueError("单句输出必须是输出目录中的安全文件名。")
            target = (output_dir() / safe_audio).resolve()
            root = output_dir().resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError("单句输出不能位于输出目录之外。") from exc
            if not target.is_file() or target.suffix.lower() != ".wav":
                raise ValueError(f"单句输出不存在：{safe_audio}。")
            line["audio_file"] = safe_audio
            line["generation_metadata"] = _json_copy(metadata or {}, max_bytes=128 * 1024)
            line["generated_at"] = now
            line["dirty_fields"] = sorted(
                field for field in set(line.get("dirty_fields") or [])
                if field not in _GENERATION_DIRTY_FIELDS
            )
            line["status"] = "completed"
            line["error"] = ""
            completed.add(line_id)
            failed.discard(line_id)
            if isinstance(checkpoint.get("last_error"), dict) and checkpoint["last_error"].get("line_id") == line_id:
                checkpoint.pop("last_error", None)
        elif status == "failed":
            message = str(error or "单句生成失败，但执行器没有提供错误详情。").strip()[:8_000]
            line["status"] = "failed"
            line["error"] = message
            failed.add(line_id)
            completed.discard(line_id)
            checkpoint["last_error"] = {
                "line_id": line_id,
                "message": message,
                "error_type": str(error_type or "UnknownLineFailure")[:200],
                "at": now,
            }
        else:
            line["status"] = "pending"
            line["error"] = ""
            failed.discard(line_id)
        checkpoint.update({
            "completed_line_ids": sorted(completed),
            "failed_line_ids": sorted(failed),
            "updated_at": now,
        })
        project["checkpoint"] = checkpoint
        return save_project(project, expected_revision=project["revision"])


def project_mix_results(project_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build merge inputs from the latest successful audio of every project line."""
    project = load_project(project_id)
    if project is None:
        raise KeyError("工程不存在。")
    results: list[dict[str, Any]] = []
    missing: list[str] = []
    root = output_dir().resolve()
    for line in project["lines"]:
        safe_audio = Path(str(line.get("audio_file") or "")).name
        target = (root / safe_audio).resolve() if safe_audio else None
        if not safe_audio or target is None or not target.is_file():
            missing.append(f"{line['order']}:{line['line_id']}")
            continue
        results.append({
            "index": line["order"] - 1,
            "output": safe_audio,
            "metadata": {**deepcopy(line.get("generation_metadata") or {}), "output": str(target)},
            "role": line.get("role"),
            "line_id": line["line_id"],
            "subtitle": {
                "index": line["order"], "start_ms": line["start_ms"],
                "end_ms": line["end_ms"], "text": line["text"],
            },
        })
    if missing:
        raise ValueError("以下台词尚无可重混音频：" + "、".join(missing))
    if not results:
        raise ValueError("工程没有可重混的单句音频。")
    return project, results


def remix_project(project_id: str, *, expected_revision: int | None = None) -> dict[str, Any]:
    """Rebuild the full timeline from cached per-line audio and checkpoint it."""
    from .batch_audio import merge_batch_outputs

    project, results = project_mix_results(project_id)
    base_revision = project["revision"]
    if expected_revision is not None and base_revision != int(expected_revision):
        raise ProjectRevisionConflict(project_id, int(expected_revision), base_revision)
    target, metadata = merge_batch_outputs(
        results,
        timeline=True,
        timing_policy=project.get("timing", {}).get("policy", "preserve"),
    )
    # Detect an edit that happened while the potentially expensive remix ran.
    current = load_project(project_id)
    if current is None:
        raise KeyError("工程不存在。")
    if current["revision"] != base_revision:
        raise ProjectRevisionConflict(project_id, base_revision, current["revision"])
    checkpoint = deepcopy(current.get("checkpoint") or {})
    checkpoint["last_mix"] = {
        "output": target.name,
        "metadata": _json_copy(metadata, max_bytes=256 * 1024),
        "project_revision": base_revision,
        "created_at": int(time.time()),
    }
    checkpoint["updated_at"] = int(time.time())
    current["checkpoint"] = checkpoint
    saved = save_project(current, expected_revision=base_revision)
    return {"project": saved, "output": target.name, "metadata": metadata}


def _payload_uses_voice(payload: Any, voice_id: str) -> bool:
    if not isinstance(payload, dict):
        return False
    defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {}
    if str(defaults.get("voice_id") or "") == voice_id:
        return True
    roles = payload.get("role_voices") if isinstance(payload.get("role_voices"), dict) else {}
    if any(str(value or "") == voice_id for value in roles.values()):
        return True
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    return any(
        isinstance(item, dict) and str(item.get("voice_id") or "") == voice_id
        for item in items
    )


def find_voice_references(voice_id: str) -> list[dict[str, Any]]:
    """Return project and resumable queue references that make deletion unsafe."""
    references: list[dict[str, Any]] = []
    with _LOCK:
        projects_root = _root() / "projects"
        for path in projects_root.glob("*.json"):
            try:
                project = normalize_project(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            lines = [line["line_id"] for line in project["lines"] if line.get("voice_id") == voice_id]
            defaults_use = str(project.get("defaults", {}).get("voice_id") or "") == voice_id
            if lines or defaults_use:
                references.append({
                    "kind": "project", "project_id": project["project_id"],
                    "name": project["name"], "line_ids": lines, "defaults": defaults_use,
                })
        for job in _read_queue_unlocked():
            if job["status"] not in ACTIVE_QUEUE_STATUSES:
                continue
            if _payload_uses_voice(job.get("request"), voice_id):
                references.append({
                    "kind": "queue", "job_id": job["job_id"], "status": job["status"],
                    "project_id": str(job.get("project_id") or ""),
                })
    return references


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_BUNDLE_FILES:
        raise ValueError("工程包文件数量超限。")
    total, seen = 0, set()
    for info in infos:
        name = info.filename.replace("\\", "/")
        path = PurePosixPath(name)
        folded = name.casefold()
        if (
            not name or name.startswith(("/", "\\")) or ":" in name or ".." in path.parts
            or folded in seen or info.file_size > MAX_MEMBER_BYTES or info.is_dir()
            or ((info.external_attr >> 16) & 0o170000) == 0o120000
            or bool(info.flag_bits & 0x1)
        ):
            raise ValueError(f"工程包包含不安全成员：{name}")
        seen.add(folded)
        total += info.file_size
        if total > MAX_BUNDLE_BYTES:
            raise ValueError("工程包解压总大小超限。")
    return infos


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def export_project(project: dict[str, Any], target: Path) -> Path:
    normalized = normalize_project(project)
    exported = deepcopy(normalized)
    target = Path(target).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    audio_members: dict[str, tuple[str, Path]] = {}
    missing_audio_line_ids: list[str] = []
    total_audio_bytes = 0
    output_root = output_dir().resolve()
    for line in exported["lines"]:
        audio_name = Path(str(line.get("audio_file") or "")).name
        source = (output_root / audio_name).resolve() if audio_name else None
        if not audio_name or source is None or not source.is_file():
            if audio_name:
                missing_audio_line_ids.append(line["line_id"])
            line["audio_file"] = ""
            if line.get("status") == "completed":
                line["status"] = "pending"
            continue
        try:
            source.relative_to(output_root)
        except ValueError as exc:
            raise ValueError("工程引用了输出目录之外的音频。") from exc
        if source.suffix.lower() != ".wav" or source.stat().st_size > MAX_MEMBER_BYTES:
            raise ValueError(f"工程单句音频格式或大小无效：{audio_name}")
        total_audio_bytes += source.stat().st_size
        if total_audio_bytes > MAX_BUNDLE_BYTES:
            raise ValueError("工程音频总大小超过 1 GiB。")
        member = f"audio/{line['line_id']}.wav"
        audio_members[line["line_id"]] = (member, source)

    completed = set(exported.get("checkpoint", {}).get("completed_line_ids", []))
    completed.difference_update(missing_audio_line_ids)
    if exported.get("checkpoint"):
        exported["checkpoint"]["completed_line_ids"] = sorted(completed)
    project_bytes = json.dumps(exported, ensure_ascii=False, indent=2).encode("utf-8")
    file_hashes = {"project.json": hashlib.sha256(project_bytes).hexdigest()}
    audio_map: dict[str, str] = {}
    for line_id, (member, source) in audio_members.items():
        audio_map[line_id] = member
        file_hashes[member] = _file_sha256(source)
    manifest = {
        "schema_version": 1,
        "kind": "t8-breeze-project",
        "project_file": "project.json",
        "files": file_hashes,
        "audio": audio_map,
        "missing_audio_line_ids": missing_audio_line_ids,
    }
    fd, temporary_name = tempfile.mkstemp(suffix=".t8project.zip", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("project.json", project_bytes)
            for member, source in audio_members.values():
                archive.write(source, member)
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return target


def import_project(source: Path) -> dict[str, Any]:
    source = Path(source).expanduser().resolve()
    if not source.is_file() or source.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("工程包不存在或大小超限。")
    with zipfile.ZipFile(source, "r") as archive:
        infos = _safe_members(archive)
        info_names = {info.filename.replace("\\", "/") for info in infos}
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError("工程包清单无效。") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != 1
            or manifest.get("kind") != "t8-breeze-project"
            or not isinstance(manifest.get("files"), dict)
        ):
            raise ValueError("工程包类型或版本无效。")
        try:
            project_file = str(manifest["project_file"])
            raw = archive.read(project_file)
        except KeyError as exc:
            raise ValueError("工程包缺少 project_file。") from exc
        declared = {str(name): str(digest) for name, digest in manifest["files"].items()}
        if set(declared) | {"manifest.json"} != info_names:
            raise ValueError("工程包包含未声明文件或缺少清单文件。")
        for member, expected in declared.items():
            if member not in info_names or len(expected) != 64:
                raise ValueError("工程包文件清单无效。")
            if hashlib.sha256(archive.read(member)).hexdigest() != expected:
                raise ValueError(f"工程包哈希校验失败：{member}")
        project = normalize_project(json.loads(raw.decode("utf-8")))
        audio_map = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
        line_ids = {line["line_id"] for line in project["lines"]}
        extracted: list[Path] = []
        new_project_id = uuid.uuid4().hex
        try:
            output_root = output_dir().resolve()
            output_root.mkdir(parents=True, exist_ok=True)
            for line_id, raw_member in audio_map.items():
                if line_id not in line_ids:
                    raise ValueError("工程包音频映射引用了不存在的台词。")
                member = str(raw_member).replace("\\", "/")
                if member not in declared or PurePosixPath(member).suffix.lower() != ".wav":
                    raise ValueError("工程包音频映射无效。")
                destination = output_root / f"breeze_project_{new_project_id[:8]}_{line_id[:12]}.wav"
                temporary = destination.with_suffix(".wav.tmp")
                try:
                    with archive.open(member) as reader, temporary.open("wb") as writer:
                        shutil.copyfileobj(reader, writer, length=1024 * 1024)
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
                extracted.append(destination)
                line = next(value for value in project["lines"] if value["line_id"] == line_id)
                line["audio_file"] = destination.name
                if line.get("status") != "failed":
                    line["status"] = "completed"
            project["project_id"] = new_project_id
            project["revision"] = 0
            return save_project(project)
        except Exception:
            for path in extracted:
                path.unlink(missing_ok=True)
            raise
