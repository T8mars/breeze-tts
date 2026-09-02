from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import threading
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from .config import (
    CORE_REVISION,
    MODEL_REVISION,
    PROJECT_VERSION,
    default_model_dir,
    output_dir,
    project_root,
    user_data_dir,
)
from .batch_audio import merge_batch_outputs
from .diagnostics import collect_diagnostics, write_redacted_report
from .model_store import (
    DOWNLOAD_MANAGER,
    ensure_model_integrity,
    record_license_acceptance,
    validate_model_dir,
)
from .runtime_manager import DEFAULT_INSTRUCTION, GenerationRequest, RuntimeManager
from .dialogue import apply_timeline_edit, new_project, normalize_project, parse_dialogue, to_srt
from .script_tools import parse_multi_role_script, parse_srt
from .settings_store import load_settings, update_settings
from .transcription import transcribe_audio, whisper_available
from .workspace_store import (
    ProjectRevisionConflict,
    QueueRevisionConflict,
    append_history,
    delete_project,
    export_project,
    import_project,
    list_history,
    list_projects,
    load_project,
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
from .voice_library import (
    VoiceInUseError,
    delete_voice,
    export_voice_bundle,
    get_voice,
    import_voice_bundle,
    list_voices,
    private_reference_path,
    save_voice,
    update_voice,
)


LOGGER = logging.getLogger("t8_breeze_desktop")
MAX_REFERENCE_BYTES = 100 * 1024 * 1024
MAX_REFERENCE_SECONDS = 60.0
ALLOWED_REFERENCE_SUFFIXES = {".wav", ".flac", ".ogg", ".mp3"}


class ModelPathRequest(BaseModel):
    model_dir: str
    verify_hashes: bool = False


class DownloadRequest(BaseModel):
    model_dir: str
    accept_model_license: bool


class SelectModelRequest(ModelPathRequest):
    accept_model_license: bool = False


class DirectorySettingRequest(BaseModel):
    path: str


class TextToolRequest(BaseModel):
    text: str


class RuntimeLoadRequest(BaseModel):
    fast_all: bool = False
    max_new_tokens: int = 1500


class DialogueParseRequest(BaseModel):
    kind: str
    text: str
    default_role: str = "旁白"


class ProjectSaveRequest(BaseModel):
    project: dict[str, Any]
    expected_revision: int | None = None


class TimelineEditRequest(BaseModel):
    line_id: str
    start_ms: int
    end_ms: int
    revision: int


class BundlePathRequest(BaseModel):
    path: str


class QueueRequest(BaseModel):
    payload: dict[str, Any]


class QueueUpdateRequest(BaseModel):
    updates: dict[str, Any]


class ProjectActionRequest(BaseModel):
    expected_revision: int | None = None


class VoiceCreateRequest(BaseModel):
    name: str
    mode: str = "design"
    instruction: str = DEFAULT_INSTRUCTION
    reference_text: str = ""
    reference_filename: str = "reference.wav"
    reference_audio_base64: str = ""
    language: str = "auto"
    tags: list[str] = []
    favorite: bool = False
    notes: str = ""
    preview_text: str = ""


class VoiceUpdateRequest(BaseModel):
    name: str | None = None
    mode: str | None = None
    instruction: str | None = None
    reference_text: str | None = None
    reference_filename: str = "reference.wav"
    reference_audio_base64: str = ""
    clear_reference: bool = False
    language: str | None = None
    tags: list[str] | None = None
    favorite: bool | None = None
    notes: str | None = None
    preview_text: str | None = None


class TranscriptionRequest(BaseModel):
    reference_filename: str
    reference_audio_base64: str
    model_size: str = "small"
    language: str | None = None


def _configure_logging() -> None:
    data = user_data_dir()
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        logs / "backend.log", maxBytes=10 * 1024 * 1024, backupCount=4, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, stream_handler], force=True)


