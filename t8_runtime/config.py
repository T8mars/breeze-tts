from __future__ import annotations

import os
from pathlib import Path


PROJECT_VERSION = "0.3.6"
CORE_REVISION = "e2c5ac2f54fe15daa94237a7dbf31e446660a4c9"
MODEL_REPOSITORY = "BreezeBlue/Breeze-TTS-2"
MODEL_REVISION = "799624c0b4a1daa8db6d28bbd9850043c0270734"
SAMPLE_RATE = 24_000


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_model_dir() -> Path:
    from .settings_store import load_settings

    persisted = load_settings().get("model_dir", "").strip()
    if persisted:
        return Path(persisted).expanduser().resolve()
    configured = os.environ.get("T8_BREEZE_MODEL_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return project_root() / "models" / "Breeze-TTS-2"


def user_data_dir() -> Path:
    configured = os.environ.get("T8_BREEZE_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "T8star-Aix" / "BreezeTTS"
    return project_root() / "userdata"


def output_dir() -> Path:
    from .settings_store import load_settings

    persisted = load_settings().get("output_dir", "").strip()
    if persisted:
        return Path(persisted).expanduser().resolve()
    configured = os.environ.get("T8_BREEZE_OUTPUT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    documents = Path.home() / "Documents"
    return documents / "T8star-Aix Breeze TTS" / "outputs"
