"""Portable entry point for the unchanged, pinned Confucius inference core.

Only paths and DLL discovery are adapted here. No ComfyUI module is imported.
"""
from __future__ import annotations

import argparse
import ctypes
import functools
import hashlib
import os
from pathlib import Path
import sys
import threading


def watch_parent(pid: int) -> None:
    """Release the child even when Electron forcibly terminates its backend."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, 0, pid)  # SYNCHRONIZE; no write rights.
    if not handle:
        raise RuntimeError("Confucius parent backend is no longer available.")

    def wait():
        try:
            if kernel.WaitForSingleObject(handle, 0xFFFFFFFF) == 0:
                os._exit(0)
        finally:
            kernel.CloseHandle(handle)

    threading.Thread(target=wait, name="confucius-parent-watch", daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--vad", type=Path, required=True)
    args = parser.parse_args()
    watch_parent(args.parent_pid)
    source, runtime = args.source.resolve(), args.runtime.resolve()
    # Retain DLL-directory handles for the lifetime of the worker. Python 3.8+
    # deliberately does not use PATH for extension dependency discovery.
    dll_handles = []
    for directory in (runtime / "cuda/bin", runtime / "python", runtime / "native/bin/Release"):
        dll_handles.append(os.add_dll_directory(str(directory)))
    os.environ["CUDA_PATH"] = str(runtime / "cuda")
    cuda = ctypes.WinDLL(str(runtime / "cuda/bin/cudart64_12.dll"))
    count = ctypes.c_int()
    cuda.cudaGetDeviceCount.argtypes = [ctypes.POINTER(ctypes.c_int)]
    cuda.cudaGetDeviceCount.restype = ctypes.c_int
    result = cuda.cudaGetDeviceCount(ctypes.byref(count))
    if result != 0 or count.value < 1:
        raise RuntimeError(f"Confucius Q8 需要可用的 NVIDIA CUDA GPU（CUDA error {result}）。不会静默改用 CPU。")
    sys.path.insert(0, str(source))
    from r2t2_core import worker
    from r2t2_core.native import NativeQ8Engine
    from r2t2_core.segmented import SegmentedStream
    from r2t2_core.vad import FireRedOnnxVAD

    worker.NativeQ8Engine = functools.partial(
        NativeQ8Engine, model_dir=args.models.resolve(), build_dir=runtime / "native"
    )

    class PortableSegmentedStream(SegmentedStream):
        def __init__(self, *positional, **options):
            if "vad" not in options:
                options["vad"] = FireRedOnnxVAD(args.vad.resolve())
            super().__init__(*positional, **options)

    worker.SegmentedStream = PortableSegmentedStream
    extensions = list((runtime / "native/python").rglob("qwen3asr_native*.pyd"))
    if len(extensions) != 1:
        raise RuntimeError("Confucius native 扩展缺失或存在多个版本，请重新下载完整整合包。")
    worker.BUILD_ID = "llama-ad6c66839af3-cu128-" + hashlib.sha256(extensions[0].read_bytes()).hexdigest()[:12]
    sys.argv = [str(Path(__file__)), "--port", str(args.port)]
    worker.main()


if __name__ == "__main__":
    main()
