# T8star-Aix Voice Studio distribution

This repository now contains two unofficial distribution targets built on the official Breeze TTS 2 source:

- Windows portable desktop application under `desktop/`
- ComfyUI node package under `comfyui-breeze-tts-T8/`

The implementation plan and acceptance record live in `roadmap.md`.

## Build the Windows packages

Desktop **0.3.8** adds offline Confucius4-R2T2 Q8 alongside Whisper Large-v3, without changing either standalone ComfyUI node. Before packaging, prepare the isolated portable worker with `packaging/prepare_confucius.ps1 -SourceRoot <pinned-confucius-checkout> -NativeBuildDir <multi-architecture-build>`. See [CONFUCIUS_NOTICE.md](CONFUCIUS_NOTICE.md) and [manifests/confucius-runtime.json](manifests/confucius-runtime.json) for source/model hashes, dependencies and licenses. The worker is independent Python 3.12, not a copied venv launcher or an installation into ComfyUI. Build the native library from the pinned source with `75;80;86;89;90;100;120` CUDA architectures; compilation coverage does not mean every GPU has been physically tested.

Both model directories can be saved or reset in **Settings & diagnostics → Transcription engines and model paths**. Confucius takes a directory containing the exact Q8 GGUF/model-projector pair; custom `FireRedVAD-ONNX/` is optional if bundled VAD is available. Whisper takes a CTranslate2 Large-v3 directory. A broken custom path fails explicitly instead of downloading another model. Confucius hotwords/context apply to generation-page and voice-library drafts. ASR unloads TTS first and releases its own model afterwards; the next TTS request reloads automatically. Confucius is CUDA-only and does not fabricate subtitle timestamps or translate languages.

For low disk space, `build_portable.ps1`, `build_self_extract.ps1` and `build_release.ps1` accept `-OutputRoot <dedicated-build-directory>`; only this version's explicitly resolved outputs are cleared. Do not point it at a filesystem root.

Use 64-bit PowerShell on Windows:

```powershell
.\packaging\build_portable.ps1

# Portable ZIP + large-package self-extracting EXE + release manifest/checksums
.\packaging\build_release.ps1

# GitHub Release: split the >2 GiB self-extractor into verified downloadable parts
.\packaging\New-GitHubReleaseAssets.ps1
```

The build creates a private portable CPython 3.10 runtime, installs official Breeze runtime requirements with PyTorch 2.9.1 CUDA 12.8, the pinned prebuilt FlashAttention 2.8.3 Windows wheel, `faster-whisper` 1.2.1, and the pinned Whisper Large-v3 checkpoint, verifies exact versions, builds Electron, and writes SHA-256 checksums. The FlashAttention wheel is downloaded from a pinned GitHub Release with a required SHA-256 digest and is never compiled locally. Standard streaming uses FlashAttention only for the compatible T5Gemma2 text encoder; Breeze's custom backbone and depth decoder remain on Eager, while Fast All retains its SDPA CUDA Graph text-encoder path. Version 0.3.6 expands no-CFG backbone-prefill graphs through 512 tokens and selectively falls back only the prefill stage for undeclared longer shapes, so long Voice Clone requests keep the remaining Fast All acceleration instead of failing. It retains automatic ComfyUI bundle recovery, official routing, request-boundary seed reset, BF16 cache dtype, smallest-fitting graph buckets, batch-4 warmups, tokenizer compatibility, and model-license 1.1 updates. Pure Clone is reference-only; adding an instruction explicitly selects Direction. Diagnostics also flag GPUs without native BF16 while the hard CUDA device guard prevents silent CPU generation. Large-v3 remains an editable transcription draft and never silently replaces the exact reference transcript. Because the bundled CUDA runtime exceeds Squirrel's reliable embedded-Setup size, the release uses a verified 7-Zip self-extracting EXE and manual update manifest. The application downloads the fixed Breeze model revision only after the user acknowledges its license.

## Install the ComfyUI nodes

The standalone node package is now **0.3.7**, fixing CPU/CUDA tensor mismatches after repeated runs or memory offloading (Issue #2), including the scaled text embedding, depth codebook head, codec residency, and CUDA Graph recapture. Update the nodes and restart ComfyUI. This node-only patch is independent of the **0.3.8** desktop portable distribution; adding Confucius to the desktop does not require another node update.

Install **Breeze TTS 2 · T8star-Aix** through ComfyUI-Manager, or clone `https://github.com/T8mars/Comfyui-breeze-tts` into `ComfyUI/custom_nodes/` and install `requirements.txt` with the ComfyUI Python. Dependencies use the official Manager pipeline; the node contains no runtime pip subprocess and does not declare Torch, Torchaudio, Transformers, Tokenizers, or NumPy. The loader validates Transformers `>=4.57,<6` and downloads the fixed official model revision into `ComfyUI/models/breeze_tts/BreezeBlue_Breeze-TTS-2` after explicit license acceptance. The desktop app instead defaults to `%APPDATA%\T8star-Aix Voice Studio\models\Breeze-TTS-2`; these paths are intentionally independent.

The ComfyUI ZIP is not a standalone application and intentionally contains no launcher. It must be installed into an existing ComfyUI. The separately built `T8star-Aix-Voice-Studio-vX.Y.Z-SelfExtract.exe` is the Windows desktop bundle with its own `T8star-Aix-Voice-Studio.exe` launcher.

- Published source: `https://github.com/T8mars/Comfyui-breeze-tts`
- Registry entry: `https://registry.comfy.org/publishers/t8star/nodes/comfyui-breeze-tts-T8`

## Legal boundary

This distribution is unofficial. Breeze source code is Apache-2.0. Breeze model materials and self-hosted outputs are restricted to research and non-commercial use under `MODEL_LICENSE`; commercial rights are not included. Confucius weights use their separate NetEase Youdao Model Use License; VAD, Whisper and runtime components retain their own licenses as recorded in `CONFUCIUS_NOTICE.md`, `WHISPER_NOTICE.md` and the bundled notices. Voice cloning requires the speaker's explicit, legally sufficient consent and the rights to all submitted recordings.
