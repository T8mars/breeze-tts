from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from t8_runtime.voice_library import (
    LIBRARY_SCHEMA_VERSION,
    delete_voice,
    export_voice_bundle,
    get_voice,
    import_voice_bundle,
    list_voices,
    private_reference_path,
    rename_voice,
    save_voice,
    update_voice,
)


def _data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "data"
    monkeypatch.setenv("T8_BREEZE_DATA_DIR", str(root))
    return root


def _write_bundle(path: Path, manifest: dict, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        for name, payload in members.items():
            archive.writestr(name, payload)


def test_legacy_list_migrates_once_with_backup_and_stable_ids(monkeypatch, tmp_path):
    root = _data_dir(monkeypatch, tmp_path)
    voices_dir = root / "voices"
    voices_dir.mkdir(parents=True)
    legacy = [
        {
            "id": "kept-id",
            "name": "旁白",
            "mode": "design",
            "instruction": "平静",
            "created_at": 10,
            "updated_at": 11,
        },
        {"name": "无旧 ID", "mode": "design", "instruction": "自然"},
    ]
    raw = json.dumps(legacy, ensure_ascii=False, indent=2).encode("utf-8")
    (voices_dir / "library.json").write_bytes(raw)

    first = list_voices()
    ids = [voice["id"] for voice in first]
    assert ids[0] == "kept-id"
    assert len(ids[1]) == 32
    assert (voices_dir / "library.v1.backup.json").read_bytes() == raw
    stored = json.loads((voices_dir / "library.json").read_text(encoding="utf-8"))
    assert stored["schema_version"] == LIBRARY_SCHEMA_VERSION
    assert stored["voices"][1]["id"] == ids[1]
    assert stored["voices"][0]["language"] == "auto"
    backup_before = (voices_dir / "library.v1.backup.json").read_bytes()

    assert [voice["id"] for voice in list_voices()] == ids
    assert (voices_dir / "library.v1.backup.json").read_bytes() == backup_before


def test_create_update_search_rename_and_delete_reference(monkeypatch, tmp_path):
    _data_dir(monkeypatch, tmp_path)
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"RIFF-original")
    replacement = tmp_path / "replacement.wav"
    replacement.write_bytes(b"RIFF-replacement")

    created = save_voice(
        name="温柔旁白",
        mode="clone",
        language="zh",
        instruction="温柔自然",
        reference_text="参考文本",
        reference_source=reference,
        tags=["女声", "Narration", "女声"],
        notes="项目 A",
        preview_text="试听这一句",
        quality={"snr": 28.5, "checked": True},
    )
    assert created["has_reference"] is True
    assert created["tags"] == ["女声", "Narration"]
    private_before = private_reference_path(get_voice(created["id"], include_private=True))
    assert private_before is not None and private_before.read_bytes() == b"RIFF-original"

    renamed = rename_voice(created["id"], "主角女声")
    assert renamed["name"] == "主角女声"
    updated = update_voice(
        created["id"],
        favorite=True,
        notes="已复核",
        tags=[],
        quality={"snr": 31},
        reference_source=replacement,
    )
    assert updated["favorite"] is True
    assert updated["tags"] == []
    assert updated["quality"] == {"snr": 31}
    assert updated["updated_at"] > created["updated_at"]
    assert private_before.exists() is False
    private_after = private_reference_path(get_voice(created["id"], include_private=True))
    assert private_after is not None and private_after.read_bytes() == b"RIFF-replacement"

    assert [item["id"] for item in list_voices("复核")] == [created["id"]]
    assert [item["id"] for item in list_voices(favorite=True, language="zh")] == [created["id"]]
    assert list_voices(mode="design") == []
    with pytest.raises(ValueError, match="参考音频"):
        update_voice(created["id"], clear_reference=True)

    assert delete_voice(created["id"]) is True
    assert delete_voice(created["id"]) is False
    assert private_after.exists() is False


