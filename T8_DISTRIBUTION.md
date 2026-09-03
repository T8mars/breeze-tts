# T8star-Aix Voice Studio distribution

This repository now contains two unofficial distribution targets built on the official Breeze TTS 2 source:

- Windows portable desktop application under `desktop/`
- ComfyUI node package under `comfyui-breeze-tts-T8/`

The implementation plan and acceptance record live in `roadmap.md`.

## Build the Windows packages

Use 64-bit PowerShell on Windows:

```powershell
.\packaging\build_portable.ps1

# Portable ZIP + large-package self-extracting EXE + release manifest/checksums
.\packaging\build_release.ps1

# GitHub Release: split the >2 GiB self-extractor into verified downloadable parts
.\packaging\New-GitHubReleaseAssets.ps1
```

The build creates a private portable CPython 3.10 runtime, installs official Breeze runtime requirements with PyTorch 2.9.1 CUDA 12.8, `faster-whisper` 1.2.1, and the pinned Whisper Large-v3 checkpoint, verifies exact versions, builds Electron, and writes SHA-256 checksums. Large-v3 is available offline at `resources/backend/models/faster-whisper-large-v3`; its output is an editable draft and never silently replaces the exact reference transcript. Version 0.3.1 adds a prominent transcript warning and backend verification gate, locks long-form Voice Design to a generated voice anchor, preserves per-line rerun and automatic remix, supports separate display/spoken text for rare-character pronunciation, and groups 23 T8 post-production presets into base, indoor, large-space, outdoor/special-space, device/communication, and creative-tone categories. One-click native vocal-event, voice-character, production-scene, and emotion/delivery presets remain clearly distinguished from post effects. Because the bundled CUDA runtime exceeds Squirrel's reliable embedded-Setup size, the release uses a verified 7-Zip self-extracting EXE and manual update manifest. The application downloads the fixed Breeze model revision only after the user accepts its license.

## Install the ComfyUI nodes

Install **Breeze TTS 2 · T8star-Aix** through ComfyUI-Manager, or clone `https://github.com/T8mars/Comfyui-breeze-tts` into `ComfyUI/custom_nodes/` and install `requirements.txt` with the ComfyUI Python. Dependencies use the official Manager pipeline; the node contains no runtime pip subprocess and does not declare Torch, Torchaudio, Transformers, Tokenizers, or NumPy. The loader validates Transformers `>=4.57,<6` and downloads the fixed official model revision into `ComfyUI/models/breeze_tts/BreezeBlue_Breeze-TTS-2` after explicit license acceptance. The desktop app instead defaults to `%APPDATA%\T8star-Aix Voice Studio\models\Breeze-TTS-2`; these paths are intentionally independent.

The ComfyUI ZIP is not a standalone application and intentionally contains no launcher. It must be installed into an existing ComfyUI. The separately built `T8star-Aix-Voice-Studio-vX.Y.Z-SelfExtract.exe` is the Windows desktop bundle with its own `T8star-Aix-Voice-Studio.exe` launcher.

- Published source: `https://github.com/T8mars/Comfyui-breeze-tts`
- Registry entry: `https://registry.comfy.org/publishers/t8star/nodes/comfyui-breeze-tts-T8`

## Legal boundary

This distribution is unofficial. Source code is Apache-2.0. Model materials and self-hosted outputs are restricted to research and non-commercial use under `MODEL_LICENSE`; commercial rights are not included. Voice cloning requires the speaker's explicit, legally sufficient consent and the rights to all submitted recordings.
