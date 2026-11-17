"""
PaddleOCR ONNX engine for manga text recognition.

Uses PP-OCRv5 ONNX models for Japanese/Chinese text detection and recognition.
No PaddlePaddle dependency - uses pure ONNX Runtime for inference.
"""

import asyncio
import os
import logging
from typing import List, Tuple
import numpy as np
import onnxruntime as ort
import cv2

from manga_backend.utils.device import (
    get_onnx_providers,
    preload_onnx_gpu_dependencies,
    with_provider_options,
)
from manga_backend.utils.download import download_pinned_model, verify_cached_model
from manga_backend.paths import MODELS_CACHE_DIR

logger = logging.getLogger(__name__)

# Detection and recognition are PP-OCRv5 server models. Both ONNX files come from
# the same Hugging Face repo; the recognition ONNX is numerically identical to
# the official PaddlePaddle/PP-OCRv5_server_rec weights (max probability
# difference ~4e-5 against Paddle's own inference), and the character dictionary
# is read from the official repo's inference.yml.
PADDLEOCR_DET_REPO = os.getenv(
    "PADDLEOCR_DET_REPO", "xberg-io/paddleocr-onnx-models"
)
PADDLEOCR_ONNX_REPO = os.getenv(
    "PADDLEOCR_ONNX_REPO", "xberg-io/paddleocr-onnx-models"
)
PADDLEOCR_DICT_REPO = os.getenv(
    "PADDLEOCR_DICT_REPO", "PaddlePaddle/PP-OCRv5_server_rec"
)
PADDLEOCR_DET_FILE = "PP-OCRv5_server_det_infer.onnx"
PADDLEOCR_REC_FILE = "rec/chinese/model.onnx"
PADDLEOCR_DICT_FILE = "inference.yml"

# DB post-processing (PaddleOCR defaults) and recognition input geometry.
DET_UNCLIP_RATIO = 1.5
REC_HEIGHT = 48
REC_MIN_WIDTH = 320
REC_MAX_WIDTH = 3200
REC_BATCH_SIZE = 8
REC_ROTATE_RATIO = 1.5  # crops at least this much taller than wide are read sideways


def _sort_box_points(pts):
    pts = np.array(pts, dtype=np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).flatten()
    sorted_pts = np.zeros((4, 2), dtype=np.float32)
    sorted_pts[0] = pts[np.argmin(s)]
    sorted_pts[2] = pts[np.argmax(s)]
    sorted_pts[1] = pts[np.argmin(d)]
    sorted_pts[3] = pts[np.argmax(d)]
    return sorted_pts


