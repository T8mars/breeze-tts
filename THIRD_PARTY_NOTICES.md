# Third-party notices

T8star-Aix Voice Studio is an unofficial Breeze TTS 2 integration. It does not claim affiliation with or endorsement by BreezeBlue or RESONIA, INC.

## Breeze TTS 2 source

The included Breeze TTS 2 source is synchronized through `breezeblue-ai/breeze-tts` revision `e2c5ac2f54fe15daa94237a7dbf31e446660a4c9`, licensed under Apache License 2.0. See `LICENSE`.

## Breeze TTS 2 model

The application downloads `BreezeBlue/Breeze-TTS-2` at pinned revision `799624c0b4a1daa8db6d28bbd9850043c0270734`. Model weights, tokenizer/codec model materials, derivative models, and self-hosted outputs are governed by the BreezeBlue Research and Non-Commercial License Agreement version 1.1.

Breeze TTS 2 is licensed under the BreezeBlue Research and Non-Commercial License Agreement. Copyright (c) 2026 RESONIA, INC. All Rights Reserved.

As an application safeguard, this integration requires the local user to acknowledge the model agreement before download or use. Commercial rights to the open-weight model and self-hosted outputs are not granted. Paid hosted BreezeBlue output is governed separately by BreezeBlue's service terms. Users must hold the necessary rights and consent for every reference voice.

## ComfyUI node compatibility code

The `comfyui-breeze-tts-T8` compatibility inference path is adapted from `Saganaki22/ComfyUI-Breeze-TTS-2` revision `0aaa6fd8a4694a9b504970ade4078be2d4620a0a`, Apache License 2.0, and from the official Breeze TTS 2 source. Vendored codec code includes material from Qwen3-TTS / qwen-tts 0.1.1 under Apache License 2.0.

Detailed file-level attribution is included in `comfyui-breeze-tts-T8/THIRD_PARTY_NOTICES.md`.

## Bundled runtimes

The Windows portable artifact includes CPython under the Python Software Foundation License and Electron/Chromium plus Python packages under their respective licenses. Their license files and package metadata remain inside the bundled runtime/application. `requirements-desktop.lock.txt` records exact Python package versions.

The Windows runtime also bundles FlashAttention 2.8.3 under the BSD 3-Clause
License. The binary is a community-built CPython 3.10 / PyTorch 2.9 / CUDA 12.8
wheel published at `kingbri1/flash-attention` on GitHub Releases. Its download
URL and SHA-256 digest are pinned in `requirements-desktop.in` and
`requirements-desktop.lock.txt`; the build does not compile FlashAttention from
source. Upstream source is `Dao-AILab/flash-attention` tag `v2.8.3`.

The package also includes `faster-whisper` 1.2.1 and the
`Systran/faster-whisper-large-v3` converted checkpoint. Both report the MIT
license. `WHISPER_NOTICE.md` records the pinned model revision and source URL,
and records that transcription output remains an unverified draft until the
user checks it word-for-word against the reference recording.