def test_design_profile_can_clear_reference(monkeypatch, tmp_path):
    _data_dir(monkeypatch, tmp_path)
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"RIFF-data")
    created = save_voice(
        name="设计音色",
        mode="design",
        instruction="明亮",
        reference_source=reference,
    )
    path = private_reference_path(get_voice(created["id"], include_private=True))
    assert path is not None
    cleared = update_voice(created["id"], clear_reference=True)
    assert cleared["has_reference"] is False
    assert path.exists() is False


def test_bundle_roundtrip_hashes_conflicts_and_private_reference(monkeypatch, tmp_path):
    _data_dir(monkeypatch, tmp_path)
    reference = tmp_path / "voice.flac"
    reference.write_bytes(b"fLaC\x00private voice bytes")
    created = save_voice(
        name="角色甲",
        mode="direction",
        language="en",
        instruction="calm and clear",
        reference_text="Reference text.",
        reference_source=reference,
        favorite=True,
        tags=["hero"],
    )
    bundle = export_voice_bundle(created["id"], tmp_path / "role")
    assert bundle.name.endswith(".t8voice.zip")
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        asset = archive.read(manifest["files"][0]["path"])
        assert manifest["files"][0]["sha256"] == hashlib.sha256(asset).hexdigest()
        assert "reference_file" not in manifest["voice"]

    assert delete_voice(created["id"])
    restored = import_voice_bundle(bundle, conflict="error")
    assert restored["id"] == created["id"]
    assert restored["favorite"] is True
    restored_private = private_reference_path(get_voice(restored["id"], include_private=True))
    assert restored_private is not None and restored_private.read_bytes() == reference.read_bytes()

    with pytest.raises(FileExistsError):
        import_voice_bundle(bundle, conflict="error")
    duplicate = import_voice_bundle(bundle, conflict="rename")
    assert duplicate["id"] != restored["id"]
    assert duplicate["name"].startswith("角色甲 (导入")

    update_voice(restored["id"], notes="will be replaced")
    replaced = import_voice_bundle(bundle, conflict="replace")
    assert replaced["id"] == restored["id"]
    assert replaced["notes"] == ""
    assert len(list_voices()) == 2


@pytest.mark.parametrize(
    "bad_name",
    ["../escape.wav", "/absolute.wav", "C:/drive.wav", "assets/../escape.wav", "assets/trailing. "],
)
def test_bundle_rejects_traversal_and_unsafe_windows_paths(monkeypatch, tmp_path, bad_name):
    root = _data_dir(monkeypatch, tmp_path)
    bundle = tmp_path / "unsafe.t8voice.zip"
    manifest = {
        "schema_version": 1,
        "type": "t8voice",
        "voice": {"id": "unsafe", "name": "bad", "mode": "design"},
        "files": [],
    }
    _write_bundle(bundle, manifest, {bad_name: b"bad"})
    before = list_voices()
    with pytest.raises(ValueError):
        import_voice_bundle(bundle)
    assert list_voices() == before
    assert not (root / "escape.wav").exists()


def test_bundle_rejects_casefold_duplicates_and_hash_tampering(monkeypatch, tmp_path):
    _data_dir(monkeypatch, tmp_path)
    duplicate = tmp_path / "duplicate.t8voice.zip"
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("assets/Voice.wav", b"a")
        archive.writestr("assets/voice.wav", b"b")
    with pytest.raises(ValueError, match="大小写重复"):
        import_voice_bundle(duplicate)

    tampered = tmp_path / "tampered.t8voice.zip"
    payload = b"modified"
    manifest = {
        "schema_version": 1,
        "type": "t8voice",
        "voice": {
            "id": "tampered",
            "name": "tampered",
            "mode": "clone",
            "reference_text": "text",
            "reference_member": "assets/reference.wav",
        },
        "files": [
            {"path": "assets/reference.wav", "size": len(payload), "sha256": "0" * 64}
        ],
    }
    _write_bundle(tampered, manifest, {"assets/reference.wav": payload})
    with pytest.raises(ValueError, match="哈希"):
        import_voice_bundle(tampered)
    assert list_voices() == []


def test_private_reference_never_escapes_library(monkeypatch, tmp_path):
    _data_dir(monkeypatch, tmp_path)
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"secret")
    assert private_reference_path({"reference_file": "../../secret.wav"}) is None
    assert outside.read_bytes() == b"secret"
