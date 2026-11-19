"""Manga OCR using ONNX runtime.

Based on comic-translate's manga_ocr implementation.
Uses the manga-ocr-base model from HuggingFace.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Sequence
from types import ModuleType

import numpy as np
import onnxruntime as ort
from PIL import Image

from manga_backend.paths import MODELS_CACHE_DIR
from manga_backend.utils.device import create_onnx_session, get_onnx_providers
from manga_backend.utils.download import download_manga_ocr_onnx
from manga_backend.utils.textblock import TextBlock

logger = logging.getLogger(__name__)

_jaconv: ModuleType | None
try:
    import jaconv as _jaconv
except ImportError:
    _jaconv = None
    logger.warning("jaconv not installed, Japanese text normalization disabled")


class OCREngine:
    """Manga OCR engine using ONNX runtime."""

    BOS_TOKEN = 2
    EOS_TOKEN = 3
    SKIP_TOKEN_RANGE = 5
    # On crops that hold no real text the decoder can loop on one character (or
    # a short cycle) until max_length, and every extra step re-reads the whole
    # sequence. A tail that is pure repetition for this many tokens ends the decode.
    REPEAT_LIMIT = 20
    MAX_REPEAT_PERIOD = 4
    # Copies of the repeating unit kept when that stop fires, so a real scream
    # or sound effect (うおおおお, ゴゴゴゴ) survives in shortened form.
    REPEAT_KEEP_COPIES = 4

    def __init__(self, device: str = "cpu") -> None:
        self.device = device
        self.encoder: ort.InferenceSession | None = None
        self.decoder: ort.InferenceSession | None = None
        self.vocab: list[str] = []
        self.encoder_image_input = ""
        self.encoder_output_name = ""
        self.decoder_token_input = ""
        self.decoder_encoder_input = ""
        self.decoder_output_name = ""
        self.expansion_percentage = 5
        self.model_dir = MODELS_CACHE_DIR / "manga_ocr"
        self.batch_size = self._resolve_batch_size()
        self._inference_lock = asyncio.Lock()

    def _resolve_batch_size(self) -> int:
        default = 32 if self.device in {"cuda", "tensorrt", "directml", "dml"} else 4
        try:
            return max(1, int(os.getenv("MANGA_OCR_BATCH_SIZE", str(default))))
        except ValueError:
            logger.warning("Invalid MANGA_OCR_BATCH_SIZE; using %s", default)
            return default

    async def initialize(self) -> None:
        """Download and load the OCR model."""
        self.model_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Downloading manga-ocr model...")
        model_files = await download_manga_ocr_onnx(self.model_dir)

        providers = get_onnx_providers(self.device)
        logger.info("Loading OCR model with providers: %s", providers)

        self.encoder = await asyncio.to_thread(create_onnx_session, model_files["encoder"], providers)
        self.decoder = await asyncio.to_thread(create_onnx_session, model_files["decoder"], providers)
        self.vocab = model_files["vocab"].read_text(encoding="utf-8").splitlines()
        self._setup_io_names(self.encoder, self.decoder)

        logger.info("OCR model loaded successfully (batch_size=%s)", self.batch_size)

    def _setup_io_names(self, encoder: ort.InferenceSession, decoder: ort.InferenceSession) -> None:
        """Discover input/output names from ONNX models."""
        encoder_inputs = [inp.name for inp in encoder.get_inputs()]
        self.encoder_image_input = self._find_name(encoder_inputs, ["image", "pixel_values", "input"])
        self.encoder_output_name = encoder.get_outputs()[0].name

        decoder_inputs = [inp.name for inp in decoder.get_inputs()]
        self.decoder_token_input = self._find_name(decoder_inputs, ["token_ids", "input_ids", "input"])
        self.decoder_encoder_input = self._find_name(
            decoder_inputs,
            ["encoder_hidden_states", "encoder_outputs", "encoder_last_hidden_state"],
        )
        decoder_outputs = decoder.get_outputs()
        self.decoder_output_name = decoder_outputs[0].name if decoder_outputs else ""

    @staticmethod
    def _find_name(names: Sequence[str], candidates: Sequence[str]) -> str:
        """Find a matching name from candidates."""
        for cand in candidates:
            for name in names:
                if cand.lower() in name.lower():
                    return name
        return names[0] if names else ""

    async def process(self, image: np.ndarray, blocks: list[TextBlock]) -> list[str]:
        """Extract text from each block of an RGB image, storing it on the block."""
        if self.encoder is None or self.decoder is None:
            raise RuntimeError("OCR model not initialized")

        results = [""] * len(blocks)
        crops: list[np.ndarray] = []
        crop_indexes: list[int] = []
        h, w = image.shape[:2]

        for index, block in enumerate(blocks):
            x1, y1, x2, y2 = [int(v) for v in block.xyxy]

            exp_x = int((x2 - x1) * self.expansion_percentage / 100)
            exp_y = int((y2 - y1) * self.expansion_percentage / 100)

            x1 = max(0, x1 - exp_x)
            y1 = max(0, y1 - exp_y)
            x2 = min(w, x2 + exp_x)
            y2 = min(h, y2 + exp_y)

            if x1 < x2 and y1 < y2:
                crops.append(image[y1:y2, x1:x2])
                crop_indexes.append(index)

        if not crops:
            return results

        try:
            async with self._inference_lock:
                texts = await asyncio.to_thread(self._ocr_batch, crops)
        except Exception as error:
            logger.warning("Batched OCR failed; retrying regions individually: %s", error)
            async with self._inference_lock:
                texts = await asyncio.to_thread(self._ocr_individual_fallback, crops)

        for index, text in zip(crop_indexes, texts):
            results[index] = text
            blocks[index].text = text

        return results

    def _ocr_batch(self, images: list[np.ndarray]) -> list[str]:
        """Run OCR in bounded batches to amortize encoder and decoder launches."""
        results: list[str] = []
        for offset in range(0, len(images), self.batch_size):
            chunk = images[offset:offset + self.batch_size]
            image_batch = np.concatenate([self._preprocess(image) for image in chunk], axis=0)
            token_batches = self._generate_batch(image_batch)
            results.extend(self._postprocess(self._decode(tokens)) for tokens in token_batches)
        return results

    def _ocr_individual_fallback(self, images: list[np.ndarray]) -> list[str]:
        results: list[str] = []
        for image in images:
            try:
                results.append(self._ocr_single(image))
            except Exception as error:
                logger.error("OCR failed for block: %s", error)
                results.append("")
        return results

    def _ocr_single(self, image: np.ndarray) -> str:
        """Run OCR on a single cropped image."""
        token_ids = self._generate_batch(self._preprocess(image))[0]
        return self._postprocess(self._decode(token_ids))

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        """Preprocess image for the encoder."""
        if image is None or image.size == 0:
            raise ValueError("Empty image")

        pil_img = Image.fromarray(image).convert("L").convert("RGB")
        pil_img = pil_img.resize((224, 224), resample=Image.Resampling.BILINEAR)

        arr = np.array(pil_img, dtype=np.float32) / 255.0
        arr = (arr - 0.5) / 0.5

        # (H, W, C) -> (1, C, H, W)
        return arr.transpose((2, 0, 1))[np.newaxis, ...]

    @staticmethod
    def _supports_cuda_iobinding(session: ort.InferenceSession) -> bool:
        get_providers = getattr(session, "get_providers", None)
        if not callable(get_providers):
            return False
        providers = get_providers()
        return (
            isinstance(providers, (list, tuple))
            and bool(providers)
            and providers[0] == "CUDAExecutionProvider"
            and callable(getattr(session, "io_binding", None))
            and callable(getattr(session, "run_with_iobinding", None))
            and hasattr(ort, "OrtValue")
        )

    def _generate_batch(self, images: np.ndarray, max_length: int = 300) -> list[list[int]]:
        """Greedy-decode multiple crops with one model launch per token step."""
        encoder, decoder = self.encoder, self.decoder
        if encoder is None or decoder is None:
            raise RuntimeError("OCR model not initialized")
        use_cuda_io = (
            bool(self.encoder_output_name)
            and bool(self.decoder_output_name)
            and self._supports_cuda_iobinding(encoder)
            and self._supports_cuda_iobinding(decoder)
        )
        batch_size = int(images.shape[0])
        sequences = [[self.BOS_TOKEN] for _ in range(batch_size)]
        active_indexes = np.arange(batch_size)
        active_tokens = np.full((batch_size, 1), self.BOS_TOKEN, dtype=np.int64)

        if use_cuda_io:
            enc_io = encoder.io_binding()
            enc_io.bind_cpu_input(self.encoder_image_input, np.ascontiguousarray(images))
            enc_io.bind_output(self.encoder_output_name, "cuda")
            encoder.run_with_iobinding(enc_io)
            hidden_gpu = enc_io.get_outputs()[0]
            hidden_cpu: np.ndarray | None = None

            for _ in range(max_length):
                dec_io = decoder.io_binding()
                dec_io.bind_cpu_input(
                    self.decoder_token_input, np.ascontiguousarray(active_tokens)
                )
                dec_io.bind_ortvalue_input(self.decoder_encoder_input, hidden_gpu)
                dec_io.bind_output(self.decoder_output_name, "cpu")
                decoder.run_with_iobinding(dec_io)
                logits = np.asarray(dec_io.copy_outputs_to_cpu()[0])
                next_tokens = np.argmax(logits[:, -1, :], axis=-1).astype(np.int64)
                active_tokens = np.concatenate(
                    [active_tokens, next_tokens[:, np.newaxis]], axis=1
                )
                unfinished = next_tokens != self.EOS_TOKEN
                for position, (index, token) in enumerate(
                    zip(active_indexes.tolist(), next_tokens.tolist())
                ):
                    sequences[index].append(token)
                    if self._trim_runaway_repetition(sequences[index]):
                        unfinished[position] = False

                if not unfinished.any():
                    break
                if not unfinished.all():
                    active_indexes = active_indexes[unfinished]
                    active_tokens = active_tokens[unfinished]
                    if hidden_cpu is None:
                        hidden_cpu = np.asarray(hidden_gpu.numpy())
                    hidden_cpu = np.ascontiguousarray(hidden_cpu[unfinished])
                    hidden_gpu = ort.OrtValue.ortvalue_from_numpy(hidden_cpu, "cuda", 0)
            return sequences

        encoder_hidden = np.asarray(encoder.run(None, {self.encoder_image_input: images})[0])
        active_hidden = encoder_hidden

        for _ in range(max_length):
            logits = np.asarray(
                decoder.run(
                    None,
                    {
                        self.decoder_token_input: active_tokens,
                        self.decoder_encoder_input: active_hidden,
                    },
                )[0]
            )
            next_tokens = np.argmax(logits[:, -1, :], axis=-1).astype(np.int64)
            active_tokens = np.concatenate([active_tokens, next_tokens[:, np.newaxis]], axis=1)
            unfinished = next_tokens != self.EOS_TOKEN
            for position, (index, token) in enumerate(
                zip(active_indexes.tolist(), next_tokens.tolist())
            ):
                sequences[index].append(token)
                if self._trim_runaway_repetition(sequences[index]):
                    unfinished[position] = False

            if not unfinished.any():
                break
            active_indexes = active_indexes[unfinished]
            active_tokens = active_tokens[unfinished]
            active_hidden = active_hidden[unfinished]

        return sequences

    @classmethod
    def _trim_runaway_repetition(cls, tokens: list[int]) -> bool:
        """Shorten a runaway repetition at the end of `tokens` in place.

        Returns True, meaning this crop's decode should stop, once the last
        REPEAT_LIMIT or more tokens only repeat one token or a cycle of up to
        MAX_REPEAT_PERIOD tokens. The tokens decoded before that run are kept
        unchanged and the run is cut to its first REPEAT_KEEP_COPIES copies of
        the unit, so a long scream such as うおおお... comes back as うおおおお.
        Shorter runs are left alone and decoding continues.

        Because decoding stops here, anything the model would have read after
        a run that reaches the limit (a closing ！ after a long scream, say) is
        lost. That is accepted to keep crops without real text from decoding
        to max_length.
        """
        for period in range(1, cls.MAX_REPEAT_PERIOD + 1):
            span = -(-cls.REPEAT_LIMIT // period) * period
            if len(tokens) <= span:
                continue
            tail = tokens[-span:]
            if all(tail[offset] == tail[offset % period] for offset in range(period, span)):
                keep = min(span, cls.REPEAT_KEEP_COPIES * period)
                del tokens[len(tokens) - span + keep:]
                return True
        return False

    def _decode(self, token_ids: Sequence[int]) -> str:
        """Decode token IDs to text."""
        return "".join(
            self.vocab[tid]
            for tid in token_ids
            if self.SKIP_TOKEN_RANGE <= tid < len(self.vocab)
        )

    def _postprocess(self, text: str) -> str:
        """Clean up the OCR output."""
        text = "".join(text.split())

        text = text.replace("…", "...")

        text = re.sub(r"[・.]{2,}", lambda x: "." * (x.end() - x.start()), text)

        if _jaconv is not None:
            text = _jaconv.h2z(text, ascii=True, digit=True)

        return text

    def dispose(self) -> None:
        self.encoder = None
        self.decoder = None
