from __future__ import annotations

import json
import math
import threading
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from .config import SAMPLE_RATE, output_dir, project_root
from .model_store import ensure_model_integrity
from .text_processing import split_text_for_model


DEFAULT_INSTRUCTION = "Speak clearly and naturally."
MAX_NEW_TOKENS = 1500
MAX_SEQ_LEN = 2048
REPETITION_PENALTY = 1.1
MAX_TEXT_CHARS = 20_000
MAX_INSTRUCTION_CHARS = 4_000
MIN_FAST_VRAM_BYTES = 20 * 1024**3


@dataclass(frozen=True)
class GenerationRequest:
    mode: str
    text: str
    instruction: str = DEFAULT_INSTRUCTION
    ref_audio_path: Path | None = None
    ref_text: str | None = None
    cfg_scale: float = 1.0
    seed: int = 42
    fast_all: bool = False
    max_new_tokens: int = MAX_NEW_TOKENS

    def validate(self) -> None:
        if self.mode not in {"design", "clone", "direction"}:
            raise ValueError(f"未知生成模式：{self.mode}")
        if not self.text.strip():
            raise ValueError("目标文本不能为空。")
        if len(self.text) > MAX_TEXT_CHARS:
            raise ValueError(f"目标文本不能超过 {MAX_TEXT_CHARS} 个字符。")
        if len(self.instruction) > MAX_INSTRUCTION_CHARS:
            raise ValueError(f"演绎指令不能超过 {MAX_INSTRUCTION_CHARS} 个字符。")
        if self.ref_text is not None and len(self.ref_text) > MAX_TEXT_CHARS:
            raise ValueError(f"参考逐字稿不能超过 {MAX_TEXT_CHARS} 个字符。")
        if not math.isfinite(self.cfg_scale) or self.cfg_scale <= 0:
            raise ValueError("CFG 必须是大于 0 的有限数值。")
        if not 64 <= int(self.max_new_tokens) <= MAX_NEW_TOKENS:
            raise ValueError(f"max_new_tokens 必须在 64 到 {MAX_NEW_TOKENS} 之间。")
        needs_reference = self.mode in {"clone", "direction"}
        if needs_reference and (self.ref_audio_path is None or not (self.ref_text or "").strip()):
            raise ValueError("Voice Clone/Direction 必须同时提供参考音频和准确逐字稿。")
        if self.ref_audio_path is not None and not self.ref_audio_path.is_file():
            raise FileNotFoundError(f"参考音频不存在：{self.ref_audio_path}")
        if self.mode in {"design", "direction"} and not self.instruction.strip():
            raise ValueError("Voice Design/Direction 的演绎指令不能为空。")