def _ui_dir() -> Path:
    configured = os.environ.get("T8_BREEZE_UI_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return project_root() / "desktop" / "src"


def _decode_reference(
    payload: dict[str, Any], *, max_seconds: float = MAX_REFERENCE_SECONDS
) -> Path | None:
    encoded = str(payload.get("reference_audio_base64") or "").strip()
    if not encoded:
        return None
    if "," in encoded and encoded.lower().startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("参考音频不是有效的 Base64 数据。") from exc
    if not raw or len(raw) > MAX_REFERENCE_BYTES:
        raise ValueError("参考音频为空或超过 100 MiB 限制。")
    suffix = Path(str(payload.get("reference_filename") or "reference.wav")).suffix.lower()
    if suffix not in ALLOWED_REFERENCE_SUFFIXES:
        raise ValueError("参考音频仅支持 WAV、FLAC、OGG 或 MP3。")
    cache = user_data_dir() / "cache" / "references"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"ref_{uuid.uuid4().hex}{suffix}"
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        temporary.write_bytes(raw)
        temporary.replace(target)
        info = sf.info(target)
        if info.frames <= 0 or info.samplerate <= 0:
            raise ValueError("参考音频为空或无法读取。")
        duration = float(info.frames) / float(info.samplerate)
        if duration > max_seconds:
            raise ValueError(f"参考音频不能超过 {max_seconds:.0f} 秒。")
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    return target


def _cleanup_reference_cache(max_age_seconds: float = 24 * 60 * 60) -> int:
    cache = user_data_dir() / "cache" / "references"
    if not cache.is_dir():
        return 0
    import time

    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in cache.iterdir():
        if not path.is_file() or not (path.name.startswith("ref_") or path.suffix == ".tmp"):
            continue
        try:
            if path.stat().st_mtime <= cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            LOGGER.warning("Unable to remove stale reference cache file: %s", path)
    return removed


def _pcm16(audio: np.ndarray) -> bytes:
    values = np.asarray(audio, dtype=np.float32).reshape(-1)
    return (np.clip(values, -1.0, 1.0) * 32767.0).astype("<i2", copy=False).tobytes()


def _apply_line_direction(payload: dict[str, Any], *, base_instruction: str) -> dict[str, Any]:
    merged = dict(payload)
    direction_mode = str(merged.get("direction_mode") or "inherit")
    if direction_mode == "override":
        instruction = str(merged.get("direction_text") or "").strip()
        if not instruction:
            raise ValueError("逐句覆盖模式必须填写自然语言演绎指令。")
        merged["instruction"] = instruction
        if merged.get("mode") == "clone":
            merged["mode"] = "direction"
    elif direction_mode == "neutral":
        merged["instruction"] = DEFAULT_INSTRUCTION
        if merged.get("mode") == "clone":
            merged["mode"] = "direction"
    elif direction_mode == "inherit":
        merged["instruction"] = base_instruction
    else:
        raise ValueError("逐句演绎模式必须是 inherit、override 或 neutral。")
    return merged


def _apply_voice_profile(payload: dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    voice_id = str(payload.get("voice_id") or "").strip()
    if not voice_id:
        return _apply_line_direction(
            payload, base_instruction=str(payload.get("instruction") or DEFAULT_INSTRUCTION)
        ), None
    profile = get_voice(voice_id, include_private=True)
    if profile is None:
        raise ValueError(f"音色不存在：{voice_id}")
    merged = dict(payload)
    merged["mode"] = profile["mode"]
    profile_instruction = profile.get("instruction") or DEFAULT_INSTRUCTION
    merged["instruction"] = profile_instruction
    merged["reference_text"] = profile.get("reference_text") or ""
    reference = private_reference_path(profile)
    if profile["mode"] in {"clone", "direction"} and reference is None:
        raise ValueError(f"音色参考音频丢失：{profile['name']}")
    return _apply_line_direction(merged, base_instruction=profile_instruction), reference


def _generation_request(payload: dict[str, Any], reference_path: Path | None) -> GenerationRequest:
    mode = str(payload.get("mode") or "design")
    default_cfg = 1.0 if mode == "clone" else 4.0
    cfg_scale = payload.get("cfg_scale")
    seed = payload.get("seed")
    max_new_tokens = payload.get("max_new_tokens")
    return GenerationRequest(
        mode=mode,
        text=str(payload.get("text") or ""),
        instruction=str(payload.get("instruction") or DEFAULT_INSTRUCTION),
        ref_audio_path=reference_path,
        ref_text=str(payload.get("reference_text") or "") or None,
        cfg_scale=float(default_cfg if cfg_scale in {None, ""} else cfg_scale),
        seed=int(42 if seed in {None, ""} else seed),
        fast_all=bool(payload.get("fast_all", False)),
        max_new_tokens=int(1500 if max_new_tokens in {None, ""} else max_new_tokens),
    )


def _expand_batch(payload: dict[str, Any]) -> list[dict[str, Any]]:
    defaults = payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {}
    raw_items = payload.get("items")
    if isinstance(payload.get("srt_text"), str) and payload["srt_text"].strip():
        raw_items = [{"text": item["text"], "subtitle": item} for item in parse_srt(payload["srt_text"])]
    elif isinstance(payload.get("script"), str) and payload["script"].strip():
        mapping = payload.get("role_voices") if isinstance(payload.get("role_voices"), dict) else {}
        raw_items = []
        for item in parse_multi_role_script(payload["script"]):
            expanded = {"text": item["text"], "role": item["role"]}
            voice_id = mapping.get(item["role"]) or defaults.get("voice_id")
            if voice_id:
                expanded["voice_id"] = voice_id
            raw_items.append(expanded)
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("批量任务至少需要一个项目、SRT 或多角色脚本。")
    if len(raw_items) > 100:
        raise ValueError("单次批量任务不能超过 100 项。")
    result = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise ValueError(f"批量任务第 {index + 1} 项格式无效。")
        result.append({**defaults, **item})
    return result


def _is_benign_windows_proactor_reset(
    context: dict[str, Any], *, platform_name: str | None = None
) -> bool:
    """Match only the noisy Proactor disconnect callback seen on Windows.

    A reset raised by application code, a different WinError, or a different
    asyncio callback must still reach the loop's normal exception handler.
    """
    if (platform_name or os.name) != "nt":
        return False
    exception = context.get("exception")
    if not isinstance(exception, ConnectionResetError) or getattr(exception, "winerror", None) != 10054:
        return False
    callback_text = " ".join(
        str(value) for value in (context.get("message"), context.get("handle")) if value is not None
    )
    return "_ProactorBasePipeTransport._call_connection_lost" in callback_text


def _install_asyncio_exception_handler(loop: asyncio.AbstractEventLoop):
    previous = loop.get_exception_handler()

    def handler(current_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        if _is_benign_windows_proactor_reset(context):
            LOGGER.debug("Ignored benign Windows WebSocket disconnect reset (WinError 10054)")
            return
        if previous is not None:
            previous(current_loop, context)
        else:
            current_loop.default_exception_handler(context)

    loop.set_exception_handler(handler)
    return previous, handler


def create_app(model_dir: Path | None = None) -> FastAPI:
    _configure_logging()
    removed = _cleanup_reference_cache()
    recovered_jobs = recover_interrupted_jobs()
    LOGGER.info("Starting Breeze desktop service %s; removed %d stale reference files", PROJECT_VERSION, removed)
    if recovered_jobs:
        LOGGER.warning("Recovered %d interrupted queue job(s) as paused", len(recovered_jobs))
    selected_model = Path(model_dir or default_model_dir()).expanduser().resolve()
    runtime = RuntimeManager(selected_model)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        loop = asyncio.get_running_loop()
        previous, handler = _install_asyncio_exception_handler(loop)
        application.state.asyncio_loop = loop
        application.state.previous_asyncio_exception_handler = previous
        application.state.t8_asyncio_exception_handler = handler
        try:
            yield
        finally:
            if not loop.is_closed() and loop.get_exception_handler() is handler:
                loop.set_exception_handler(previous)

    app = FastAPI(
        title="T8star-Aix Voice Studio · Breeze TTS 2 integration",
        version=PROJECT_VERSION,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.state.runtime = runtime

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": PROJECT_VERSION,
            "core_revision": CORE_REVISION,
            "model_revision": MODEL_REVISION,
            "runtime": runtime.status(),
        }

    @app.get("/api/diagnostics")
    def diagnostics() -> dict[str, Any]:
        report = collect_diagnostics()
        report["runtime"] = runtime.status()
        report["model"] = validate_model_dir(runtime.model_dir, verify_hashes=False)
        return report

    @app.post("/api/diagnostics/export")
    def export_diagnostics() -> dict[str, str]:
        target = user_data_dir() / "diagnostics" / "breeze-diagnostics.json"
        return {"path": str(write_redacted_report(target))}

    @app.post("/api/models/validate")
    def validate_model(request: ModelPathRequest) -> dict[str, Any]:
        model_path = Path(request.model_dir).expanduser().resolve()
        if request.verify_hashes:
            return ensure_model_integrity(model_path, force=True)
        return validate_model_dir(model_path, verify_hashes=False)

    @app.post("/api/models/select")
    def select_model(request: SelectModelRequest) -> dict[str, Any]:
        model_path = Path(request.model_dir).expanduser().resolve()
        report = ensure_model_integrity(model_path, force=request.verify_hashes)
        if not report["valid"]:
            raise HTTPException(status_code=400, detail=report)
        if request.accept_model_license:
            record_license_acceptance(model_path)
            report = validate_model_dir(model_path, verify_hashes=False)
        if not report["license_accepted"]:
            raise HTTPException(status_code=403, detail="请先阅读并接受 BreezeBlue 模型许可证。")
        try:
            runtime.select_model_dir(model_path)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        update_settings(model_dir=str(model_path))
        return {"runtime": runtime.status(), "model": report}

    @app.post("/api/models/download")
    def download_model(request: DownloadRequest) -> dict[str, Any]:
        try:
            return DOWNLOAD_MANAGER.start(
                Path(request.model_dir), accepted=request.accept_model_license
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (RuntimeError, OSError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/models/download")
    def download_status() -> dict[str, Any]:
        return DOWNLOAD_MANAGER.snapshot()

    @app.post("/api/models/download/cancel")
    def cancel_download() -> dict[str, Any]:
        return DOWNLOAD_MANAGER.cancel()

    @app.get("/api/runtime")
    def runtime_status() -> dict[str, Any]:
        return runtime.status()

    @app.post("/api/runtime/load")
    def load_runtime(request: RuntimeLoadRequest) -> dict[str, Any]:
        try:
            runtime.load(fast_all=request.fast_all, max_new_tokens=request.max_new_tokens)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return runtime.status()

    @app.post("/api/runtime/unload")
    def unload_runtime() -> dict[str, Any]:
        try:
            runtime.unload()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return runtime.status()

    @app.get("/api/settings")
    def settings() -> dict[str, str]:
        persisted = load_settings()
        return {
            **persisted,
            "model_directory": str(runtime.model_dir),
            "output_directory": str(output_dir()),
            "model_dir": str(runtime.model_dir),
            "output_dir": str(output_dir()),
        }

    @app.post("/api/settings/output-directory")
    def set_output_directory(request: DirectorySettingRequest) -> dict[str, str]:
        if not request.path.strip():
            raise HTTPException(status_code=400, detail="输出目录不能为空。")
        target = Path(request.path).expanduser().resolve()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"无法创建输出目录：{exc}") from exc
        if not target.is_dir():
            raise HTTPException(status_code=400, detail="输出路径不是目录。")
        update_settings(output_dir=str(target))
        os.environ["T8_BREEZE_OUTPUT_DIR"] = str(target)
        return {"output_directory": str(target), "output_dir": str(target)}

    @app.get("/api/capabilities")
    def capabilities() -> dict[str, Any]:
        return {
            "long_text": True,
            "batch": True,
            "multi_role": True,
            "voice_library": True,
            "srt": True,
            "whisper": whisper_available(),
            "fast_24gb": True,
            "editable_timeline": True,
            "per_line_direction": True,
            "projects": True,
            "persistent_queue": True,
            "queue_resume": True,
            "history": True,
            "voice_bundle": True,
            "project_remix": True,
            "safe_voice_delete": True,
        }

    @app.post("/api/dialogue/parse")
    def dialogue_parse(request: DialogueParseRequest) -> dict[str, Any]:
        try:
            return parse_dialogue(request.kind, request.text, default_role=request.default_role)
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/dialogue/srt")
    def dialogue_srt(request: ProjectSaveRequest) -> dict[str, str]:
        try:
            return {"srt": to_srt(request.project)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects")
    def projects() -> dict[str, Any]:
        return {"projects": list_projects()}

    @app.post("/api/projects/new")
    def project_new() -> dict[str, Any]:
        return save_project(new_project())

    @app.get("/api/projects/{project_id}")
    def project_get(project_id: str) -> dict[str, Any]:
        project = load_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="工程不存在。")
        return project

    @app.put("/api/projects/{project_id}")
    def project_save(project_id: str, request: ProjectSaveRequest) -> dict[str, Any]:
        payload = dict(request.project)
        payload["project_id"] = project_id
        try:
            return save_project(payload, expected_revision=request.expected_revision)
        except ProjectRevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/projects/{project_id}/timeline")
    def project_timeline(project_id: str, request: TimelineEditRequest) -> dict[str, Any]:
        project = load_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="工程不存在。")
        try:
            updated = apply_timeline_edit(project, **request.model_dump())
            return save_project(updated, expected_revision=request.revision)
        except (ProjectRevisionConflict, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/projects/{project_id}")
    def project_delete(project_id: str) -> dict[str, bool]:
        if not delete_project(project_id):
            raise HTTPException(status_code=404, detail="工程不存在。")
        return {"deleted": True}

    @app.post("/api/projects/{project_id}/remix")
    def project_remix(project_id: str, request: ProjectActionRequest) -> dict[str, Any]:
        try:
            return remix_project(project_id, expected_revision=request.expected_revision)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ProjectRevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/projects/export")
    def project_export(request: ProjectSaveRequest) -> dict[str, str]:
        project = normalize_project(request.project)
        target = user_data_dir() / "exports" / f"{project['project_id']}.t8project.zip"
        return {"path": str(export_project(project, target))}

    @app.post("/api/projects/import")
    def project_import(request: BundlePathRequest) -> dict[str, Any]:
        try:
            return import_project(Path(request.path))
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/history")
    def history(limit: int = 100) -> dict[str, Any]:
        return {"history": list_history(limit)}

    @app.get("/api/queue")
    def queue() -> dict[str, Any]:
        return {"jobs": queue_snapshot()}

    @app.post("/api/queue")
    def queue_create(request: QueueRequest) -> dict[str, Any]:
        return queue_put(request.payload)

    @app.patch("/api/queue/{job_id}")
    def queue_patch(job_id: str, request: QueueUpdateRequest) -> dict[str, Any]:
        try:
            return queue_update(job_id, request.updates)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except QueueRevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/queue/{job_id}/resume")
    def queue_resume(job_id: str) -> dict[str, Any]:
        try:
            return {"payload": queue_resume_payload(job_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/voices")
    def voices(
        query: str = "", favorite: bool | None = None,
        mode: str | None = None, language: str | None = None,
    ) -> dict[str, Any]:
        return {"voices": list_voices(query, favorite=favorite, mode=mode, language=language)}

    @app.post("/api/voices")
    def create_voice(request: VoiceCreateRequest) -> dict[str, Any]:
        reference_path: Path | None = None
        try:
            if request.reference_audio_base64.strip():
                reference_path = _decode_reference(request.model_dump())
            return save_voice(
                name=request.name,
                mode=request.mode,
                instruction=request.instruction,
                reference_text=request.reference_text,
                reference_source=reference_path,
                language=request.language,
                tags=request.tags,
                favorite=request.favorite,
                notes=request.notes,
                preview_text=request.preview_text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            if reference_path is not None:
                reference_path.unlink(missing_ok=True)

    @app.patch("/api/voices/{voice_id}")
    def edit_voice(voice_id: str, request: VoiceUpdateRequest) -> dict[str, Any]:
        reference_path: Path | None = None
        try:
            if request.reference_audio_base64.strip():
                reference_path = _decode_reference(request.model_dump())
            values = request.model_dump(exclude_unset=True)
            for key in ("reference_filename", "reference_audio_base64"):
                values.pop(key, None)
            values["reference_source"] = reference_path
            return update_voice(voice_id, **values)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="音色不存在。") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            if reference_path is not None:
                reference_path.unlink(missing_ok=True)

    @app.post("/api/voices/{voice_id}/export")
    def voice_export(voice_id: str) -> dict[str, str]:
        target = user_data_dir() / "exports" / f"{voice_id}.t8voice.zip"
        try:
            return {"path": str(export_voice_bundle(voice_id, target))}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="音色不存在。") from exc

    @app.post("/api/voices/import")
    def voice_import(request: BundlePathRequest) -> dict[str, Any]:
        try:
            return import_voice_bundle(Path(request.path), conflict="rename")
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/voices/{voice_id}")
    def remove_voice(voice_id: str) -> dict[str, bool]:
        try:
            deleted = delete_voice(voice_id)
        except VoiceInUseError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="音色不存在。")
        return {"deleted": True}

    @app.post("/api/tools/parse-srt")
    def parse_srt_tool(request: TextToolRequest) -> dict[str, Any]:
        try:
            return {"segments": parse_srt(request.text)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/tools/parse-multi-role")
    def parse_multi_role_tool(request: TextToolRequest) -> dict[str, Any]:
        return {"segments": parse_multi_role_script(request.text)}

    @app.post("/api/tools/transcribe")
    def transcribe(request: TranscriptionRequest) -> dict[str, Any]:
        if request.model_size not in {"tiny", "base", "small", "medium", "large-v3"}:
            raise HTTPException(status_code=400, detail="不支持的 Whisper 模型规格。")
        reference_path: Path | None = None
        try:
            reference_path = _decode_reference(request.model_dump(), max_seconds=600.0)
            if reference_path is None:
                raise ValueError("音频不能为空。")
            return transcribe_audio(
                reference_path, model_size=request.model_size, language=request.language
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            if reference_path is not None:
                reference_path.unlink(missing_ok=True)

    @app.get("/api/license", response_class=PlainTextResponse)
    def model_license() -> str:
        root = project_root()
        candidates = (
            root / "MODEL_LICENSE",
            root / "comfyui-breeze-tts-T8" / "MODEL_LICENSE",
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        raise HTTPException(status_code=503, detail="模型许可证文件缺失。")

    @app.get("/api/outputs/{filename}")
    def get_output(filename: str):
        safe_name = Path(filename).name
        target = output_dir() / safe_name
        if not target.is_file() or target.suffix.lower() not in {".wav", ".json"}:
            raise HTTPException(status_code=404, detail="输出文件不存在。")
        return FileResponse(target)

    @app.websocket("/ws/generate")
    async def generate(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        host = websocket.headers.get("host")
        if origin and origin not in {f"http://{host}", f"https://{host}"}:
            await websocket.close(code=1008, reason="Origin not allowed")
            return
        await websocket.accept()
        reference_path: Path | None = None
        temporary_reference_path: Path | None = None
        cancel_event = threading.Event()
        receiver: asyncio.Task | None = None
        try:
            payload = json.loads(await websocket.receive_text())
            payload, profile_reference = _apply_voice_profile(payload)
            if profile_reference is not None:
                reference_path = profile_reference
            else:
                temporary_reference_path = _decode_reference(payload)
                reference_path = temporary_reference_path
            request = _generation_request(payload, reference_path)
            request.validate()
            model_report = validate_model_dir(runtime.model_dir, verify_hashes=False)
            if not model_report["valid"] or not model_report["license_accepted"]:
                raise PermissionError("模型不完整，或尚未接受 BreezeBlue 模型许可证。")
            await websocket.send_json({"type": "start", "sample_rate": 24_000})
            loop = asyncio.get_running_loop()
            chunk_queue: asyncio.Queue[bytes] = asyncio.Queue()

            def on_chunk(audio: np.ndarray) -> None:
                loop.call_soon_threadsafe(chunk_queue.put_nowait, _pcm16(audio))

            async def receive_control() -> None:
                try:
                    while True:
                        message = await websocket.receive_text()
                        control = json.loads(message)
                        if control.get("type") == "cancel":
                            cancel_event.set()
                            return
                except (WebSocketDisconnect, RuntimeError):
                    cancel_event.set()

            receiver = asyncio.create_task(receive_control())
            generation = asyncio.create_task(
                asyncio.to_thread(
                    runtime.generate,
                    request,
                    cancel_event=cancel_event,
                    on_chunk=on_chunk,
                )
            )
            while not generation.done() or not chunk_queue.empty():
                try:
                    chunk = await asyncio.wait_for(chunk_queue.get(), timeout=0.1)
                    await websocket.send_bytes(chunk)
                except asyncio.TimeoutError:
                    continue
            output_path, metadata = await generation
            history_item = append_history({
                "kind": "single",
                "mode": request.mode,
                "text": request.text,
                "voice_id": payload.get("voice_id", ""),
                "instruction": request.instruction,
                "output": output_path.name,
                "metadata": metadata,
                "status": "completed",
            })
            await websocket.send_json(
                {
                    "type": "complete",
                    "output": output_path.name,
                    "metadata": metadata,
                    "history": history_item,
                }
            )
        except InterruptedError:
            try:
                await websocket.send_json({"type": "cancelled", "message": "生成已取消。"})
            except Exception:
                pass
        except WebSocketDisconnect:
            cancel_event.set()
        except Exception as exc:
            LOGGER.exception("Generation failed")
            try:
                await websocket.send_json(
                    {"type": "error", "message": str(exc), "error_type": type(exc).__name__}
                )
            except Exception:
                pass
        finally:
            cancel_event.set()
            if receiver is not None:
                receiver.cancel()
            if temporary_reference_path is not None:
                temporary_reference_path.unlink(missing_ok=True)
            try:
                await websocket.close()
            except Exception:
                pass

    @app.websocket("/ws/batch")
    async def batch_generate(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        host = websocket.headers.get("host")
        if origin and origin not in {f"http://{host}", f"https://{host}"}:
            await websocket.close(code=1008, reason="Origin not allowed")
            return
        await websocket.accept()
        cancel_event = threading.Event()
        receiver: asyncio.Task | None = None
        temporary_paths: set[Path] = set()
        queue_job: dict[str, Any] | None = None
        current_line_key: int | str | None = None
        current_project_line_id = ""
        latest_project: dict[str, Any] | None = None
        remix_status = "not_requested"
        remix_reason = "未关联已保存工程，返回的是本次任务合并结果。"
        remix_output: str | None = None
        project_attachment_failed = False
        project_attachment_reason = ""
        project_expected_revision: int | None = None
        try:
            received = json.loads(await websocket.receive_text())
            resume_job_id = str(received.get("resume_job_id") or "").strip()
            payload = queue_resume_payload(resume_job_id) if resume_job_id else received
            project_id = str(payload.get("project_id") or "").strip()
            raw_project_revision = payload.get("project_revision")
            if project_id:
                latest_project = load_project(project_id)
                if latest_project is None:
                    remix_status = "unavailable"
                    remix_reason = "工程不存在或尚未保存，无法执行完整工程重混。"
                elif raw_project_revision in {None, ""}:
                    project_attachment_failed = True
                    project_attachment_reason = (
                        "请求缺少 project_revision；为避免覆盖已保存工程，本次仅执行 one-shot 生成。"
                    )
                    remix_status = "unavailable"
                    remix_reason = project_attachment_reason
                else:
                    try:
                        project_expected_revision = int(raw_project_revision)
                    except (TypeError, ValueError):
                        project_attachment_failed = True
                        project_attachment_reason = (
                            "请求中的 project_revision 无效；为避免覆盖已保存工程，"
                            "本次仅执行 one-shot 生成。"
                        )
                        remix_status = "unavailable"
                        remix_reason = project_attachment_reason
                    else:
                        remix_status = "pending"
                        remix_reason = ""
            queue_job = queue_claim(payload, job_id=resume_job_id or None)
            all_items = _expand_batch(payload)
            if (
                project_id
                and latest_project is not None
                and any(not str(item.get("line_id") or "").strip() for item in all_items)
            ):
                missing_line_reason = (
                    "关联已保存工程的批量项目必须全部提供 line_id；"
                    "为避免错误回填，本次仅执行 one-shot 生成。"
                )
                project_attachment_failed = True
                project_attachment_reason = (
                    f"{project_attachment_reason} {missing_line_reason}".strip()
                )
                remix_status = "unavailable"
                remix_reason = project_attachment_reason
            completed = set(queue_job.get("completed_lines") or [])
            indexed_items = [
                (index, item)
                for index, item in enumerate(all_items)
                if index not in completed and str(item.get("line_id") or "") not in completed
            ]
            if not indexed_items and all_items:
                raise ValueError("任务中的所有台词都已在检查点完成；请直接重混音或新建任务。")
            model_report = validate_model_dir(runtime.model_dir, verify_hashes=False)
            if not model_report["valid"] or not model_report["license_accepted"]:
                raise PermissionError("模型不完整，或尚未接受 BreezeBlue 模型许可证。")

            async def receive_control() -> None:
                try:
                    while True:
                        control = json.loads(await websocket.receive_text())
                        if control.get("type") == "cancel":
                            cancel_event.set()
                            return
                except (WebSocketDisconnect, RuntimeError, json.JSONDecodeError):
                    cancel_event.set()

            receiver = asyncio.create_task(receive_control())
            checkpoint_results = queue_job.get("results")
            if not isinstance(checkpoint_results, dict):
                checkpoint_results = {}
            results = []
            for index, raw_item in enumerate(all_items):
                line_id = str(raw_item.get("line_id") or "")
                line_key: int | str = line_id or index
                if index not in completed and line_id not in completed:
                    continue
                checkpoint_result = checkpoint_results.get(str(line_key))
                if not isinstance(checkpoint_result, dict):
                    raise ValueError(
                        f"任务检查点缺少第 {index + 1} 句结果，无法安全恢复；请重新生成该任务。"
                    )
                results.append(checkpoint_result)
                checkpoint_project_revision = checkpoint_result.get("project_revision")
                if (
                    project_expected_revision is not None
                    and checkpoint_project_revision not in {None, ""}
                ):
                    project_expected_revision = int(checkpoint_project_revision)
            await websocket.send_json({
                "type": "batch_start", "total": len(indexed_items),
                "original_total": len(all_items), "resumed": bool(completed),
                "job_id": queue_job["job_id"], "completed_lines": list(completed),
            })
            for index, raw_item in indexed_items:
                if cancel_event.is_set():
                    raise InterruptedError("批量生成已由用户取消。")
                current_project_line_id = str(raw_item.get("line_id") or "")
                current_line_key = current_project_line_id or index
                item, profile_reference = _apply_voice_profile(raw_item)
                temporary: Path | None = None
                reference = profile_reference
                if reference is None:
                    temporary = _decode_reference(item)
                    reference = temporary
                    if temporary is not None:
                        temporary_paths.add(temporary)
                request = _generation_request(item, reference)
                request.validate()
                await websocket.send_json(
                    {
                        "type": "item_start",
                        "index": index,
                        "total": len(indexed_items),
                        "text": request.text,
                        "role": item.get("role"),
                    }
                )
                output_path, metadata = await asyncio.to_thread(
                    runtime.generate, request, cancel_event=cancel_event
                )
                result = {
                    "index": index,
                    "output": output_path.name,
                    "metadata": metadata,
                    "role": item.get("role"),
                    "subtitle": item.get("subtitle"),
                    "line_id": current_project_line_id,
                }
                results.append(result)
                if project_id and current_project_line_id and not project_attachment_failed:
                    try:
                        latest_project = record_project_line_result(
                            project_id,
                            current_project_line_id,
                            status="completed",
                            audio_file=output_path.name,
                            metadata=metadata,
                            expected_revision=project_expected_revision,
                        )
                        project_expected_revision = latest_project["revision"]
                        result["project_revision"] = project_expected_revision
                    except (KeyError, ProjectRevisionConflict) as exc:
                        # Unsaved in-memory projects remain valid one-shot jobs.
                        project_attachment_failed = True
                        project_attachment_reason = str(exc).strip("'")
                        LOGGER.info("Batch line result belongs to an unsaved project %s", project_id)
                queue_job = queue_checkpoint(queue_job["job_id"], current_line_key, result)
                await websocket.send_json({"type": "item_complete", **result})
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                    temporary_paths.discard(temporary)
            results.sort(key=lambda value: int(value.get("index", 0)))
            remixed_project: dict[str, Any] | None = None
            if project_id and latest_project is not None and not project_attachment_failed:
                try:
                    remixed_project = await asyncio.to_thread(
                        remix_project,
                        project_id,
                        expected_revision=project_expected_revision,
                    )
                    latest_project = remixed_project["project"]
                    remix_status = "completed"
                    remix_output = remixed_project["output"]
                    remix_reason = ""
                except (KeyError, ValueError, ProjectRevisionConflict) as exc:
                    latest_project = load_project(project_id)
                    remix_status = "unavailable"
                    remix_reason = str(exc)
                    LOGGER.info("Project %s is not yet fully remixable: %s", project_id, exc)
            elif project_attachment_failed:
                latest_project = load_project(project_id)
                remix_status = "unavailable"
                remix_reason = (
                    "生成结果未能回填到工程台词，未执行完整工程重混："
                    f"{project_attachment_reason or '台词不存在或工程 revision 已变化。'}"
                )
            if remixed_project is not None:
                merged_path = output_dir() / remixed_project["output"]
                merged_metadata = remixed_project["metadata"]
            else:
                merged_path, merged_metadata = await asyncio.to_thread(
                    merge_batch_outputs,
                    results,
                    timeline=bool(str(payload.get("srt_text") or "").strip()) or bool(payload.get("timeline")),
                    timing_policy=str(payload.get("timing_policy") or "preserve"),
                )
            if project_id:
                latest_project = load_project(project_id)
            queue_job = queue_update(queue_job["job_id"], {"status": "completed", "error": ""})
            history_item = append_history({
                "kind": "batch",
                "count": len(results),
                "output": merged_path.name,
                "metadata": merged_metadata,
                "status": "completed",
            })
            await websocket.send_json(
                {
                    "type": "batch_complete",
                    "results": results,
                    "merged_output": merged_path.name,
                    "merged_metadata": merged_metadata,
                    "history": history_item,
                    "job": queue_job,
                    "project": latest_project,
                    "project_revision": latest_project["revision"] if latest_project else None,
                    "full_project_mix": remix_status == "completed",
                    "remix": {
                        "status": remix_status,
                        "reason": remix_reason,
                        "output": remix_output,
                    },
                }
            )
        except InterruptedError:
            project_id = str(locals().get("payload", {}).get("project_id") or "").strip()
            latest_project = load_project(project_id) if project_id else None
            cancelled_remix_status = "unavailable" if latest_project else "not_requested"
            cancelled_remix_reason = (
                "任务已取消，未执行完整工程重混。" if latest_project
                else "未关联已保存工程，任务已取消。"
            )
            if queue_job is not None:
                try:
                    queue_job = queue_update(queue_job["job_id"], {
                        "status": "paused",
                        "error": "任务由用户取消；已保留完成行，可重新排队。",
                        "error_type": "UserCancelled",
                        "failed_line": current_line_key,
                    })
                except Exception:
                    LOGGER.exception("Failed to checkpoint cancelled queue job")
            try:
                await websocket.send_json({
                    "type": "cancelled", "message": "批量生成已取消。", "job": queue_job,
                    "project": latest_project,
                    "project_revision": latest_project["revision"] if latest_project else None,
                    "full_project_mix": False,
                    "remix": {
                        "status": cancelled_remix_status,
                        "reason": cancelled_remix_reason,
                        "output": None,
                    },
                })
            except Exception:
                pass
        except WebSocketDisconnect:
            cancel_event.set()
            if queue_job is not None and queue_job.get("status") == "running":
                try:
                    queue_update(queue_job["job_id"], {
                        "status": "paused",
                        "error": "客户端连接已断开；已保留完成行，可重新排队。",
                        "error_type": "ClientDisconnected",
                        "failed_line": current_line_key,
                    })
                except Exception:
                    LOGGER.exception("Failed to checkpoint disconnected queue job")
        except Exception as exc:
            LOGGER.exception("Batch generation failed")
            project_id = str(locals().get("payload", {}).get("project_id") or "").strip()
            if project_id and current_project_line_id and not project_attachment_failed:
                try:
                    latest_project = record_project_line_result(
                        project_id,
                        current_project_line_id,
                        status="failed",
                        error=str(exc),
                        error_type=type(exc).__name__,
                        expected_revision=project_expected_revision,
                    )
                except (KeyError, ProjectRevisionConflict) as attachment_exc:
                    project_attachment_failed = True
                    project_attachment_reason = str(attachment_exc).strip("'")
                    LOGGER.info("Could not attach failed line to project %s", project_id)
                    latest_project = load_project(project_id)
            elif project_id:
                latest_project = load_project(project_id)
            if queue_job is not None:
                try:
                    queue_job = queue_update(queue_job["job_id"], {
                        "status": "failed", "error": str(exc),
                        "error_type": type(exc).__name__, "failed_line": current_line_key,
                    })
                except Exception:
                    LOGGER.exception("Failed to persist queue failure")
            try:
                await websocket.send_json(
                    {
                        "type": "error", "message": str(exc),
                        "error_type": type(exc).__name__, "failed_line": current_line_key,
                        "job": queue_job,
                        "project": latest_project,
                        "project_revision": latest_project["revision"] if latest_project else None,
                        "full_project_mix": False,
                        "remix": {
                            "status": "unavailable" if latest_project else "not_requested",
                            "reason": (
                                "当前单句生成失败，且失败状态未能回填到工程台词："
                                f"{project_attachment_reason}"
                                if project_attachment_failed else
                                "当前单句生成失败，未执行完整工程重混。" if latest_project else
                                "未关联已保存工程，且本次生成失败。"
                            ),
                            "output": None,
                        },
                    }
                )
            except Exception:
                pass
        finally:
            cancel_event.set()
            if receiver is not None:
                receiver.cancel()
            for path in temporary_paths:
                path.unlink(missing_ok=True)
            try:
                await websocket.close()
            except Exception:
                pass

    ui = _ui_dir()
    if ui.is_dir():
        app.mount("/", StaticFiles(directory=str(ui), html=True), name="ui")
    else:
        @app.get("/")
        def missing_ui() -> JSONResponse:
            return JSONResponse({"error": f"Desktop UI not found: {ui}"}, status_code=404)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="T8star-Aix Voice Studio desktop service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--model-dir", type=Path, default=default_model_dir())
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Desktop service only permits loopback binding.")
    import uvicorn

    uvicorn.run(
        create_app(args.model_dir),
        host="127.0.0.1",
        port=args.port,
        log_level="info",
        ws_max_size=(MAX_REFERENCE_BYTES * 4 // 3) + (1024 * 1024),
    )


if __name__ == "__main__":
    main()
