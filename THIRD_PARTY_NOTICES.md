# Third-party notices

T8star-Aix Voice Studio is an unofficial Breeze TTS 2 integration. It does not claim affiliation with or endorsement by BreezeBlue or RESONIA, INC.

## Breeze TTS 2 source

The included Breeze TTS 2 source is from `breezeblue-ai/breeze-tts` at revision `ca632ce6c4d05f7985da4eab29b1a5d445b43f7b`, licensed under Apache License 2.0. See `LICENSE`.

## Breeze TTS 2 model

The application downloads `BreezeBlue/Breeze-TTS-2` at pinned revision `c1c8ca18b70b30822735633991d9ebf4898e47d4`. Model weights, tokenizer/codec model materials, derivative models, and self-hosted outputs are governed by the BreezeBlue Research and Non-Commercial License Agreement.

Breeze TTS 2 is licensed under the BreezeBlue Research and Non-Commercial License Agreement. Copyright (c) 2026 RESONIA, INC. All Rights Reserved.

The application requires each recipient to accept the model agreement before download or use. Commercial use is not granted. Users must hold the necessary rights and consent for every reference voice.

## ComfyUI node compatibility code

The `comfyui-breeze-tts-T8` compatibility inference path is adapted from `Saganaki22/ComfyUI-Breeze-TTS-2` revision `0aaa6fd8a4694a9b504970ade4078be2d4620a0a`, Apache License 2.0, and from the official Breeze TTS 2 source. Vendored codec code includes material from Qwen3-TTS / qwen-tts 0.1.1 under Apache License 2.0.

Detailed file-level attribution is included in `comfyui-breeze-tts-T8/THIRD_PARTY_NOTICES.md`.

## Bundled runtimes

The Windows portable artifact includes CPython under the Python Software Foundation License and Electron/Chromium plus Python packages under their respective licenses. Their license files and package metadata remain inside the bundled runtime/application. `requirements-desktop.lock.txt` records exact Python package versions.

The package also includes `faster-whisper` 1.2.1 and the
`Systran/faster-whisper-small` converted checkpoint. Both report the MIT
license. `WHISPER_NOTICE.md` records the pinned model revision and source URL,
and distinguishes the bundled Small model from optional models downloaded on
first use.
