from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .config import user_data_dir


LIBRARY_SCHEMA_VERSION = 2
BUNDLE_SCHEMA_VERSION = 1
MAX_REFERENCE_BYTES = 128 * 1024 * 1024
MAX_BUNDLE_MEMBERS = 16
MAX_BUNDLE_BYTES = MAX_REFERENCE_BYTES + 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024

_LOCK = threading.RLock()
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus"}


class VoiceInUseError(RuntimeError):
    def __init__(self, voice_id: str, references: list[dict[str, Any]]):
        self.voice_id = voice_id
        self.references = references
        labels = []
        for reference in references[:8]:
            if reference.get("kind") == "project":
                labels.append(f"工程 {reference.get('name') or reference.get('project_id')}")
            else:
                labels.append(f"队列任务 {reference.get('job_id')} ({reference.get('status')})")
        suffix = "、".join(labels)
        if len(references) > 8:
            suffix += f" 等 {len(references)} 处"
        super().__init__(f"音色 {voice_id} 正被引用，不能删除：{suffix}。")


def _library_dir() -> Path:
    return user_data_dir() / "voices"


def _library_path() -> Path:
    return _library_dir() / "library.json"


def _backup_path() -> Path:
    return _library_dir() / "library.v1.backup.json"


def _now() -> int:
    return int(time.time())


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(target: Path, payload: Any) -> None:
    _atomic_write_bytes(
        target,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
    )


def _clean_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise ValueError(f"文本不能超过 {limit} 个字符。")
    return text


def _clean_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    if not isinstance(value, (list, tuple)):
        raise ValueError("tags 必须是字符串数组。")
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        tag = _clean_text(raw, limit=32)
        key = tag.casefold()
        if tag and key not in seen:
            result.append(tag)
            seen.add(key)
    if len(result) > 32:
        raise ValueError("标签不能超过 32 个。")
    return result


def _clean_quality(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("quality 必须是对象。")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("quality 必须包含可序列化的 JSON 数据。") from exc
    if len(encoded.encode("utf-8")) > 32 * 1024:
        raise ValueError("quality 数据不能超过 32 KiB。")
    return decoded


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and _SAFE_ID.fullmatch(value) is not None


def _stable_legacy_id(item: dict[str, Any], index: int, occurrence: int = 0) -> str:
    identity = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"t8star-aix:breeze-tts:legacy-voice:{index}:{occurrence}:{identity}",
    ).hex


