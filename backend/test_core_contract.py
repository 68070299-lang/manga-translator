"""Focused core backend contract tests independent from the legacy API suite."""

import io
import sys
import unittest
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import main
from pipeline import GoogleOCRError, GoogleOCRTimeoutError, InpainterUnavailableError


def make_png_bytes(size=(12, 12)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.getvalue()


class CoreContractPipeline:
    is_ready = True
    device = "cpu"
    model_status = {"detector": {"status": "loaded"}}

    def __init__(self):
        self.translate_calls = []

    async def translate(self, **kwargs):
        self.translate_calls.append(kwargs)
        return {
            "blocks": [{"bbox": [0, 0, 10, 10], "type": "text"}],
            "original_texts": ["source"],
            "translated_texts": ["translated"],
            "image": kwargs["image"].copy(),
        }


class MismatchedPipeline(CoreContractPipeline):
    async def translate(self, **kwargs):
        return {
            "blocks": [
                {"bbox": [0, 0, 10, 10], "type": "text"},
                {"bbox": [10, 10, 20, 20], "type": "text"},
            ],
            "original_texts": ["one", "two", "extra"],
            "translated_texts": ["translated"],
        }


class OptionalInpainterFailurePipeline(CoreContractPipeline):
    model_status = {
        "detector": {"status": "loaded"},
        "ocr": {"status": "loaded"},
        "inpainter": {"status": "error", "error": "model unavailable"},
    }

    def component_ready(self, component):
        return component in {"detector", "ocr"}

    async def translate(self, **kwargs):
        self.translate_calls.append(kwargs)
        if kwargs.get("render") and kwargs.get("inpainter") != "none":
            raise InpainterUnavailableError("model unavailable")
        return await CoreContractPipeline.translate(self, **kwargs)


class GoogleFailurePipeline(CoreContractPipeline):
    def __init__(self, error):
        super().__init__()
        self.error = error

    async def detect(self, image, detector="rtdetr"):
        return [{"bbox": [0, 0, image.shape[1], image.shape[0]], "type": "text"}]

    async def ocr(self, *args, **kwargs):
        raise self.error


class CoreBackendContractTests(unittest.TestCase):
    def setUp(self):
        self.original_pipeline = main.pipeline
        self.original_active_translate_requests = main.active_translate_requests
        self.original_last_error = main.last_error
        self.addCleanup(self.restore_main_state)

        self.pipeline = CoreContractPipeline()
        main.pipeline = self.pipeline
        main.active_translate_requests = 0
        main.last_error = None
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def restore_main_state(self):
        main.pipeline = self.original_pipeline
        main.active_translate_requests = self.original_active_translate_requests
        main.last_error = self.original_last_error

    def test_health_exposes_model_free_pipeline_status(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["models_loaded"])
        self.assertEqual(body["device"], "cpu")
        self.assertIn("config", body)

    def test_translate_accepts_model_free_pipeline_and_returns_image(self):
        response = self.client.post(
            "/translate",
            headers={"X-Request-ID": "req-core-backend"},
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"translator": "none", "render": "true", "target_lang": "Thai"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["x-request-id"], "req-core-backend")
        call = self.pipeline.translate_calls[-1]
        self.assertEqual(call["translator"], "none")
        self.assertEqual(call["target_lang"], "Thai")
        self.assertIsInstance(call["image"], np.ndarray)

    def test_translate_validation_rejects_bad_inputs_before_pipeline_call(self):
        response = self.client.post(
            "/translate",
            files={"image": ("page.txt", b"not an image", "text/plain")},
            data={"translator": "none"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid image type", response.json()["detail"])
        self.assertEqual(self.pipeline.translate_calls, [])

    def test_translate_json_contract_aligns_provider_arrays_to_blocks(self):
        main.pipeline = MismatchedPipeline()

        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"translator": "none", "render": "false", "inpainter": "none"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["blocks"]), 2)
        self.assertEqual(payload["original_texts"], ["one", "two"])
        self.assertEqual(payload["translated_texts"], ["translated", ""])

    def test_optional_inpainter_failure_is_reported_only_to_routes_that_need_it(self):
        main.pipeline = OptionalInpainterFailurePipeline()

        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertFalse(health.json()["components"]["inpainter"])

        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"translator": "none"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("inpainter", response.json()["detail"])

        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"translator": "none", "inpainter": "none"},
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"translator": "none", "render": "false"},
        )
        self.assertEqual(response.status_code, 200)

    def test_google_ocr_upstream_failure_is_sanitized_to_502(self):
        main.pipeline = GoogleFailurePipeline(GoogleOCRError("secret vision key leaked"))

        response = self.client.post(
            "/ocr",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"ocr": "google-ocr", "google_ocr_api_key": "secret vision key"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret", response.text)

    def test_google_ocr_timeout_is_reported_as_504(self):
        main.pipeline = GoogleFailurePipeline(GoogleOCRTimeoutError())

        response = self.client.post(
            "/ocr",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"ocr": "google-ocr", "google_ocr_api_key": "secret vision key"},
        )

        self.assertEqual(response.status_code, 504)


if __name__ == "__main__":
    unittest.main()
