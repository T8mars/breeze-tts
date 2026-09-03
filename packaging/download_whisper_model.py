from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import snapshot_download


REPOSITORY = "Systran/faster-whisper-large-v3"
REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"
REQUIRED_FILES = (
    ".gitattributes",
    "README.md",
    "config.json",
    "model.bin",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocabulary.json",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=REPOSITORY,
        revision=REVISION,
        local_dir=output_dir,
        allow_patterns=list(REQUIRED_FILES),
    )
    missing = [name for name in REQUIRED_FILES if not (output_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"Bundled Whisper model is incomplete: {missing}")
    print(f"Bundled Whisper model ready: {output_dir}")
    print(f"Repository: {REPOSITORY}@{REVISION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
