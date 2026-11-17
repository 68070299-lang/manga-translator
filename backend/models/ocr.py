"""
Manga OCR using ONNX runtime.

Based on comic-translate's manga_ocr implementation.
Uses the manga-ocr-base model from HuggingFace.
"""

import asyncio
import os
import re
import logging
from typing import List
import numpy as np
from PIL import Image

from utils.device import get_onnx_providers, create_onnx_session
from utils.textblock import TextBlock
from utils.download import download_manga_ocr_onnx

logger = logging.getLogger(__name__)

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models_cache")

try:
    import jaconv
    HAS_JACONV = True
except ImportError:
    HAS_JACONV = False
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

    def __init__(self, device: str = "cpu"):
        self.device = device
        self.encoder = None
        self.decoder = None
        self.vocab = None
        self.expansion_percentage = 5
        self.model_dir = os.path.join(MODELS_DIR, "manga_ocr")
        self.batch_size = self._resolve_batch_size()
        self._inference_lock = asyncio.Lock()

    def _resolve_batch_size(self) -> int:
        default = 16 if self.device in {"cuda", "tensorrt", "directml", "dml"} else 4
        try:
            return max(1, int(os.getenv("MANGA_OCR_BATCH_SIZE", str(default))))
        except ValueError:
            logger.warning("Invalid MANGA_OCR_BATCH_SIZE; using %s", default)
            return default

    async def initialize(self):
        """Download and load the OCR model."""
        os.makedirs(self.model_dir, exist_ok=True)

        logger.info("Downloading manga-ocr model...")
        model_files = await download_manga_ocr_onnx(self.model_dir)

        providers = get_onnx_providers(self.device)
        logger.info(f"Loading OCR model with providers: {providers}")

        self.encoder = await asyncio.to_thread(create_onnx_session, model_files["encoder"], providers)
        self.decoder = await asyncio.to_thread(create_onnx_session, model_files["decoder"], providers)
        with open(model_files["vocab"], 'r', encoding='utf-8') as f:
            self.vocab = f.read().splitlines()
        self._setup_io_names()

        logger.info("OCR model loaded successfully (batch_size=%s)", self.batch_size)

    def _setup_io_names(self):
        """Discover input/output names from ONNX models."""
        encoder_inputs = [inp.name for inp in self.encoder.get_inputs()]
        self.encoder_image_input = self._find_name(encoder_inputs, ['image', 'pixel_values', 'input'])
        self.encoder_output_name = self.encoder.get_outputs()[0].name

        decoder_inputs = [inp.name for inp in self.decoder.get_inputs()]
        self.decoder_token_input = self._find_name(decoder_inputs, ['token_ids', 'input_ids', 'input'])
        self.decoder_encoder_input = self._find_name(
            decoder_inputs,
            ['encoder_hidden_states', 'encoder_outputs', 'encoder_last_hidden_state']
        )

    def _find_name(self, names: List[str], candidates: List[str]) -> str:
        """Find a matching name from candidates."""
        for cand in candidates:
            for name in names:
                if cand.lower() in name.lower():
                    return name
        return names[0] if names else ""

    async def process(self, image: np.ndarray, blocks: List[TextBlock]) -> List[str]:
        """Extract text from each block of an RGB image, storing it on the block."""
        if self.encoder is None or self.decoder is None:
            raise RuntimeError("OCR model not initialized")

        results = [""] * len(blocks)
        crops = []
        crop_indexes = []
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

    def _ocr_batch(self, images: List[np.ndarray]) -> List[str]:
        """Run OCR in bounded batches to amortize encoder and decoder launches."""
        results = []
        for offset in range(0, len(images), self.batch_size):
            chunk = images[offset:offset + self.batch_size]
            image_batch = np.concatenate([self._preprocess(image) for image in chunk], axis=0)
            token_batches = self._generate_batch(image_batch)
            results.extend(self._postprocess(self._decode(tokens)) for tokens in token_batches)
        return results

    def _ocr_individual_fallback(self, images: List[np.ndarray]) -> List[str]:
        results = []
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

        pil_img = Image.fromarray(image).convert('L').convert('RGB')
        pil_img = pil_img.resize((224, 224), resample=Image.BILINEAR)

        arr = np.array(pil_img, dtype=np.float32) / 255.0
        arr = (arr - 0.5) / 0.5

        # (H, W, C) -> (1, C, H, W)
        return arr.transpose((2, 0, 1))[np.newaxis, ...]

    def _generate_batch(self, images: np.ndarray, max_length: int = 300) -> List[List[int]]:
        """Greedy-decode multiple crops with one model launch per token step."""
        encoder_hidden = self.encoder.run(None, {self.encoder_image_input: images})[0]
        batch_size = int(images.shape[0])
        sequences = [[self.BOS_TOKEN] for _ in range(batch_size)]
        active_indexes = np.arange(batch_size)
        active_tokens = np.full((batch_size, 1), self.BOS_TOKEN, dtype=np.int64)
        active_hidden = encoder_hidden

        for _ in range(max_length):
            logits = self.decoder.run(None, {
                self.decoder_token_input: active_tokens,
                self.decoder_encoder_input: active_hidden
            })[0]
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
    def _trim_runaway_repetition(cls, tokens: List[int]) -> bool:
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

    def _decode(self, token_ids: List[int]) -> str:
        """Decode token IDs to text."""
        return ''.join(
            self.vocab[tid]
            for tid in token_ids
            if self.SKIP_TOKEN_RANGE <= tid < len(self.vocab)
        )

    def _postprocess(self, text: str) -> str:
        """Clean up the OCR output."""
        text = ''.join(text.split())

        text = text.replace('…', '...')

        text = re.sub(r'[・.]{2,}', lambda x: '.' * (x.end() - x.start()), text)

        if HAS_JACONV:
            text = jaconv.h2z(text, ascii=True, digit=True)

        return text

    def dispose(self):
        self.encoder = None
        self.decoder = None