class RuntimeManager:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = Path(model_dir).expanduser().resolve()
        self._load_lock = threading.RLock()
        self._generation_lock = threading.Lock()
        self._runtime = None
        self._tokenizer = None
        self._model = None
        self._audio_tokenizer = None
        self._fast_all = None
        self._max_new_tokens = None
        self._loaded_at = None

    @property
    def loaded(self) -> bool:
        return self._runtime is not None

    def status(self) -> dict:
        return {
            "loaded": self.loaded,
            "generating": self._generation_lock.locked(),
            "model_dir": str(self.model_dir),
            "fast_all": self._fast_all,
            "max_new_tokens": self._max_new_tokens,
            "loaded_at": self._loaded_at,
            "sample_rate": getattr(self._runtime, "sample_rate", SAMPLE_RATE),
        }

    @staticmethod
    def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("生成已由用户取消。")

    def load(
        self,
        *,
        fast_all: bool = False,
        max_new_tokens: int = MAX_NEW_TOKENS,
        cancel_event: threading.Event | None = None,
    ) -> None:
        with self._load_lock:
            self._raise_if_cancelled(cancel_event)
            report = ensure_model_integrity(self.model_dir)
            if not report["valid"]:
                raise RuntimeError(f"模型目录不完整：{report}")
            if not report["license_accepted"]:
                raise PermissionError("必须先阅读并接受 BreezeBlue 模型许可证。")
            max_new_tokens = int(max_new_tokens)
            if self.loaded and self._fast_all == bool(fast_all):
                self._runtime.config = replace(
                    self._runtime.config, max_new_tokens=max_new_tokens
                )
                self._max_new_tokens = max_new_tokens
                return
            if self.loaded:
                self._unload_state()
            import torch

            if fast_all:
                if not torch.cuda.is_available():
                    raise RuntimeError("Fast All 仅支持 NVIDIA CUDA GPU。")
                properties = torch.cuda.get_device_properties(torch.cuda.current_device())
                if int(properties.total_memory) < MIN_FAST_VRAM_BYTES:
                    total_gib = int(properties.total_memory) / 1024**3
                    raise RuntimeError(
                        f"Fast All 需要 24GB 级显卡（至少 20 GiB 可寻址显存）；当前为 {total_gib:.1f} GiB。"
                    )

            from breeze_infer.runtime import (
                load_runtime,
                resolve_device,
                update_generation_config_for_breeze,
            )
            from models.fast_streaming import FastBreezeStreamingRuntime, FastStreamingConfig
            from models.warmup_profile import load_warmup_profile

            tokenizer, model, audio_tokenizer = load_runtime(
                self.model_dir,
                device=resolve_device(),
                attn_implementation="eager",
                cancel_check=lambda: self._raise_if_cancelled(cancel_event),
            )
            update_generation_config_for_breeze(model)
            config = FastStreamingConfig(
                max_new_tokens=max_new_tokens,
                max_seq_len=MAX_SEQ_LEN,
                fast_all=bool(fast_all),
                repetition_penalty=REPETITION_PENALTY,
            )
            runtime = FastBreezeStreamingRuntime(model, audio_tokenizer, config, tokenizer=tokenizer)
            if runtime.fast_enabled:
                self._raise_if_cancelled(cancel_event)
                profile = load_warmup_profile(project_root() / "configs" / "fast.json")
                profile = replace(profile, codec_chunk_frames=runtime.codec_chunk_frames)
                runtime.warmup_from_profile(profile)
            self._tokenizer = tokenizer
            self._model = model
            self._audio_tokenizer = audio_tokenizer
            self._runtime = runtime
            self._fast_all = bool(fast_all)
            self._max_new_tokens = max_new_tokens
            self._loaded_at = time.time()
            if torch.cuda.is_available():
                torch.cuda.synchronize()

    def _unload_state(self) -> None:
        self._runtime = None
        self._tokenizer = None
        self._model = None
        self._audio_tokenizer = None
        self._fast_all = None
        self._max_new_tokens = None
        self._loaded_at = None
        try:
            import gc
            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def unload(self, *, wait: bool = False) -> None:
        if not self._generation_lock.acquire(blocking=wait):
            raise RuntimeError("正在生成，无法卸载模型；请先取消或等待任务结束。")
        try:
            with self._load_lock:
                self._unload_state()
        finally:
            self._generation_lock.release()

    def select_model_dir(self, model_dir: Path) -> None:
        target = Path(model_dir).expanduser().resolve()
        # Re-confirming the directory that is already active is an idempotent UI
        # action.  It must not be rejected just because a generation currently
        # owns the model lock, and it must not unload a model that is in use.
        if target == self.model_dir:
            return
        if not self._generation_lock.acquire(blocking=False):
            raise RuntimeError("正在生成，无法切换模型；请先取消或等待任务结束。")
        try:
            with self._load_lock:
                self._unload_state()
                self.model_dir = target
        finally:
            self._generation_lock.release()

    def generate(
        self,
        request: GenerationRequest,
        *,
        cancel_event: threading.Event | None = None,
        on_chunk: Callable[[np.ndarray], None] | None = None,
    ) -> tuple[Path, dict]:
        request.validate()
        started = time.perf_counter()
        if not self._generation_lock.acquire(blocking=False):
            raise RuntimeError("Breeze TTS 2 当前正在生成；请等待上一任务结束。")
        try:
            self.load(
                fast_all=request.fast_all,
                max_new_tokens=request.max_new_tokens,
                cancel_event=cancel_event,
            )
            from breeze_infer.runtime import set_all_seeds
            from breeze_infer.templates import get_template, prepare_inputs
            from breeze_infer.audio import encode_prompt_audio

            self._raise_if_cancelled(cancel_event)
            runtime = self._runtime
            tokenizer = self._tokenizer
            model = self._model
            audio_tokenizer = self._audio_tokenizer
            if runtime is None or tokenizer is None or model is None or audio_tokenizer is None:
                raise RuntimeError("模型运行时未正确加载。")
            segments = split_text_for_model(request.text, tokenizer)
            if not segments:
                raise ValueError("目标文本不能为空。")
            request_id = uuid.uuid4().hex
            template_name = "tts_instruction"
            reference_codes = None
            if request.ref_audio_path is not None:
                template_name = "ref_edit_tata"
                reference_codes = encode_prompt_audio(audio_tokenizer, request.ref_audio_path)
                self._raise_if_cancelled(cancel_event)
            destination = output_dir()
            destination.mkdir(parents=True, exist_ok=True)
            output_path = destination / f"breeze_{request.mode}_{time.strftime('%Y%m%d_%H%M%S')}_{request_id[:8]}.wav"
            total_samples = 0
            segment_metadata: list[dict] = []
            sample_rate = int(runtime.sample_rate)
            try:
                with sf.SoundFile(
                    output_path,
                    mode="w",
                    samplerate=sample_rate,
                    channels=1,
                    subtype="PCM_16",
                ) as output_file:
                    for segment_index, segment_text in enumerate(segments):
                        self._raise_if_cancelled(cancel_event)
                        segment_seed = int(request.seed) + segment_index
                        set_all_seeds(segment_seed)
                        payload = {
                            "id": f"{request_id}-{segment_index}",
                            "text": segment_text,
                            "instruction": request.instruction.strip() or DEFAULT_INSTRUCTION,
                            "speaker": "S0",
                        }
                        if request.ref_audio_path is not None:
                            payload["ref_audio_path"] = str(request.ref_audio_path)
                            payload["ref_audio_codes"] = reference_codes
                            payload["ref_text"] = (request.ref_text or "").strip()
                        inputs = prepare_inputs(
                            tokenizer,
                            audio_tokenizer,
                            model,
                            [payload],
                            get_template(template_name),
                            guidance_scale=request.cfg_scale,
                            guidance_scale_ref=None,
                            guidance_scale_ins=None,
                        )
                        segment_samples = 0
                        for chunk in runtime.iter_audio_chunks(inputs, request_id=payload["id"]):
                            self._raise_if_cancelled(cancel_event)
                            audio = np.asarray(chunk.audio, dtype=np.float32).reshape(-1)
                            if audio.size == 0:
                                continue
                            if not np.isfinite(audio).all():
                                raise RuntimeError("模型生成了 NaN 或 Inf 音频样本。")
                            audio = np.clip(audio, -1.0, 1.0)
                            output_file.write(audio)
                            segment_samples += int(audio.size)
                            total_samples += int(audio.size)
                            if on_chunk is not None:
                                on_chunk(audio.copy())
                        segment_metadata.append(
                            {"index": segment_index, "text": segment_text, "seed": segment_seed, "samples": segment_samples}
                        )
                        if segment_index + 1 < len(segments):
                            pause = np.zeros(int(sample_rate * 0.2), dtype=np.float32)
                            output_file.write(pause)
                            total_samples += int(pause.size)
                            if on_chunk is not None:
                                on_chunk(pause.copy())
            except Exception:
                output_path.unlink(missing_ok=True)
                output_path.with_suffix(".json").unlink(missing_ok=True)
                raise
            if total_samples <= 0:
                output_path.unlink(missing_ok=True)
                raise RuntimeError("模型没有生成有效音频。")
            elapsed = time.perf_counter() - started
            duration = total_samples / float(sample_rate)
            metadata = {
                "mode": request.mode,
                "seed": request.seed,
                "cfg_scale": request.cfg_scale,
                "fast_all": request.fast_all,
                "sample_rate": sample_rate,
                "samples": total_samples,
                "duration_seconds": duration,
                "elapsed_seconds": elapsed,
                "rtf": elapsed / duration if duration > 0 else None,
                "output": str(output_path),
                "segment_count": len(segment_metadata),
                "segments": segment_metadata,
            }
            output_path.with_suffix(".json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return output_path, metadata
        finally:
            self._generation_lock.release()
