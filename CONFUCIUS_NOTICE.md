# Confucius4-R2T2 transcription component

Voice Studio reuses the existing [T8 Confucius4-R2T2 project](https://github.com/T8mars/comfyui-confucius-r2t2-t8) at commit `189fb555a8f283e7c2d28925f2df047488e99077` (0.1.2). The files in `vendor/confucius-r2t2` are an unchanged source snapshot. Desktop adapters supply portable runtime and custom model paths; they do not update either existing ComfyUI node package. The source provenance and model checksums are recorded in `manifests/confucius-runtime.json`.

The component performs speech recognition, not translation. Transcripts remain drafts for manual comparison with the reference audio. Confucius results do not contain verified word timestamps. The original reference recording is preserved.

## Source and native libraries

The T8 node and the upstream native adapter use Apache License 2.0. Their license and modification notices are included under `vendor/confucius-r2t2/LICENSE` and `vendor/confucius-r2t2/r2t2_native/NOTICE.md`. The native adapter was derived from NetEase Youdao's source at commit `26d55a54ce5670cff9947a167d8ed95d569fd4d9`.

llama.cpp is built from commit `ad6c66839af3c5646fba8c6c2e2087a1e4e38948` and uses the MIT license, included as `confucius/licenses/llama.cpp-LICENSE`. CPython's complete license is included as `confucius/python/LICENSE.txt`; Python dependency licenses are retained with their `.dist-info` metadata and package data. These notices do not replace the licenses of individual components.

## Model assets

The decoder and audio projector are the unmodified official Q8 files from [NetEase Youdao](https://huggingface.co/netease-youdao/Confucius4-R2T2-GGUF), revision `86ff0251cb9f456b63aeef5f80137f104e22869a`. They are mirrored in [t8star/Confucius-R2t2-Comfy](https://huggingface.co/t8star/Confucius-R2t2-Comfy), revision `2223a55593a85bbaf2b12d58c410da67a8822608`.

The model and projector are governed separately by the NetEase Youdao Model Use License Agreement. Both language versions are provided in the model directory as `MODEL_LICENSE` and `MODEL_LICENSE_zh`; the Chinese agreement governs as specified in that agreement. Preserve the original notices and agreements in copies and distributions, and ensure downstream use complies with them. Apache 2.0 does not grant rights to these model weights. This integration is not affiliated with or endorsed by NetEase Youdao.

FireRedVAD ONNX and CMVN assets originate from FireRedVAD commit `c30ec49e8cc69642b0ee65362eba11b9d11c6e54` and use Apache License 2.0. Their license accompanies them at `models/Confucius4-R2T2-GGUF/FireRedVAD-ONNX/LICENSE`.

## NVIDIA and Microsoft runtime files

The portable component includes only the CUDA 12.8 Runtime and cuBLAS libraries required by its native extension: `cudart64_12.dll`, `cublas64_12.dll`, and `cublasLt64_12.dll`. They remain proprietary NVIDIA components, subject to the [CUDA 12.8 EULA](https://docs.nvidia.com/cuda/archive/12.8.0/eula/index.html), including its distribution requirements and third-party notices. The complete local toolkit license is included at `confucius/licenses/CUDA-12.8-EULA.txt`. Attachment A lists these libraries, including filename variants with version and architecture information, for distribution with applications. They are included solely for this application's NVIDIA GPU functionality and are not relicensed under Apache 2.0.

Microsoft Visual C++ runtime binaries are taken unchanged from the official Visual Studio 2022 `VC/Redist` directory, subject to [Microsoft's redistribution terms](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution). Relevant installed redistribution and third-party notices accompany them under `confucius/licenses`. No debug runtime binaries are distributed. The application does not redistribute the Windows NVIDIA driver; users provide a compatible NVIDIA driver themselves.

CUDA architecture targets recorded in the manifest describe compiled coverage, not hardware test results. Real-model acceptance on RTX 5090 Laptop does not establish acceptance on every compiled GPU family.
