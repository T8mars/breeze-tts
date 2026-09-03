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

The build creates a private portable CPython 3.10 runtime, installs official Breeze runtime requirements with PyTorch 2.9.1 CUDA 12.8, verifies exact versions, builds Electron, and writes SHA-256 checksums. Because the bundled CUDA runtime exceeds Squirrel's reliable embedded-Setup size, the release uses a verified 7-Zip self-extracting EXE and manual update manifest instead of publishing a broken Squirrel installer. The application downloads the fixed official model revision only after the user accepts the model license. Version 0.2.6 exposes inline vocal-event syntax across the standalone desktop UI and all four ComfyUI canvas workflows while retaining editable timelines, per-line rerun and direction, Voice Library 2.0, history/queue recovery, secure bundles, long-text, Whisper, SRT, Fast/Eager, and Transformers 4.57.x/5.x compatibility.

## Install the ComfyUI nodes

Install **Breeze TTS 2 · T8star-Aix** through ComfyUI-Manager, or clone `https://github.com/T8mars/Comfyui-breeze-tts` into `ComfyUI/custom_nodes/` and install `requirements.txt` with the ComfyUI Python. Dependencies use the official Manager pipeline; the node contains no runtime pip subprocess and does not declare Torch, Torchaudio, Transformers, Tokenizers, or NumPy. The loader validates Transformers `>=4.57,<6` and downloads the official model into `ComfyUI/models/breeze_tts/` after explicit license acceptance.

The ComfyUI ZIP is not a standalone application and intentionally contains no launcher. It must be installed into an existing ComfyUI. The separately built `T8star-Aix-Voice-Studio-vX.Y.Z-SelfExtract.exe` is the Windows desktop bundle with its own `T8star-Aix-Voice-Studio.exe` launcher.

- Published source: `https://github.com/T8mars/Comfyui-breeze-tts`
- Registry entry: `https://registry.comfy.org/publishers/t8star/nodes/comfyui-breeze-tts-T8`

## Legal boundary

This distribution is unofficial. Source code is Apache-2.0. Model materials and self-hosted outputs are restricted to research and non-commercial use under `MODEL_LICENSE`; commercial rights are not included. Voice cloning requires the speaker's explicit, legally sufficient consent and the rights to all submitted recordings.
