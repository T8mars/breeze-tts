from __future__ import annotations

import argparse
import json
from importlib import metadata

import torch
from flash_attn import flash_attn_func, flash_attn_varlen_func


EXPECTED_VERSION = "2.8.3"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the prebuilt FlashAttention runtime used by Breeze's text encoder."
    )
    parser.add_argument(
        "--kernel", action="store_true", help="Execute fixed and variable-length CUDA kernels."
    )
    args = parser.parse_args()

    version = metadata.version("flash-attn")
    report: dict[str, object] = {
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "flash_attn": version,
        "kernel_tested": False,
    }
    if version.split("+")[0] != EXPECTED_VERSION:
        raise RuntimeError(f"flash-attn must be {EXPECTED_VERSION}, got {version}")

    if args.kernel:
        if not torch.cuda.is_available():
            raise RuntimeError("A working NVIDIA CUDA device is required for the kernel test.")
        torch.manual_seed(7)
        device = torch.device("cuda")
        dtype = torch.bfloat16

        q = torch.randn((2, 96, 4, 64), device=device, dtype=dtype)
        k = torch.randn_like(q)
        v = torch.randn_like(q)
        fixed = flash_attn_func(
            q, k, v, dropout_p=0.0, causal=True, window_size=(32, 0)
        )
        if fixed.shape != q.shape or not torch.isfinite(fixed).all():
            raise RuntimeError("FlashAttention fixed-length kernel returned invalid output.")

        lengths = (37, 64)
        total = sum(lengths)
        q_var = torch.randn((total, 4, 64), device=device, dtype=dtype)
        k_var = torch.randn_like(q_var)
        v_var = torch.randn_like(q_var)
        cu_seqlens = torch.tensor((0, lengths[0], total), device=device, dtype=torch.int32)
        variable = flash_attn_varlen_func(
            q_var,
            k_var,
            v_var,
            cu_seqlens,
            cu_seqlens,
            max(lengths),
            max(lengths),
            dropout_p=0.0,
            causal=False,
        )
        torch.cuda.synchronize()
        if variable.shape != q_var.shape or not torch.isfinite(variable).all():
            raise RuntimeError("FlashAttention variable-length kernel returned invalid output.")

        properties = torch.cuda.get_device_properties(torch.cuda.current_device())
        report.update(
            {
                "kernel_tested": True,
                "gpu": properties.name,
                "compute_capability": f"{properties.major}.{properties.minor}",
                "dtype": str(dtype),
                "fixed_shape": list(fixed.shape),
                "variable_shape": list(variable.shape),
            }
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
