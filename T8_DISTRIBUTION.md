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
```

The build creates a private portable CPython 3.10 runtime, installs official Breeze runtime requirements with PyTorch 2.9.1 CUDA 12.8, verifies exact versions, builds Electron, and writes SHA-256 checksums. Because the bundled CUDA runtime exceeds Squirrel's reliable embedded-Setup size, the release uses a verified 7-Zip self-extracting EXE and manual update manifest instead of publishing a broken Squirrel installer. The application downloads the fixed official model revision only after the user accepts the model license. Version 0.2.2 adds an IndexTTS 2.5-inspired light launcher/workbench, Voice Library 2.0, editable bidirectional dialogue timelines, native Breeze per-line direction controls, persistent history/queue recovery, secure project/voice bundles, hardened desktop logging, draft recovery, resilient Electron process shutdown, and verified checkpoint resume/merge while retaining long-text, Whisper, SRT, Fast/Eager, and opt-in HTTPS updates.

## Install the ComfyUI nodes

Install **Breeze TTS 2 · T8star-Aix** through ComfyUI-Manager, or clone `https://github.com/T8mars/Comfyui-breeze-tts` into `ComfyUI/custom_nodes/` and install `requirements.txt` with the ComfyUI Python. Dependencies use the official Manager pipeline; the node contains no runtime pip subprocess and does not declare Torch, Torchaudio, Transformers, Tokenizers, or NumPy. The loader validates Transformers `>=4.57,<6` and downloads the official model into `ComfyUI/models/breeze_tts/` after explicit license acceptance.

- Published source: `https://github.com/T8mars/Comfyui-breeze-tts`
- Registry entry: `https://registry.comfy.org/publishers/t8star/nodes/comfyui-breeze-tts-T8`

## Legal boundary

This distribution is unofficial. Source code is Apache-2.0. Model materials and self-hosted outputs are restricted to research and non-commercial use under `MODEL_LICENSE`; commercial rights are not included. Voice cloning requires the speaker's explicit, legally sufficient consent and the rights to all submitted recordings.
