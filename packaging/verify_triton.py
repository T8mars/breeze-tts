from __future__ import annotations

import argparse
import json
from importlib import metadata

import torch
import triton
import triton.language as tl


EXPECTED_DISTRIBUTION_VERSION = "3.5.1.post24"


@triton.jit
def add_kernel(x_ptr, y_ptr, output_ptr, size: tl.constexpr, block_size: tl.constexpr):
    offsets = tl.program_id(axis=0) * block_size + tl.arange(0, block_size)
    mask = offsets < size
    tl.store(
        output_ptr + offsets,
        tl.load(x_ptr + offsets, mask=mask) + tl.load(y_ptr + offsets, mask=mask),
        mask=mask,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the Windows Triton runtime used by Fast All.")
    parser.add_argument("--kernel", action="store_true", help="Compile and execute a tiny CUDA kernel.")
    args = parser.parse_args()

    from torch.utils._triton import has_triton, has_triton_package

    distribution_version = metadata.version("triton-windows")
    report: dict[str, object] = {
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "triton": triton.__version__,
        "triton_windows": distribution_version,
        "has_triton_package": has_triton_package(),
        "has_triton": has_triton(),
        "kernel_tested": False,
    }
    if distribution_version != EXPECTED_DISTRIBUTION_VERSION:
        raise RuntimeError(
            f"triton-windows must be {EXPECTED_DISTRIBUTION_VERSION}, got {distribution_version}"
        )
    if not has_triton_package():
        raise RuntimeError("PyTorch cannot discover triton-windows.")

    if args.kernel:
        if not torch.cuda.is_available() or not has_triton():
            raise RuntimeError("A working NVIDIA CUDA device is required for the Triton kernel test.")

        size = 4096
        x = torch.randn(size, device="cuda")
        y = torch.randn(size, device="cuda")
        output = torch.empty_like(x)
        grid = (triton.cdiv(size, 256),)
        add_kernel[grid](x, y, output, size=size, block_size=256)
        torch.cuda.synchronize()
        if not torch.allclose(output, x + y):
            raise RuntimeError("Triton CUDA kernel returned an incorrect result.")
        report["kernel_tested"] = True
        report["gpu"] = torch.cuda.get_device_name(0)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