class PaddleOCROnnxEngine:
    """ONNX-based PaddleOCR engine for manga - no PaddlePaddle required."""

    IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    def __init__(self, lang: str = "japan", device: str = "cpu"):
        self.lang = lang
        self.device = device
        self.det_session = None
        self.rec_session = None
        self.char_dict = None
        self._initialized = False
        self._initialization_lock = asyncio.Lock()
        self._inference_lock = asyncio.Lock()

        self.models_dir = str(MODELS_CACHE_DIR / 'paddleocr_onnx')

    async def initialize(self):
        """Initialize the ONNX models without blocking request handling."""
        async with self._initialization_lock:
            if self._initialized:
                return

            try:
                os.makedirs(self.models_dir, exist_ok=True)
                det_path = await self._download_model(
                    PADDLEOCR_DET_FILE,
                    repo_id=PADDLEOCR_DET_REPO,
                )
                rec_path = await self._download_model(
                    PADDLEOCR_REC_FILE,
                    repo_id=PADDLEOCR_ONNX_REPO,
                )
                dict_path = await self._download_model(
                    PADDLEOCR_DICT_FILE,
                    repo_id=PADDLEOCR_DICT_REPO,
                )
                char_dict = await asyncio.to_thread(self._load_char_dict, dict_path)

                providers = get_onnx_providers(self.device)
                logger.info(f"Loading PaddleOCR ONNX sessions with providers: {providers}")
                det_session = await asyncio.to_thread(
                    self._create_session, det_path, providers
                )
                rec_session = await asyncio.to_thread(
                    self._create_session, rec_path, providers
                )

                self.char_dict = char_dict
                self.det_session = det_session
                self.rec_session = rec_session
                self._initialized = True
                logger.info("PaddleOCR ONNX initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize PaddleOCR ONNX: {e}")
                raise

    async def _download_model(self, filename: str, repo_id: str) -> str:
        """Download a model without blocking the event loop."""
        return await asyncio.to_thread(self._download_model_sync, filename, repo_id)

    def _download_model_sync(self, filename: str, repo_id: str) -> str:
        local_path = os.path.join(self.models_dir, filename)

        if os.path.exists(local_path):
            logger.info(f"Found cached PaddleOCR model: {filename}")
            verify_cached_model(repo_id, filename, local_path)
            return local_path

        logger.info(f"Downloading PaddleOCR model: {filename}...")

        return download_pinned_model(
            repo_id,
            filename,
            local_dir=self.models_dir,
        )

    @staticmethod
    def _create_session(model_path: str, providers):
        providers = with_provider_options(providers)
        preload_onnx_gpu_dependencies(providers)
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.inter_op_num_threads = 1
        return ort.InferenceSession(
            model_path, sess_options=opts, providers=providers
        )

    def _load_char_dict(self, yml_path: str) -> List[str]:
        """Load the character dictionary PP-OCRv5 keeps in its yml or a sibling file."""
        import yaml

        with open(yml_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        char_list = ['blank']  # CTC blank token
        post_process = config.get('PostProcess') or {}

        if 'character_dict' in config:
            char_list.extend(config['character_dict'])
        elif 'character_dict' in post_process:
            char_list.extend(post_process['character_dict'])
        elif 'character_dict_path' in post_process:
            dict_path = os.path.join(os.path.dirname(yml_path), post_process['character_dict_path'])
            if os.path.exists(dict_path):
                with open(dict_path, 'r', encoding='utf-8') as f:
                    char_list.extend(line.strip() for line in f)
        elif 'character' in post_process:
            char_list.extend(post_process['character'])
        else:
            logger.warning("Could not load character dict, using basic set")
            char_list.extend(chr(i) for i in range(0x3040, 0x30FF))  # Hiragana + Katakana
            char_list.extend(chr(i) for i in range(0x4E00, 0x9FFF))  # CJK Unified Ideographs
            return char_list

        # PaddleOCR's CTCLabelDecode appends the ASCII space after the dictionary
        # (use_space_char), so the PP-OCRv5 head has len(dict) + 2 classes.
        char_list.append(' ')
        return char_list

    def _preprocess_det(
        self, image: np.ndarray
    ) -> Tuple[np.ndarray, float, Tuple[int, int]]:
        max_side = 960
        h, w = image.shape[:2]
        ratio = 1.0

        if max(h, w) > max_side:
            ratio = max_side / max(h, w)
            new_h, new_w = int(h * ratio), int(w * ratio)
            image = cv2.resize(image, (new_w, new_h))

        h, w = image.shape[:2]
        new_h = (h + 31) // 32 * 32
        new_w = (w + 31) // 32 * 32

        padded = np.zeros((new_h, new_w, 3), dtype=np.float32)
        padded[:h, :w] = image

        padded = (padded / 255.0 - self.IMAGENET_MEAN) / self.IMAGENET_STD

        padded = padded.transpose(2, 0, 1)
        padded = np.expand_dims(padded, axis=0).astype(np.float32)

        return padded, ratio, (h, w)

    def _postprocess_det(self, output: np.ndarray, ratio: float, orig_shape: Tuple[int, int], resized_shape: Tuple[int, int]) -> List[np.ndarray]:
        pred = output[0, 0]

        thresh = 0.3
        binary = (pred > thresh).astype(np.uint8) * 255

        contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

        boxes = []
        res_h, res_w = resized_shape
        for contour in contours:
            if cv2.contourArea(contour) < 100:
                continue

            rect = cv2.minAreaRect(contour)
            box = _sort_box_points(self._unclip(cv2.boxPoints(rect)))

            box[:, 0] = np.clip(box[:, 0], 0, res_w)
            box[:, 1] = np.clip(box[:, 1], 0, res_h)

            box = box / ratio

            h, w = orig_shape
            box[:, 0] = np.clip(box[:, 0], 0, w)
            box[:, 1] = np.clip(box[:, 1], 0, h)

            boxes.append(box)

        return boxes

    @staticmethod
    def _unclip(box: np.ndarray, ratio: float = DET_UNCLIP_RATIO) -> np.ndarray:
        """Grow a DB text box outwards: the probability map covers only the text core."""
        (cx, cy), (width, height), angle = cv2.minAreaRect(box.astype(np.float32))
        if width <= 0 or height <= 0:
            return box
        distance = width * height * ratio / (2.0 * (width + height))
        return cv2.boxPoints(
            ((cx, cy), (width + 2 * distance, height + 2 * distance), angle)
        )

    @staticmethod
    def _upright(crop: np.ndarray) -> np.ndarray:
        """Turn a tall (vertical text) crop sideways, as PaddleOCR does before recognition."""
        h, w = crop.shape[:2]
        if h / max(w, 1) >= REC_ROTATE_RATIO:
            return np.ascontiguousarray(np.rot90(crop))
        return crop

    def _preprocess_rec(self, image: np.ndarray) -> np.ndarray:
        """Resize a crop to the recognition height (width follows the aspect ratio).

        The PP-OCRv5 recognizer expects (x / 255 - 0.5) / 0.5 on the RGB crop, not
        the ImageNet statistics used by the detector. Padding to a common width
        happens per batch in _recognize_crops.
        """
        h, w = image.shape[:2]
        target_w = min(max(int(np.ceil(REC_HEIGHT * w / max(h, 1))), 1), REC_MAX_WIDTH)
        image = cv2.resize(image, (target_w, REC_HEIGHT)).astype(np.float32)
        image = (image / 255.0 - 0.5) / 0.5
        return image.transpose(2, 0, 1)

    def _postprocess_rec_batch(self, output: np.ndarray) -> List[Tuple[str, float]]:
        """CTC-decode recognition output shaped [batch_size, seq_len, num_classes]."""
        results = []
        for preds in output:
            text = []
            scores = []
            prev_idx = -1

            for idx, score in zip(np.argmax(preds, axis=-1), np.max(preds, axis=-1)):
                # Index 0 is the CTC blank.
                if idx != 0 and idx != prev_idx and idx < len(self.char_dict):
                    text.append(self.char_dict[idx])
                    scores.append(score)
                prev_idx = idx

            results.append((''.join(text), float(np.mean(scores)) if scores else 0.0))

        return results

    def _recognize_batch(self, batch: np.ndarray) -> List[Tuple[str, float]]:
        rec_input = {self.rec_session.get_inputs()[0].name: batch}
        return self._postprocess_rec_batch(self.rec_session.run(None, rec_input)[0])

    def _recognize_crops(self, crops: List[np.ndarray]) -> List[Tuple[str, float]]:
        """Recognize RGB crops, batching similar widths to keep padding small."""
        inputs = [self._preprocess_rec(self._upright(crop)) for crop in crops]
        order = sorted(range(len(inputs)), key=lambda index: inputs[index].shape[2])
        results: List[Tuple[str, float]] = [("", 0.0)] * len(inputs)

        for start in range(0, len(order), REC_BATCH_SIZE):
            indices = order[start:start + REC_BATCH_SIZE]
            width = max(REC_MIN_WIDTH, max(inputs[index].shape[2] for index in indices))
            batch = np.zeros((len(indices), 3, REC_HEIGHT, width), dtype=np.float32)
            for row, index in enumerate(indices):
                batch[row, :, :, :inputs[index].shape[2]] = inputs[index]
            for index, result in zip(indices, self._recognize_batch(batch)):
                results[index] = result

        return results

    def _get_crop(self, image: np.ndarray, box: np.ndarray) -> np.ndarray:
        pts = _sort_box_points(box.astype(np.float32))

        width = int(max(np.linalg.norm(pts[0] - pts[1]), np.linalg.norm(pts[2] - pts[3])))
        height = int(max(np.linalg.norm(pts[0] - pts[3]), np.linalg.norm(pts[1] - pts[2])))

        if width < 10 or height < 10:
            return None

        dst = np.array([
            [0, 0],
            [width, 0],
            [width, height],
            [0, height]
        ], dtype=np.float32)

        M = cv2.getPerspectiveTransform(pts, dst)
        return cv2.warpPerspective(
            image, M, (width, height),
            flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
        )

    async def detect_and_recognize(self, image: np.ndarray) -> List[dict]:
        """Return 'id', 'text', 'confidence', 'bbox' and 'polygon' per text in an RGB image."""
        if not self._initialized:
            await self.initialize()

        async with self._inference_lock:
            return await asyncio.to_thread(self._detect_and_recognize_sync, image)

    def _detect_and_recognize_sync(self, image: np.ndarray) -> List[dict]:
        """Run detection and recognition off the event loop."""
        try:
            orig_h, orig_w = image.shape[:2]

            det_input, ratio, resized_shape = self._preprocess_det(image)
            det_output = self.det_session.run(None, {self.det_session.get_inputs()[0].name: det_input})[0]
            boxes = self._postprocess_det(det_output, ratio, (orig_h, orig_w), resized_shape)

            text_blocks = []
            crops = []
            valid_boxes = []

            for box in boxes:
                crop = self._get_crop(image, box)
                if crop is not None:
                    crops.append(crop)
                    valid_boxes.append(box)

            if crops:
                batch_results = self._recognize_crops(crops)

                for i, ((text, confidence), box) in enumerate(zip(batch_results, valid_boxes)):
                    if text.strip():
                        x1, y1 = box.min(axis=0)
                        x2, y2 = box.max(axis=0)

                        text_blocks.append({
                            "id": i,
                            "text": text,
                            "confidence": confidence,
                            "bbox": [float(x1), float(y1), float(x2), float(y2)],
                            "polygon": box.tolist()
                        })

            logger.info(f"PaddleOCR ONNX detected {len(text_blocks)} text blocks")
            return text_blocks

        except Exception as e:
            logger.error(f"PaddleOCR ONNX inference failed: {e}")
            raise

    async def recognize_regions(self, image: np.ndarray, regions: List[dict]) -> List[str]:
        """Recognize text in pre-defined regions using batching."""
        if not self._initialized:
            await self.initialize()

        if not regions:
            return []

        async with self._inference_lock:
            return await asyncio.to_thread(self._recognize_regions_sync, image, regions)

    def _recognize_regions_sync(self, image: np.ndarray, regions: List[dict]) -> List[str]:
        """Run region recognition off the event loop."""
        texts = [""] * len(regions)
        crops = []
        valid_indices = []

        for i, region in enumerate(regions):
            bbox = region.get('bbox', region.get('xyxy', [0, 0, 100, 100]))
            x1, y1, x2, y2 = [int(v) for v in bbox[:4]]

            crop = image[max(0, y1):min(image.shape[0], y2), max(0, x1):min(image.shape[1], x2)]
            if crop.size == 0:
                continue

            crops.append(crop)
            valid_indices.append(i)

        if not crops:
            return texts

        try:
            batch_results = self._recognize_crops(crops)
            for (text, _), index in zip(batch_results, valid_indices):
                texts[index] = text
        except Exception as e:
            logger.error(f"OCR batch inference failed: {e}")
            for index, crop in zip(valid_indices, crops):
                try:
                    texts[index] = self._recognize_crops([crop])[0][0]
                except Exception as fallback_error:
                    logger.warning(f"OCR single-region fallback failed: {fallback_error}")

        return texts

    def dispose(self):
        self.det_session = None
        self.rec_session = None
        self._initialized = False
