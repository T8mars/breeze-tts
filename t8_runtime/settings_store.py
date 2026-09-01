from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


_LOCK = threading.RLock()
_ALLOWED_KEYS = {"model_dir", "output_dir"}


def _user_data_dir() -> Path:
    configured = os.environ.get("T8_BREEZE_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "T8star-Aix" / "BreezeTTS"
    return Path(__file__).resolve().parents[1] / "userdata"


def settings_path() -> Path:
    return _user_data_dir() / "settings" / "runtime-settings.json"


def load_settings() -> dict[str, str]:
    with _LOCK:
        try:
            payload = json.loads(settings_path().read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            key: str(value)
            for key, value in payload.items()
            if key in _ALLOWED_KEYS and isinstance(value, str) and value.strip()
        }


def update_settings(**updates: Any) -> dict[str, str]:
    invalid = set(updates) - _ALLOWED_KEYS
    if invalid:
        raise ValueError(f"不支持的设置项：{', '.join(sorted(invalid))}")
    with _LOCK:
        payload = load_settings()
        for key, value in updates.items():
            text = str(value or "").strip()
            if text:
                payload[key] = str(Path(text).expanduser().resolve())
            else:
                payload.pop(key, None)
        target = settings_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return dict(payload)