def _validate_relative_member(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise ValueError("压缩包包含无效文件名。")
    normalised = name.replace("\\", "/")
    pure = PurePosixPath(normalised)
    if (
        pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or re.match(r"^[A-Za-z]:", normalised)
        or normalised.startswith("//")
    ):
        raise ValueError("压缩包包含路径穿越或绝对路径。")
    for part in pure.parts:
        if part.endswith((" ", ".")) or ":" in part:
            raise ValueError("压缩包包含 Windows 不安全路径。")
    return pure.as_posix()


def _normalise_item(
    item: dict[str, Any],
    *,
    index: int = 0,
    used_ids: set[str] | None = None,
) -> dict[str, Any]:
    used_ids = used_ids if used_ids is not None else set()
    candidate = item.get("id")
    if not _valid_id(candidate) or candidate in used_ids:
        occurrence = 0
        candidate = _stable_legacy_id(item, index, occurrence)
        while candidate in used_ids:
            occurrence += 1
            candidate = _stable_legacy_id(item, index, occurrence)
    voice_id = str(candidate)
    used_ids.add(voice_id)

    mode = str(item.get("mode") or "design").lower()
    if mode not in {"design", "clone", "direction"}:
        mode = "design"
    language = str(item.get("language") or "auto").lower()
    if language not in {"auto", "zh", "en"}:
        language = "auto"
    timestamps = item.get("timestamps") if isinstance(item.get("timestamps"), dict) else {}
    try:
        created_at = int(item.get("created_at") or timestamps.get("created_at") or _now())
    except (TypeError, ValueError):
        created_at = _now()
    try:
        updated_at = int(item.get("updated_at") or timestamps.get("updated_at") or created_at)
    except (TypeError, ValueError):
        updated_at = created_at

    reference_file = str(item.get("reference_file") or "")
    if reference_file:
        try:
            reference_file = _validate_relative_member(reference_file)
        except ValueError:
            reference_file = ""

    name = _clean_text(item.get("name") or "未命名音色", limit=80)
    return {
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "id": voice_id,
        "name": name or "未命名音色",
        "mode": mode,
        "language": language,
        "instruction": _clean_text(item.get("instruction"), limit=4_000),
        "reference_text": _clean_text(item.get("reference_text"), limit=20_000),
        "reference_file": reference_file,
        "tags": _clean_tags(item.get("tags")),
        "favorite": bool(item.get("favorite", False)),
        "notes": _clean_text(item.get("notes"), limit=10_000),
        "preview_text": _clean_text(item.get("preview_text"), limit=2_000),
        "quality": _clean_quality(item.get("quality")),
        "created_at": created_at,
        "updated_at": updated_at,
        "timestamps": {"created_at": created_at, "updated_at": updated_at},
    }


def _empty_document() -> dict[str, Any]:
    return {"schema_version": LIBRARY_SCHEMA_VERSION, "voices": []}


def _migrate_legacy(payload: list[Any], raw: bytes) -> dict[str, Any]:
    used_ids: set[str] = set()
    voices = [
        _normalise_item(item, index=index, used_ids=used_ids)
        for index, item in enumerate(payload)
        if isinstance(item, dict)
    ]
    backup = _backup_path()
    if not backup.exists():
        _atomic_write_bytes(backup, raw)
    document = {"schema_version": LIBRARY_SCHEMA_VERSION, "voices": voices}
    _atomic_write_json(_library_path(), document)
    return document


def _read_document() -> dict[str, Any]:
    try:
        raw = _library_path().read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        return _empty_document()
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _empty_document()
    if isinstance(payload, list):
        return _migrate_legacy(payload, raw)
    if not isinstance(payload, dict) or payload.get("schema_version") != LIBRARY_SCHEMA_VERSION:
        return _empty_document()
    raw_voices = payload.get("voices")
    if not isinstance(raw_voices, list):
        return _empty_document()
    used_ids: set[str] = set()
    voices = [
        _normalise_item(item, index=index, used_ids=used_ids)
        for index, item in enumerate(raw_voices)
        if isinstance(item, dict)
    ]
    return {"schema_version": LIBRARY_SCHEMA_VERSION, "voices": voices}


def _read() -> list[dict[str, Any]]:
    """Return private profiles. Kept as a list for v0.1.x internal compatibility."""
    return _read_document()["voices"]


def _write(items: list[dict[str, Any]]) -> None:
    used_ids: set[str] = set()
    normalised = [
        _normalise_item(item, index=index, used_ids=used_ids)
        for index, item in enumerate(items)
    ]
    _atomic_write_json(
        _library_path(),
        {"schema_version": LIBRARY_SCHEMA_VERSION, "voices": normalised},
    )


def _public(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "id": item["id"],
        "name": item["name"],
        "mode": item["mode"],
        "language": item.get("language", "auto"),
        "instruction": item.get("instruction", ""),
        "reference_text": item.get("reference_text", ""),
        "has_reference": bool(item.get("reference_file")),
        "tags": list(item.get("tags") or []),
        "favorite": bool(item.get("favorite", False)),
        "notes": item.get("notes", ""),
        "preview_text": item.get("preview_text", ""),
        "quality": dict(item.get("quality") or {}),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "timestamps": dict(item.get("timestamps") or {}),
    }


def list_voices(
    query: str = "",
    *,
    favorite: bool | None = None,
    mode: str | None = None,
    language: str | None = None,
) -> list[dict[str, Any]]:
    query_key = query.strip().casefold()
    tag_mode = mode.lower() if mode else None
    tag_language = language.lower() if language else None
    with _LOCK:
        result: list[dict[str, Any]] = []
        for item in _read():
            if favorite is not None and bool(item.get("favorite")) is not favorite:
                continue
            if tag_mode and item.get("mode") != tag_mode:
                continue
            if tag_language and item.get("language") != tag_language:
                continue
            haystack = "\n".join(
                [
                    str(item.get("name") or ""),
                    str(item.get("notes") or ""),
                    str(item.get("instruction") or ""),
                    " ".join(item.get("tags") or []),
                ]
            ).casefold()
            if query_key and query_key not in haystack:
                continue
            result.append(_public(item))
        return result


def get_voice(voice_id: str, *, include_private: bool = False) -> dict[str, Any] | None:
    with _LOCK:
        item = next((entry for entry in _read() if entry.get("id") == voice_id), None)
        if item is None:
            return None
        return dict(item) if include_private else _public(item)


def _validate_mode_language(mode: str, language: str) -> tuple[str, str]:
    mode = mode.strip().lower()
    language = language.strip().lower()
    if mode not in {"design", "clone", "direction"}:
        raise ValueError("音色模式必须是 design、clone 或 direction。")
    if language not in {"auto", "zh", "en"}:
        raise ValueError("语言必须是 auto、zh 或 en。")
    return mode, language


def _copy_reference(reference_source: Path, voice_id: str) -> str:
    source = Path(reference_source)
    if not source.is_file():
        raise ValueError("参考音频文件不存在。")
    size = source.stat().st_size
    if size <= 0 or size > MAX_REFERENCE_BYTES:
        raise ValueError("参考音频为空或超过 128 MiB。")
    suffix = source.suffix.lower()
    if suffix not in _AUDIO_SUFFIXES:
        raise ValueError("参考音频格式不受支持。")
    relative = f"references/{voice_id}-{uuid.uuid4().hex}{suffix}"
    target = _library_dir() / Path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        with source.open("rb") as reader, temporary.open("wb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return relative


def save_voice(
    *,
    name: str,
    mode: str,
    instruction: str,
    reference_text: str = "",
    reference_source: Path | None = None,
    language: str = "auto",
    tags: list[str] | tuple[str, ...] | str | None = None,
    favorite: bool = False,
    notes: str = "",
    preview_text: str = "",
    quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    name = _clean_text(name, limit=80)
    if not name:
        raise ValueError("音色名称不能为空。")
    mode, language = _validate_mode_language(mode, language)
    reference_text = _clean_text(reference_text, limit=20_000)
    if mode in {"clone", "direction"} and (reference_source is None or not reference_text):
        raise ValueError("克隆/导演音色必须包含参考音频和准确逐字稿。")
    now = _now()
    voice_id = uuid.uuid4().hex
    reference_file = ""
    with _LOCK:
        try:
            if reference_source is not None:
                reference_file = _copy_reference(reference_source, voice_id)
            item = {
                "schema_version": LIBRARY_SCHEMA_VERSION,
                "id": voice_id,
                "name": name,
                "mode": mode,
                "language": language,
                "instruction": _clean_text(instruction, limit=4_000),
                "reference_text": reference_text,
                "reference_file": reference_file,
                "tags": _clean_tags(tags),
                "favorite": bool(favorite),
                "notes": _clean_text(notes, limit=10_000),
                "preview_text": _clean_text(preview_text, limit=2_000),
                "quality": _clean_quality(quality),
                "created_at": now,
                "updated_at": now,
                "timestamps": {"created_at": now, "updated_at": now},
            }
            items = _read()
            items.append(item)
            _write(items)
        except Exception:
            if reference_file:
                (_library_dir() / Path(reference_file)).unlink(missing_ok=True)
            raise
        return _public(item)


def update_voice(
    voice_id: str,
    *,
    name: str | None = None,
    mode: str | None = None,
    instruction: str | None = None,
    reference_text: str | None = None,
    reference_source: Path | None = None,
    clear_reference: bool = False,
    language: str | None = None,
    tags: list[str] | tuple[str, ...] | str | None = None,
    favorite: bool | None = None,
    notes: str | None = None,
    preview_text: str | None = None,
    quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if clear_reference and reference_source is not None:
        raise ValueError("不能同时清除和替换参考音频。")
    with _LOCK:
        items = _read()
        selected = next((item for item in items if item.get("id") == voice_id), None)
        if selected is None:
            raise KeyError(voice_id)
        updated = dict(selected)
        if name is not None:
            updated["name"] = _clean_text(name, limit=80)
            if not updated["name"]:
                raise ValueError("音色名称不能为空。")
        if mode is not None:
            updated["mode"] = mode
        if language is not None:
            updated["language"] = language
        updated["mode"], updated["language"] = _validate_mode_language(
            str(updated.get("mode") or "design"), str(updated.get("language") or "auto")
        )
        if instruction is not None:
            updated["instruction"] = _clean_text(instruction, limit=4_000)
        if reference_text is not None:
            updated["reference_text"] = _clean_text(reference_text, limit=20_000)
        if tags is not None:
            updated["tags"] = _clean_tags(tags)
        if favorite is not None:
            updated["favorite"] = bool(favorite)
        if notes is not None:
            updated["notes"] = _clean_text(notes, limit=10_000)
        if preview_text is not None:
            updated["preview_text"] = _clean_text(preview_text, limit=2_000)
        if quality is not None:
            updated["quality"] = _clean_quality(quality)

        old_reference = str(selected.get("reference_file") or "")
        new_reference = old_reference
        if clear_reference:
            new_reference = ""
        elif reference_source is not None:
            new_reference = _copy_reference(reference_source, voice_id)
        updated["reference_file"] = new_reference
        if updated["mode"] in {"clone", "direction"} and (
            not new_reference or not str(updated.get("reference_text") or "").strip()
        ):
            if new_reference and new_reference != old_reference:
                (_library_dir() / Path(new_reference)).unlink(missing_ok=True)
            raise ValueError("克隆/导演音色必须包含参考音频和准确逐字稿。")
        updated_at = _now()
        if updated_at <= int(selected.get("updated_at") or 0):
            updated_at = int(selected.get("updated_at") or 0) + 1
        updated["updated_at"] = updated_at
        updated["timestamps"] = {
            "created_at": int(selected.get("created_at") or updated_at),
            "updated_at": updated_at,
        }
        replacement = [updated if item.get("id") == voice_id else item for item in items]
        try:
            _write(replacement)
        except Exception:
            if new_reference and new_reference != old_reference:
                (_library_dir() / Path(new_reference)).unlink(missing_ok=True)
            raise
        if old_reference and old_reference != new_reference:
            _delete_private_file(old_reference)
        return _public(updated)


def rename_voice(voice_id: str, name: str) -> dict[str, Any]:
    return update_voice(voice_id, name=name)


def _resolve_private_file(relative: str) -> Path | None:
    if not relative:
        return None
    try:
        safe = _validate_relative_member(relative)
    except ValueError:
        return None
    root = _library_dir().resolve()
    target = (root / Path(safe)).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def _delete_private_file(relative: str) -> None:
    target = _resolve_private_file(relative)
    if target is not None and target.is_file():
        target.unlink(missing_ok=True)


def delete_voice(voice_id: str, *, force: bool = False) -> bool:
    if not force:
        # Imported lazily to avoid coupling voice-bundle parsing to workspace
        # persistence during application startup.
        from .workspace_store import find_voice_references

        references = find_voice_references(voice_id)
        if references:
            raise VoiceInUseError(voice_id, references)
    with _LOCK:
        items = _read()
        selected = next((item for item in items if item.get("id") == voice_id), None)
        if selected is None:
            return False
        _write([item for item in items if item.get("id") != voice_id])
        _delete_private_file(str(selected.get("reference_file") or ""))
        return True


def private_reference_path(item: dict[str, Any]) -> Path | None:
    target = _resolve_private_file(str(item.get("reference_file") or ""))
    return target if target is not None and target.is_file() else None


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def export_voice_bundle(voice_id: str, destination: Path) -> Path:
    """Export one profile and its private reference into a verified .t8voice.zip."""
    destination = Path(destination)
    if not destination.name.lower().endswith(".t8voice.zip"):
        destination = destination.with_name(destination.name + ".t8voice.zip")
    with _LOCK:
        voice = get_voice(voice_id, include_private=True)
        if voice is None:
            raise KeyError(voice_id)
        exported = dict(voice)
        exported.pop("reference_file", None)
        files: list[dict[str, Any]] = []
        payloads: dict[str, bytes] = {}
        reference = private_reference_path(voice)
        if voice.get("reference_file") and reference is None:
            raise ValueError("音色参考音频丢失，无法导出。")
        if reference is not None:
            payload = reference.read_bytes()
            if len(payload) > MAX_REFERENCE_BYTES:
                raise ValueError("参考音频超过 128 MiB。")
            member = f"assets/reference{reference.suffix.lower()}"
            payloads[member] = payload
            files.append({"path": member, "size": len(payload), "sha256": _sha256(payload)})
            exported["reference_member"] = member
        manifest = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "type": "t8voice",
            "voice": exported,
            "files": files,
        }
        manifest_bytes = json.dumps(
            manifest, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", manifest_bytes)
                for name, payload in payloads.items():
                    archive.writestr(name, payload)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination


def _read_bundle(source: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    source = Path(source)
    if not source.is_file():
        raise ValueError("音色包不存在。")
    try:
        with zipfile.ZipFile(source, "r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_BUNDLE_MEMBERS:
                raise ValueError("音色包成员数量无效。")
            seen: set[str] = set()
            total = 0
            info_by_name: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                safe = _validate_relative_member(info.filename)
                key = safe.casefold()
                if key in seen:
                    raise ValueError("音色包包含 Windows 大小写重复路径。")
                seen.add(key)
                if info.is_dir() or ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError("音色包不能包含目录或符号链接。")
                if info.file_size < 0 or info.file_size > MAX_BUNDLE_BYTES:
                    raise ValueError("音色包成员过大。")
                total += info.file_size
                if total > MAX_BUNDLE_BYTES:
                    raise ValueError("音色包解压后总大小超限。")
                info_by_name[safe] = info
            manifest_info = info_by_name.get("manifest.json")
            if manifest_info is None or manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise ValueError("音色包缺少有效 manifest.json。")
            try:
                manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("音色包清单无效。") from exc
            if not isinstance(manifest, dict):
                raise ValueError("音色包清单无效。")
            if manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION or manifest.get("type") != "t8voice":
                raise ValueError("不支持的音色包版本。")
            declared = manifest.get("files")
            if not isinstance(declared, list):
                raise ValueError("音色包文件清单无效。")
            payloads: dict[str, bytes] = {}
            declared_names = {"manifest.json"}
            for entry in declared:
                if not isinstance(entry, dict):
                    raise ValueError("音色包文件清单无效。")
                name = _validate_relative_member(str(entry.get("path") or ""))
                if name == "manifest.json" or name in declared_names:
                    raise ValueError("音色包文件清单包含重复项。")
                declared_names.add(name)
                info = info_by_name.get(name)
                if info is None:
                    raise ValueError("音色包缺少清单声明的文件。")
                payload = archive.read(info)
                try:
                    expected_size = int(entry.get("size"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("音色包文件大小声明无效。") from exc
                if len(payload) != expected_size or _sha256(payload) != entry.get("sha256"):
                    raise ValueError("音色包文件哈希校验失败。")
                payloads[name] = payload
            if set(info_by_name) != declared_names:
                raise ValueError("音色包包含未在清单中声明的文件。")
            return manifest, payloads
    except zipfile.BadZipFile as exc:
        raise ValueError("音色包不是有效 ZIP 文件。") from exc


def import_voice_bundle(source: Path, *, conflict: str = "rename") -> dict[str, Any]:
    """Import a verified bundle using error, replace, rename/new_id or keep_both policy."""
    aliases = {"overwrite": "replace", "new_id": "rename", "keep_both": "rename"}
    conflict = aliases.get(conflict, conflict)
    if conflict not in {"error", "replace", "rename"}:
        raise ValueError("冲突策略必须是 error、replace 或 rename。")
    manifest, payloads = _read_bundle(source)
    raw_voice = manifest.get("voice")
    if not isinstance(raw_voice, dict):
        raise ValueError("音色包缺少音色资料。")
    imported = _normalise_item(raw_voice)
    reference_member = str(raw_voice.get("reference_member") or "")
    if reference_member:
        reference_member = _validate_relative_member(reference_member)
        if reference_member not in payloads:
            raise ValueError("音色包缺少参考音频。")
        if Path(reference_member).suffix.lower() not in _AUDIO_SUFFIXES:
            raise ValueError("音色包参考音频格式不受支持。")
    if imported["mode"] in {"clone", "direction"} and (
        not reference_member or not imported["reference_text"]
    ):
        raise ValueError("克隆/导演音色包必须包含参考音频和准确逐字稿。")

    with _LOCK:
        items = _read()
        id_match = next((item for item in items if item["id"] == imported["id"]), None)
        name_match = next(
            (item for item in items if item["name"].casefold() == imported["name"].casefold()),
            None,
        )
        conflict_item = id_match or name_match
        if conflict_item is not None and conflict == "error":
            raise FileExistsError("同 ID 或同名音色已存在。")
        if conflict_item is not None and conflict == "replace":
            imported["id"] = conflict_item["id"]
        elif conflict_item is not None and conflict == "rename":
            imported["id"] = uuid.uuid4().hex
            base = imported["name"]
            existing_names = {item["name"].casefold() for item in items}
            number = 1
            candidate = f"{base} (导入)"
            while candidate.casefold() in existing_names:
                number += 1
                candidate = f"{base} (导入 {number})"
            imported["name"] = candidate

        old_reference = ""
        if conflict_item is not None and conflict == "replace":
            old_reference = str(conflict_item.get("reference_file") or "")
        new_reference = ""
        if reference_member:
            suffix = Path(reference_member).suffix.lower()
            new_reference = f"references/{imported['id']}-{uuid.uuid4().hex}{suffix}"
            _atomic_write_bytes(_library_dir() / Path(new_reference), payloads[reference_member])
        imported["reference_file"] = new_reference
        timestamp = _now()
        imported["updated_at"] = timestamp
        imported["timestamps"] = {
            "created_at": int(imported.get("created_at") or timestamp),
            "updated_at": timestamp,
        }
        if conflict_item is not None and conflict == "replace":
            updated_items = [
                imported if item["id"] == conflict_item["id"] else item for item in items
            ]
        else:
            updated_items = [*items, imported]
        try:
            _write(updated_items)
        except Exception:
            _delete_private_file(new_reference)
            raise
        if old_reference and old_reference != new_reference:
            _delete_private_file(old_reference)
        return _public(imported)


export_voice = export_voice_bundle
import_voice = import_voice_bundle
