"""
Deterministic backend API tests.

These tests avoid the real model pipeline and external network calls. Run them
with `npm run test:backend`, or set MANGA_TRANSLATOR_TESTING=1 first (it turns
off the auth token check and keeps backend/.env out of the process), then from
the backend directory:

    python -m unittest test_api.py test_translator.py test_utils.py test_model_wrappers.py test_pipeline.py
"""

import io
import asyncio
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parents[1]

from manga_backend import main
from manga_backend.models.translator import Translator
from manga_backend.pipeline import PaddleOCRUnavailableError


class FakePipeline:
    def __init__(self):
        self.is_ready = True
        self.device = "cpu"
        self.model_status = {
            "detector": {"status": "loaded", "name": "fake-detector"},
            "ocr": {"status": "loaded", "name": "fake-ocr"},
        }
        self.translate_calls = []
        self.detect_calls = []
        self.ocr_calls = []
        self.translate_error = None
        self.paddle_calls = []

    async def translate(self, **kwargs):
        if self.translate_error:
            raise self.translate_error
        self.translate_calls.append(kwargs)
        image = kwargs["image"]
        return {
            "blocks": [{"bbox": [0, 0, 10, 10], "type": "text"}],
            "original_texts": ["source text"],
            "translated_texts": ["hello"],
            "image": image.copy(),
        }

    async def detect(self, image, detector="rtdetr"):
        self.detect_calls.append({"image": image, "detector": detector})
        return [{"bbox": [0, 0, image.shape[1], image.shape[0]], "type": detector}]

    async def ocr(
        self,
        image,
        blocks,
        ocr="manga-ocr",
        google_ocr_api_key=None,
        google_ocr_language_hint=None,
    ):
        self.ocr_calls.append({
            "image": image,
            "blocks": blocks,
            "ocr": ocr,
            "google_ocr_api_key": google_ocr_api_key,
            "google_ocr_language_hint": google_ocr_language_hint,
        })
        return [f"{ocr}:text" for _ in blocks]
    async def paddle_ocr_full(self, image, lang="japan"):
        self.paddle_calls.append({"image": image, "lang": lang})
        return [{"text": "paddle text", "bbox": [0, 0, 10, 10]}]




def make_png_bytes(size=(16, 16), color=(255, 255, 255)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.getvalue()


class BackendApiTests(unittest.TestCase):
    def setUp(self):
        self.original_pipeline = main.pipeline
        self.original_active_translate_requests = main.active_translate_requests
        self.original_last_error = main.last_error
        self.original_max_upload_size = main.MAX_UPLOAD_SIZE
        self.addCleanup(self.restore_main_state)

        self.pipeline = FakePipeline()
        main.pipeline = self.pipeline
        main.active_translate_requests = 0
        main.last_error = None
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def restore_main_state(self):
        main.pipeline = self.original_pipeline
        main.active_translate_requests = self.original_active_translate_requests
        main.last_error = self.original_last_error
        main.MAX_UPLOAD_SIZE = self.original_max_upload_size

    def test_health_reports_fake_pipeline_status(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["models_loaded"])
        self.assertEqual(body["device"], "cpu")
        self.assertIn("uptime_seconds", body)
        self.assertEqual(body["active_translate_requests"], 0)
        self.assertIn("config", body)
        self.assertIn("models", body)
        self.assertIsNone(body["last_error"])

    def test_request_paths_report_503_when_pipeline_degraded(self):
        self.pipeline.is_ready = False

        translate = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"translator": "none", "render": "true"},
        )
        detect = self.client.post(
            "/detect",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"detector": "rtdetr"},
        )
        ocr = self.client.post(
            "/ocr",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"ocr": "paddle-ocr"},
        )

        for response in (translate, detect, ocr):
            self.assertEqual(response.status_code, 503)
            self.assertIn("Pipeline not ready", response.json()["detail"])
        self.assertEqual(self.pipeline.translate_calls, [])
        self.assertEqual(self.pipeline.detect_calls, [])
        self.assertEqual(self.pipeline.ocr_calls, [])

    def test_request_id_header_is_generated_and_preserved(self):
        generated = self.client.get("/health")
        self.assertEqual(generated.status_code, 200)
        self.assertTrue(generated.headers.get("x-request-id"))

        preserved = self.client.get("/health", headers={"X-Request-ID": "req-preserved-1"})
        self.assertEqual(preserved.status_code, 200)
        self.assertEqual(preserved.headers.get("x-request-id"), "req-preserved-1")

    def test_translate_returns_png_and_preserves_ocr_only_translator(self):
        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"translator": "none", "render": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(self.pipeline.translate_calls[-1]["translator"], "none")
        self.assertIsInstance(self.pipeline.translate_calls[-1]["image"], np.ndarray)

    def test_translate_decodes_and_encodes_images_off_the_event_loop(self):
        loops_seen = {}

        def recording(name, function):
            def wrapper(*args):
                try:
                    asyncio.get_running_loop()
                    loops_seen[name] = True
                except RuntimeError:
                    loops_seen[name] = False
                return function(*args)

            return wrapper

        with patch.object(main, "decode_image", recording("decode", main.decode_image)), patch.object(
            main, "encode_png", recording("encode", main.encode_png)
        ):
            response = self.client.post(
                "/translate",
                files={"image": ("page.png", make_png_bytes(), "image/png")},
                data={"translator": "none", "render": "true"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(loops_seen, {"decode": False, "encode": False})
        self.assertEqual(Image.open(io.BytesIO(response.content)).format, "PNG")

    def test_translate_logs_request_id_and_sanitized_metadata(self):
        with self.assertLogs(main.logger, level="INFO") as logs:
            response = self.client.post(
                "/translate",
                headers={"X-Request-ID": "req-log-1"},
                files={"image": ("page.png", make_png_bytes(size=(32, 24)), "image/png")},
                data={
                    "detector": "default",
                    "ocr": "manga-ocr",
                    "translator": "local",
                    "inpainter": "lama_large",
                    "target_lang": "Thai",
                    "local_llm_api_key": "super-secret-local-key",
                },
            )

        self.assertEqual(response.status_code, 200)
        joined = "\n".join(logs.output)
        self.assertIn("req-log-1", joined)
        self.assertIn("width=32", joined)
        self.assertIn("height=24", joined)
        self.assertIn("translator=local", joined)
        self.assertIn("target_lang=Thai", joined)
        self.assertNotIn("local_llm_api_key", joined)
        self.assertNotIn("super-secret-local-key", joined)

    def test_translate_passes_render_and_local_llm_settings_to_pipeline(self):
        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={
                "translator": "local",
                "render": "true",
                "target_lang": "Thai",
                "line_spacing": "1.5",
                "border_size": "3",
                "text_color": "#112233",
                "border_color": "#445566",
                "bg_color": "#abcdef",
                "bg_opacity": "0.25",
                "text_alignment": "left",
                "text_direction": "horizontal",
                "uppercase": "true",
                "auto_scale": "false",
                "font_size": "16",
                "local_llm_base_url": "http://localhost:8000/v1",
                "local_llm_api_key": "local-key",
            },
        )

        self.assertEqual(response.status_code, 200)
        call = self.pipeline.translate_calls[-1]
        self.assertEqual(call["translator"], "local")
        self.assertEqual(call["target_lang"], "Thai")
        self.assertEqual(call["line_spacing"], 1.5)
        self.assertEqual(call["border_size"], 3)
        self.assertEqual(call["text_color"], "#112233")
        self.assertEqual(call["border_color"], "#445566")
        self.assertEqual(call["bg_color"], "#abcdef")
        self.assertEqual(call["bg_opacity"], 0.25)
        self.assertEqual(call["text_alignment"], "left")
        self.assertEqual(call["text_direction"], "horizontal")
        self.assertTrue(call["uppercase"])
        self.assertFalse(call["auto_scale"])
        self.assertEqual(call["preferred_font_size"], 16)
        self.assertEqual(call["local_llm_base_url"], "http://localhost:8000/v1")
        self.assertEqual(call["local_llm_api_key"], "local-key")

    def test_translate_rejects_out_of_range_font_size(self):
        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"font_size": "5"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("font_size must be between 6 and 96", response.text)

    def test_translate_normalizes_supported_pipeline_aliases(self):
        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={
                "detector": "default",
                "ocr": "google",
                "translator": "google",
                "inpainter": "lama",
                "google_ocr_api_key": "vision-key",
                "api_key": "gemini-key",
            },
        )

        self.assertEqual(response.status_code, 200)
        call = self.pipeline.translate_calls[-1]
        self.assertEqual(call["detector"], "rtdetr")
        self.assertEqual(call["ocr"], "google-ocr")
        self.assertEqual(call["translator"], "gemini")
        self.assertEqual(call["inpainter"], "lama_large")

    def test_translate_normalizes_lanpaint_alias(self):
        headers = {}
        if main.BACKEND_AUTH_TOKEN:
            headers[main.BACKEND_AUTH_HEADER] = main.BACKEND_AUTH_TOKEN
        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"translator": "none", "inpainter": "lanpaint"},
            headers=headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.pipeline.translate_calls[-1]["inpainter"],
            "flux2_klein_lanpaint",
        )

    def test_translate_rejects_google_ocr_without_api_key_before_pipeline_call(self):
        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"ocr": "google-ocr"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("google_ocr_api_key", response.json()["detail"])
        self.assertEqual(self.pipeline.translate_calls, [])

    def test_translate_google_ocr_does_not_require_local_ocr_readiness(self):
        self.pipeline.component_ready = lambda component: component == "detector"

        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={
                "ocr": "google-ocr",
                "google_ocr_api_key": "vision-key",
                "render": "false",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.pipeline.translate_calls[-1]["ocr"], "google-ocr")

    def test_translate_json_response_when_render_false(self):
        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"translator": "none", "render": "false"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertEqual(response.json()["translated_texts"], ["hello"])

    def test_rejects_unsupported_detector_before_pipeline_call(self):
        response = self.client.post(
            "/detect",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"detector": "fake-detector"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("detector", response.json()["detail"])
        self.assertEqual(self.pipeline.detect_calls, [])

    def test_detect_returns_blocks_for_supported_detector(self):
        response = self.client.post(
            "/detect",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"detector": "rtdetr"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["blocks"][0]["type"], "rtdetr")
        self.assertEqual(self.pipeline.detect_calls[-1]["detector"], "rtdetr")

    def test_detect_normalizes_animetext_yolo_alias(self):
        response = self.client.post(
            "/detect",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"detector": "anime-text-yolo"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.pipeline.detect_calls[-1]["detector"], "animetext-yolo"
        )

    def test_animetext_detect_does_not_require_rtdetr_readiness(self):
        self.pipeline.component_ready = lambda component: component != "detector"

        response = self.client.post(
            "/detect",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"detector": "animetext-yolo"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.pipeline.detect_calls[-1]["detector"], "animetext-yolo"
        )

    def test_animetext_translate_does_not_require_rtdetr_readiness(self):
        self.pipeline.component_ready = lambda component: component == "ocr"

        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={
                "detector": "animetext-yolo",
                "ocr": "manga-ocr",
                "translator": "none",
                "inpainter": "none",
                "render": "false",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.pipeline.translate_calls[-1]["detector"], "animetext-yolo"
        )

    def test_rtdetr_detect_still_requires_rtdetr_readiness(self):
        self.pipeline.component_ready = lambda component: component != "detector"

        response = self.client.post(
            "/detect",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"detector": "rtdetr"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.pipeline.detect_calls, [])

    def test_detector_initialization_error_is_sanitized(self):
        async def fail_detect(*_args, **_kwargs):
            raise main.DetectorUnavailableError(
                "CUDA failed at C:\\private\\model.onnx", detector="animetext-yolo"
            )

        self.pipeline.detect = fail_detect
        response = self.client.post(
            "/detect",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"detector": "animetext-yolo"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Requested detector runtime is unavailable; "
            "set ANIMETEXT_YOLO_MODEL or authenticate with Hugging Face",
        )
        self.assertNotIn("private", response.text)
        self.assertNotIn("private", main.last_error["error"])

    def test_inpainter_unavailable_error_returns_actionable_path_free_hint(self):
        self.pipeline.translate_error = main.InpainterUnavailableError(
            "ComfyUI not reachable; model at /home/bob/models/flux.safetensors",
            inpainter="flux2_klein_lanpaint",
        )

        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"translator": "none", "inpainter": "flux2_klein_lanpaint"},
        )

        self.assertEqual(response.status_code, 503)
        detail = response.json()["detail"]
        self.assertIn("scripts/comfy-lanpaint/start.ps1", detail)
        self.assertIn("choose LaMa", detail)
        self.assertNotIn("bob", response.text)
        self.assertNotIn("bob", main.last_error["error"])

    def test_inpainter_unavailable_error_without_option_stays_generic(self):
        self.pipeline.translate_error = main.InpainterUnavailableError("boom")

        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"translator": "none", "inpainter": "lama"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"], "Requested inpainter runtime is unavailable"
        )

    def test_health_scrubs_filesystem_paths_from_model_errors(self):
        self.pipeline.model_status = {
            "detector": {"status": "loaded", "name": "fake-detector"},
            "animetext_yolo": {
                "status": "error",
                "error": "Failed to open C:\\Users\\someone\\models\\a.onnx: denied",
            },
            "inpainter": {
                "status": "loaded",
                "last_load_error": {
                    "name": "flux2_klein_lanpaint",
                    "error": "no file at /home/bob/models/flux.gguf.",
                },
            },
        }

        body = self.client.get("/health").json()

        self.assertEqual(
            body["models"]["animetext_yolo"]["error"],
            "Failed to open [path]: denied",
        )
        self.assertEqual(
            body["models"]["inpainter"]["last_load_error"]["error"],
            "no file at [path].",
        )
        self.assertNotIn("someone", self.client.get("/health").text)
        # The live status stays untouched; only the response copy is scrubbed.
        self.assertIn("someone", self.pipeline.model_status["animetext_yolo"]["error"])

    def test_sanitize_error_scrubs_paths_but_keeps_urls(self):
        text = main.sanitize_error(
            RuntimeError(
                "load failed C:\\private\\m.onnx via http://localhost:8188/view and /tmp/x/y"
            )
        )

        self.assertEqual(
            text, "load failed [path] via http://localhost:8188/view and [path]"
        )

    def test_translator_provider_failure_returns_sanitized_502(self):
        provider = Translator(translator_type="openai")
        with patch.object(
            provider,
            "_translate_openai",
            side_effect=Exception("Incorrect API key provided: sk-secret"),
        ):
            with self.assertLogs("manga_backend.models.translator", level="ERROR") as logs:
                with self.assertRaises(main.TranslatorError) as raised:
                    asyncio.run(provider.translate(["text"], "English"))
        self.assertNotIn("sk-secret", "\n".join(logs.output))
        self.pipeline.translate_error = raised.exception

        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"translator": "none"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json()["detail"], "Translation provider request failed"
        )
        self.assertNotIn("sk-secret", response.text)

    def test_translator_timeout_returns_504(self):
        provider = Translator(translator_type="openai")
        with patch.object(
            provider, "_translate_openai", side_effect=asyncio.TimeoutError()
        ):
            with self.assertLogs("manga_backend.models.translator", level="ERROR"):
                with self.assertRaises(main.TranslatorTimeoutError) as raised:
                    asyncio.run(provider.translate(["text"], "English"))
        self.pipeline.translate_error = raised.exception

        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"translator": "none"},
        )

        self.assertEqual(response.status_code, 504)
        self.assertEqual(
            response.json()["detail"], "Translation provider request timed out"
        )

    def test_ocr_runs_detection_then_ocr(self):
        response = self.client.post(
            "/ocr",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"ocr": "paddle-ocr"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["texts"], ["paddle-ocr:text"])
        self.assertEqual(len(self.pipeline.detect_calls), 1)
        self.assertEqual(self.pipeline.ocr_calls[-1]["ocr"], "paddle-ocr")

    def test_ocr_passes_google_ocr_key_and_language_hint(self):
        response = self.client.post(
            "/ocr",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={
                "ocr": "google-ocr",
                "google_ocr_api_key": "vision-key",
                "google_ocr_language_hint": "ja",
            },
        )

        self.assertEqual(response.status_code, 200)
        call = self.pipeline.ocr_calls[-1]
        self.assertEqual(call["ocr"], "google-ocr")
        self.assertEqual(call["google_ocr_api_key"], "vision-key")
        self.assertEqual(call["google_ocr_language_hint"], "ja")

    def test_ocr_rejects_google_ocr_without_api_key_before_pipeline_call(self):
        response = self.client.post(
            "/ocr",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"ocr": "google-ocr"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("google_ocr_api_key", response.json()["detail"])
        self.assertEqual(self.pipeline.detect_calls, [])
        self.assertEqual(self.pipeline.ocr_calls, [])

    def test_ocr_google_ocr_does_not_require_local_ocr_readiness(self):
        self.pipeline.component_ready = lambda component: component == "detector"

        response = self.client.post(
            "/ocr",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={
                "ocr": "google-ocr",
                "google_ocr_api_key": "vision-key",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.pipeline.ocr_calls[-1]["ocr"], "google-ocr")

    def test_rejects_unsupported_translate_pipeline_options_before_pipeline_call(self):
        cases = [
            ("detector", "ctd"),
            ("ocr", "bad-ocr"),
            ("translator", "deepl"),
            ("inpainter", "bad-inpainter"),
        ]

        for field_name, value in cases:
            with self.subTest(field_name=field_name):
                self.pipeline.translate_calls.clear()

                response = self.client.post(
                    "/translate",
                    files={"image": ("page.png", make_png_bytes(), "image/png")},
                    data={field_name: value},
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn(field_name, response.json()["detail"])
                self.assertEqual(self.pipeline.translate_calls, [])

    def test_rejects_unsupported_ocr_engine_before_pipeline_call(self):
        response = self.client.post(
            "/ocr",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"ocr": "bad-ocr"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("ocr", response.json()["detail"])
        self.assertEqual(self.pipeline.detect_calls, [])
        self.assertEqual(self.pipeline.ocr_calls, [])

    def test_rejects_unsupported_paddle_ocr_language_before_loading_engine(self):
        response = self.client.post(
            "/paddle-ocr",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"lang": "thai"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("lang", response.json()["detail"])

    def test_paddle_ocr_uses_status_aware_pipeline_path(self):
        response = self.client.post(
            "/paddle-ocr",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"lang": "en"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(len(self.pipeline.paddle_calls), 1)
        self.assertEqual(self.pipeline.paddle_calls[0]["lang"], "en")
        self.assertIsInstance(self.pipeline.paddle_calls[0]["image"], np.ndarray)

    def test_paddle_routes_are_admitted_before_paddle_ocr_is_loaded(self):
        # PaddleOCR loads on the first request that needs it, so a component that
        # is not ready (not loaded yet, or a previous load failed) is not a 503.
        self.pipeline.component_ready = lambda component: component != "paddle_ocr"

        full = self.client.post(
            "/paddle-ocr",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"lang": "en"},
        )
        ocr = self.client.post(
            "/ocr",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"ocr": "paddle-ocr"},
        )

        self.assertEqual(full.status_code, 200)
        self.assertEqual(ocr.status_code, 200)
        self.assertEqual(len(self.pipeline.paddle_calls), 1)

    def test_paddle_load_failure_is_503_and_leaves_other_ocr_routes_alone(self):
        async def failing_paddle(image, lang="japan"):
            raise PaddleOCRUnavailableError("PaddleOCR failed to load: /secret/path/x")

        self.pipeline.paddle_ocr_full = failing_paddle
        self.pipeline.component_ready = lambda component: component != "paddle_ocr"

        failed = self.client.post(
            "/paddle-ocr",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"lang": "en"},
        )
        manga = self.client.post(
            "/ocr",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"ocr": "manga-ocr"},
        )

        self.assertEqual(failed.status_code, 503)
        self.assertIn("models.paddle_ocr", failed.json()["detail"])
        self.assertNotIn("/secret/path", failed.json()["detail"])
        self.assertEqual(manga.status_code, 200)

    def test_rejects_invalid_image_type(self):
        response = self.client.post(
            "/translate",
            files={"image": ("page.txt", b"not an image", "text/plain")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid image type", response.json()["detail"])

    def test_rejects_invalid_image_data(self):
        response = self.client.post(
            "/translate",
            files={"image": ("page.png", b"not an image", "image/png")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid image data", response.json()["detail"])

    def test_rejects_uploads_over_configured_limit(self):
        main.MAX_UPLOAD_SIZE = 4

        response = self.client.post(
            "/translate",
            files={"image": ("page.png", b"12345", "image/png")},
        )

        self.assertEqual(response.status_code, 413)
        self.assertIn("File too large", response.json()["detail"])

    def test_upload_reader_uses_bounded_read(self):
        class FakeUpload:
            content_type = "image/png"

            def __init__(self):
                self.read_sizes = []

            async def read(self, size=-1):
                self.read_sizes.append(size)
                return b"12345"

        main.MAX_UPLOAD_SIZE = 4
        upload = FakeUpload()

        with self.assertRaises(main.HTTPException) as raised:
            asyncio.run(main.read_upload_image(upload))

        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(upload.read_sizes, [5])

    def test_rejects_invalid_render_settings_before_pipeline_call(self):
        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"bg_opacity": "1.5"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("bg_opacity", response.json()["detail"])
        self.assertEqual(self.pipeline.translate_calls, [])

    def test_rejects_invalid_text_alignment_before_pipeline_call(self):
        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"text_alignment": "justify"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("text_alignment", response.json()["detail"])
        self.assertEqual(self.pipeline.translate_calls, [])

    def test_reports_503_when_pipeline_not_initialized(self):
        main.pipeline = None

        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Pipeline not initialized", response.json()["detail"])

    def test_pipeline_value_errors_are_client_errors(self):
        self.pipeline.translate_error = ValueError("bad render option")

        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "bad render option")

    def test_unexpected_pipeline_errors_are_server_errors(self):
        self.pipeline.translate_error = RuntimeError("model failed")

        response = self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], main.INTERNAL_ERROR_DETAIL)

    def test_translate_failure_updates_health_last_error_and_redacts_secrets(self):
        self.pipeline.translate_error = RuntimeError("model failed with secret-value")

        with self.assertLogs(main.logger, level="ERROR") as logs:
            response = self.client.post(
                "/translate",
                headers={"X-Request-ID": "req-fail-1"},
                files={"image": ("page.png", make_png_bytes(), "image/png")},
                data={
                    "translator": "local",
                    "local_llm_api_key": "secret-value",
                    "api_key": "cloud-secret",
                    "google_ocr_api_key": "vision-secret",
                },
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers.get("x-request-id"), "req-fail-1")
        self.assertEqual(response.json()["detail"], main.INTERNAL_ERROR_DETAIL)
        self.assertNotIn("model failed with secret-value", response.text)
        self.assertNotIn("secret-value", response.text)
        self.assertNotIn("cloud-secret", response.text)
        self.assertNotIn("vision-secret", response.text)
        joined = "\n".join(logs.output)
        self.assertIn("req-fail-1", joined)
        self.assertIn("[redacted_secret]", joined)
        self.assertNotIn("secret-value", joined)
        self.assertNotIn("cloud-secret", joined)
        self.assertNotIn("vision-secret", joined)
        self.assertNotIn("api_key", joined)
        self.assertNotIn("google_ocr_api_key", joined)
        self.assertNotIn("local_llm_api_key", joined)

        health = self.client.get("/health").json()
        self.assertEqual(health["last_error"]["request_id"], "req-fail-1")
        self.assertEqual(health["last_error"]["status"], 500)
        self.assertIn("[redacted_secret]", health["last_error"]["error"])
        self.assertNotIn("secret-value", health["last_error"]["error"])

    def test_secondary_route_failures_record_request_id_and_path(self):
        async def failing(*args, **kwargs):
            raise RuntimeError("model exploded")

        self.pipeline.detect = failing
        self.pipeline.paddle_ocr_full = failing
        cases = [
            ("/detect", {}),
            ("/ocr", {}),
            ("/paddle-ocr", {"lang": "en"}),
        ]
        for path, data in cases:
            with self.subTest(path=path):
                request_id = f"req{path.replace('/', '-')}"
                with self.assertLogs(main.logger, level="ERROR") as logs:
                    response = self.client.post(
                        path,
                        headers={"X-Request-ID": request_id},
                        files={"image": ("page.png", make_png_bytes(), "image/png")},
                        data=data,
                    )

                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.headers.get("x-request-id"), request_id)
                self.assertIn(f"request_id={request_id}", "\n".join(logs.output))
                health = self.client.get("/health").json()
                self.assertEqual(health["last_error"]["request_id"], request_id)
                self.assertEqual(health["last_error"]["path"], path)
                self.assertEqual(health["last_error"]["status"], 500)

    def test_allows_chrome_extension_origin(self):
        response = self.client.get(
            "/health",
            headers={"Origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        )

        self.assertEqual(response.status_code, 200)

    def test_rejects_untrusted_browser_origin(self):
        response = self.client.get(
            "/health",
            headers={"Origin": "https://example.com"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("Origin is not allowed", response.json()["detail"])


def env_without(*names, **extra):
    """Return a patch.dict that removes the named variables and sets extras."""
    env = {key: value for key, value in os.environ.items() if key not in names}
    env.update(extra)
    return patch.dict(os.environ, env, clear=True)


class TranslatePreflightTests(unittest.TestCase):
    def setUp(self):
        self.original_pipeline = main.pipeline
        self.original_last_error = main.last_error
        self.pipeline = FakePipeline()
        main.pipeline = self.pipeline
        self.client = TestClient(main.app)
        self.addCleanup(self.restore_main_state)
        self.addCleanup(self.client.close)

    def restore_main_state(self):
        main.pipeline = self.original_pipeline
        main.last_error = self.original_last_error

    def post(self, **data):
        return self.client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data=data,
        )

    def assert_rejected_before_model_work(self, response, *fragments):
        self.assertEqual(response.status_code, 400)
        for fragment in fragments:
            self.assertIn(fragment, response.json()["detail"])
        self.assertEqual(self.pipeline.translate_calls, [])
        self.assertEqual(self.pipeline.detect_calls, [])
        self.assertEqual(self.pipeline.ocr_calls, [])

    def test_cloud_translators_require_an_api_key_before_model_work(self):
        cases = {
            "gemini": "GEMINI_API_KEY",
            "google": "GEMINI_API_KEY",
            "openai": "OPENAI_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }
        with env_without(*set(cases.values())):
            for translator, env_var in cases.items():
                with self.subTest(translator=translator):
                    response = self.post(translator=translator)
                    self.assert_rejected_before_model_work(response, "api_key", env_var)

    def test_cloud_translator_accepts_form_key_or_matching_env_key(self):
        with env_without("OPENAI_API_KEY", "GEMINI_API_KEY"):
            self.assertEqual(self.post(translator="openai", api_key="form-key").status_code, 200)
        with env_without("GEMINI_API_KEY", OPENAI_API_KEY="env-key"):
            self.assertEqual(self.post(translator="openai").status_code, 200)
            self.pipeline.translate_calls.clear()
            self.assert_rejected_before_model_work(self.post(translator="gemini"), "GEMINI_API_KEY")

    def test_local_translator_rejects_remote_base_url_before_model_work(self):
        with env_without("ALLOW_REMOTE_LOCAL_LLM", "LOCAL_LLM_BASE_URL"):
            response = self.post(translator="local", local_llm_base_url="http://example.com:8000/v1")
            self.assert_rejected_before_model_work(response, "localhost or a loopback")

            response = self.post(translator="local", local_llm_base_url="not-a-url")
            self.assert_rejected_before_model_work(response, "http(s) URL")

    def test_local_translator_checks_env_default_base_url(self):
        with env_without("ALLOW_REMOTE_LOCAL_LLM", LOCAL_LLM_BASE_URL="http://example.com:8000/v1"):
            response = self.post(translator="local")
            self.assert_rejected_before_model_work(response, "localhost or a loopback")

            ok = self.post(translator="local", local_llm_base_url="http://127.0.0.1:8000/v1")
            self.assertEqual(ok.status_code, 200)

    def test_target_lang_is_validated_before_model_work(self):
        for label, value in (
            ("newline", "English\nINJECTED log line"),
            ("carriage return", "English\rx"),
            ("blank", "   "),
            ("too long", "x" * 65),
        ):
            with self.subTest(case=label):
                self.assert_rejected_before_model_work(self.post(target_lang=value), "target_lang")

        self.assertEqual(self.post(target_lang="x" * 64).status_code, 200)

    def test_model_name_is_validated_before_model_work(self):
        for label, value in (
            ("space", "gpt 4"),
            ("newline", "gpt-4o\n"),
            ("query", "model?key=1"),
            ("too long", "m" * 129),
        ):
            with self.subTest(case=label):
                self.assert_rejected_before_model_work(
                    self.post(translator="local", model=value), "model"
                )

        for value in ("gemini-2.0-flash", "google/gemini-2.0-flash-001", "org/model:Q4_K_M@v1.5+x"):
            with self.subTest(valid=value):
                self.assertEqual(self.post(translator="local", model=value).status_code, 200)

    def test_google_ocr_language_hint_is_validated_before_model_work(self):
        response = self.post(
            ocr="google-ocr", google_ocr_api_key="vision-key", google_ocr_language_hint="ja\nx"
        )
        self.assert_rejected_before_model_work(response, "google_ocr_language_hint")

        ok = self.post(
            ocr="google-ocr", google_ocr_api_key="vision-key", google_ocr_language_hint="zh-Hant"
        )
        self.assertEqual(ok.status_code, 200)


class RequestLimitTests(unittest.TestCase):
    def setUp(self):
        self.original_state = (
            main.pipeline,
            main.active_translate_requests,
            main.last_error,
            main.MAX_UPLOAD_SIZE,
            main.UPLOAD_OVERHEAD_BYTES,
            main.MAX_CONCURRENT_REQUESTS,
            main.QUEUE_TIMEOUT_SECONDS,
            main._request_limiter,
        )
        self.addCleanup(self.restore_main_state)
        main._request_limiter = None
        main.active_translate_requests = 0

    def restore_main_state(self):
        (
            main.pipeline,
            main.active_translate_requests,
            main.last_error,
            main.MAX_UPLOAD_SIZE,
            main.UPLOAD_OVERHEAD_BYTES,
            main.MAX_CONCURRENT_REQUESTS,
            main.QUEUE_TIMEOUT_SECONDS,
            main._request_limiter,
        ) = self.original_state

    @staticmethod
    def make_blocking_pipeline():
        class BlockingPipeline(FakePipeline):
            def __init__(self):
                super().__init__()
                self.in_flight = 0
                self.max_in_flight = 0
                self.gate = asyncio.Event()

            async def _block(self):
                self.in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
                try:
                    await self.gate.wait()
                finally:
                    self.in_flight -= 1

            async def translate(self, **kwargs):
                await self._block()
                return await super().translate(**kwargs)

            async def detect(self, image, detector="rtdetr"):
                await self._block()
                return await super().detect(image, detector=detector)

            async def paddle_ocr_full(self, image, lang="japan"):
                await self._block()
                return await super().paddle_ocr_full(image, lang=lang)

        return BlockingPipeline()

    @staticmethod
    async def wait_until(predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError("condition not reached in time")
            await asyncio.sleep(0.01)

    @staticmethod
    def post(client, path):
        data = {
            "/translate": {"translator": "none", "render": "false"},
            "/detect": {"detector": "rtdetr"},
            "/ocr": {"ocr": "paddle-ocr"},
            "/paddle-ocr": {"lang": "en"},
        }[path]
        return client.post(
            path,
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data=data,
        )

    def run_scenario(self, scenario):
        async def runner():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await scenario(client)

        return asyncio.run(runner())

    def test_pipeline_calls_never_exceed_the_concurrency_limit_on_any_route(self):
        pipeline = self.make_blocking_pipeline()
        main.pipeline = pipeline
        main.MAX_CONCURRENT_REQUESTS = 2

        async def scenario(client):
            tasks = [
                asyncio.create_task(self.post(client, path))
                for path in ("/translate", "/detect", "/ocr", "/paddle-ocr")
            ]
            await self.wait_until(lambda: pipeline.in_flight == 2)
            await asyncio.sleep(0.1)
            self.assertEqual(pipeline.in_flight, 2)
            # All four routes count in the gauge, queued requests included.
            self.assertEqual(main.active_translate_requests, 4)
            pipeline.gate.set()
            return await asyncio.gather(*tasks)

        responses = self.run_scenario(scenario)

        self.assertEqual([response.status_code for response in responses], [200] * 4)
        self.assertEqual(pipeline.max_in_flight, 2)
        self.assertEqual(main.active_translate_requests, 0)

    def test_concurrency_limit_of_one_serializes_requests(self):
        pipeline = self.make_blocking_pipeline()
        main.pipeline = pipeline
        main.MAX_CONCURRENT_REQUESTS = 1

        async def scenario(client):
            tasks = [asyncio.create_task(self.post(client, "/translate")) for _ in range(3)]
            await self.wait_until(lambda: pipeline.in_flight == 1)
            await asyncio.sleep(0.1)
            self.assertEqual(pipeline.in_flight, 1)
            pipeline.gate.set()
            return await asyncio.gather(*tasks)

        responses = self.run_scenario(scenario)

        self.assertEqual([response.status_code for response in responses], [200] * 3)
        self.assertEqual(pipeline.max_in_flight, 1)

    def test_request_returns_429_with_retry_after_only_after_queue_timeout(self):
        pipeline = self.make_blocking_pipeline()
        main.pipeline = pipeline
        main.MAX_CONCURRENT_REQUESTS = 1
        main.QUEUE_TIMEOUT_SECONDS = 0.1

        async def scenario(client):
            first = asyncio.create_task(self.post(client, "/translate"))
            await self.wait_until(lambda: pipeline.in_flight == 1)
            rejected = await self.post(client, "/translate")
            pipeline.gate.set()
            return rejected, await first

        rejected, first = self.run_scenario(scenario)

        self.assertEqual(rejected.status_code, 429)
        self.assertEqual(rejected.headers["retry-after"], str(main.QUEUE_RETRY_AFTER_SECONDS))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(pipeline.translate_calls), 1)
        self.assertEqual(main.active_translate_requests, 0)

    def test_slot_is_released_when_the_pipeline_call_fails(self):
        pipeline = FakePipeline()
        pipeline.translate_error = RuntimeError("model failed")
        main.pipeline = pipeline
        main.MAX_CONCURRENT_REQUESTS = 1
        main.QUEUE_TIMEOUT_SECONDS = 0.5

        async def scenario(client):
            return [await self.post(client, "/translate") for _ in range(3)]

        responses = self.run_scenario(scenario)

        self.assertEqual([response.status_code for response in responses], [500] * 3)
        self.assertEqual(main.active_translate_requests, 0)

    def test_oversize_content_length_is_rejected_before_the_body_is_parsed(self):
        pipeline = FakePipeline()
        main.pipeline = pipeline
        main.MAX_UPLOAD_SIZE = 4
        main.UPLOAD_OVERHEAD_BYTES = 1000

        with TestClient(main.app) as client:
            response = client.post(
                "/translate",
                files={"image": ("page.png", b"x" * 3000, "image/png")},
            )
            # A body inside the overhead allowance still reaches read_upload_image.
            small = client.post(
                "/translate",
                files={"image": ("page.png", b"12345", "image/png")},
            )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"], "Request body too large")
        self.assertTrue(response.headers.get("x-request-id"))
        self.assertEqual(pipeline.translate_calls, [])
        self.assertEqual(small.status_code, 413)
        self.assertEqual(small.json()["detail"], "File too large")

    def test_absurdly_long_content_length_is_rejected_instead_of_crashing(self):
        pipeline = FakePipeline()
        main.pipeline = pipeline

        # Python refuses to int() a digit string this long, so it must never get that far.
        with TestClient(main.app) as client:
            response = client.post(
                "/translate",
                content=b"x",
                headers={"Content-Length": "9" * 5000},
            )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"], "Request body too large")
        self.assertEqual(pipeline.translate_calls, [])

    def test_limits_can_be_configured_from_the_environment(self):
        with patch.dict(os.environ, {"BACKEND_MAX_CONCURRENT": "5", "BACKEND_QUEUE_TIMEOUT_SECONDS": "12.5"}):
            self.assertEqual(main._env_positive_number("BACKEND_MAX_CONCURRENT", 2), 5)
            self.assertEqual(main._env_positive_number("BACKEND_QUEUE_TIMEOUT_SECONDS", 600.0, float), 12.5)
        for raw in ("0", "-3", "abc", "nan", ""):
            with self.subTest(raw=raw), patch.dict(os.environ, {"BACKEND_MAX_CONCURRENT": raw}):
                self.assertEqual(main._env_positive_number("BACKEND_MAX_CONCURRENT", 2), 2)
        with env_without("BACKEND_MAX_CONCURRENT"):
            self.assertEqual(main._env_positive_number("BACKEND_MAX_CONCURRENT", 2), 2)

    def test_module_constants_are_read_from_the_environment_at_import(self):
        # Other tests overwrite main.MAX_CONCURRENT_REQUESTS in-process, so import
        # main fresh in a child process to prove the env vars feed the constants.
        env = {
            **os.environ,
            "MANGA_TRANSLATOR_TESTING": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "BACKEND_MAX_CONCURRENT": "5",
            "BACKEND_QUEUE_TIMEOUT_SECONDS": "12.5",
        }
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from manga_backend import main; print(main.MAX_CONCURRENT_REQUESTS, main.QUEUE_TIMEOUT_SECONDS)",
            ],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "5 12.5")


class SlowStartPipeline(FakePipeline):
    """Fake pipeline whose initialize() blocks until the test releases it."""

    instances = []

    def __init__(self, device="cpu"):
        super().__init__()
        self.device = device
        self.is_initialized = False
        self.release = threading.Event()
        self.fail_with = None
        self.init_cancelled = False
        self.disposed = False
        self.model_status = {
            "detector": {"status": "not_loaded", "name": "fake-detector"},
            "ocr": {"status": "not_loaded", "name": "fake-ocr"},
        }
        SlowStartPipeline.instances.append(self)

    @property
    def is_ready(self):
        return self.is_initialized and self.model_status["detector"]["status"] == "loaded"

    @is_ready.setter
    def is_ready(self, _value):
        pass

    def component_ready(self, component):
        return self.is_initialized and self.model_status.get(component, {}).get("status") == "loaded"

    async def initialize(self):
        for details in self.model_status.values():
            details["status"] = "loading"
        try:
            while not self.release.is_set():
                await asyncio.sleep(0.005)
        except asyncio.CancelledError:
            self.init_cancelled = True
            raise
        if self.fail_with is not None:
            raise self.fail_with
        for details in self.model_status.values():
            details["status"] = "loaded"
        self.is_initialized = True

    def dispose(self):
        self.disposed = True


class StartupLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.original_pipeline = main.pipeline
        self.original_last_error = main.last_error
        self.original_limiter = main._request_limiter
        SlowStartPipeline.instances = []
        main.pipeline = None
        main.last_error = None
        main._request_limiter = None
        patcher = patch.multiple(
            main,
            TranslationPipeline=SlowStartPipeline,
            get_device=lambda use_gpu=False: "cpu",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.restore_main_state)

    def restore_main_state(self):
        main.pipeline = self.original_pipeline
        main.last_error = self.original_last_error
        main._request_limiter = self.original_limiter

    def wait_for_health_status(self, client, expected, timeout=5.0):
        deadline = time.monotonic() + timeout
        while True:
            body = client.get("/health").json()
            if body["status"] == expected:
                return body
            if time.monotonic() > deadline:
                self.fail(f"health never reported {expected!r}; last status {body['status']!r}")
            time.sleep(0.01)

    @staticmethod
    def translate(client):
        return client.post(
            "/translate",
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            data={"translator": "none", "render": "false", "inpainter": "none"},
        )

    def test_backend_serves_while_models_load_and_becomes_ready(self):
        with TestClient(main.app) as client:
            pipeline = SlowStartPipeline.instances[0]

            root = client.get("/")
            self.assertEqual(root.status_code, 200)
            self.assertEqual(root.json()["status"], "ok")

            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["status"], "loading")
            self.assertFalse(health.json()["models_loaded"])
            self.assertEqual(health.json()["models"]["detector"]["status"], "loading")

            blocked = self.translate(client)
            self.assertEqual(blocked.status_code, 503)
            self.assertIn("Backend component unavailable", blocked.json()["detail"])
            self.assertEqual(pipeline.translate_calls, [])

            pipeline.release.set()
            ready = self.wait_for_health_status(client, "ok")
            self.assertTrue(ready["models_loaded"])
            self.assertEqual(ready["components"], {"detector": True, "ocr": True})
            self.assertEqual(self.translate(client).status_code, 200)

        self.assertTrue(pipeline.disposed)
        self.assertIsNone(main.pipeline)

    def test_backend_stays_loading_until_the_inpainter_warm_up_finishes(self):
        warm_release = threading.Event()
        warm_calls = []

        async def warm_up(self):
            warm_calls.append("started")
            await asyncio.to_thread(warm_release.wait, 5.0)
            warm_calls.append("finished")

        with patch.object(SlowStartPipeline, "warm_up", warm_up, create=True):
            with TestClient(main.app) as client:
                pipeline = SlowStartPipeline.instances[0]
                pipeline.release.set()

                deadline = time.monotonic() + 5.0
                while warm_calls != ["started"] and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(warm_calls, ["started"])
                self.assertEqual(client.get("/health").json()["status"], "loading")
                self.assertEqual(self.translate(client).status_code, 503)

                warm_release.set()
                self.wait_for_health_status(client, "ok")
                self.assertEqual(warm_calls, ["started", "finished"])
                self.assertEqual(self.translate(client).status_code, 200)

    def test_shutdown_cancels_a_pending_initialization_and_disposes(self):
        with TestClient(main.app) as client:
            pipeline = SlowStartPipeline.instances[0]
            self.assertEqual(client.get("/health").json()["status"], "loading")
            task = main.app.state.pipeline_init_task
            self.assertFalse(task.done())

        self.assertTrue(pipeline.init_cancelled)
        self.assertTrue(task.cancelled())
        self.assertTrue(pipeline.disposed)
        self.assertIsNone(main.app.state.pipeline_init_task)
        self.assertIsNone(main.pipeline)

    def test_failed_initialization_is_recorded_and_reported_degraded(self):
        with TestClient(main.app) as client:
            pipeline = SlowStartPipeline.instances[0]
            pipeline.fail_with = RuntimeError("download failed")
            pipeline.release.set()

            health = self.wait_for_health_status(client, "degraded")

            self.assertFalse(health["models_loaded"])
            self.assertEqual(health["last_error"]["path"], "startup")
            self.assertIn("download failed", health["last_error"]["error"])
            self.assertEqual(health["models"]["detector"]["status"], "error")
            self.assertEqual(self.translate(client).status_code, 503)

    def test_injected_pipeline_is_used_without_initialization_or_disposal(self):
        injected = FakePipeline()
        main.pipeline = injected

        with TestClient(main.app) as client:
            self.assertEqual(client.get("/health").json()["status"], "ok")
            self.assertIsNone(main.app.state.pipeline_init_task)

        self.assertEqual(SlowStartPipeline.instances, [])
        self.assertIs(main.pipeline, injected)

    def test_testing_mode_logs_a_warning_that_auth_is_disabled(self):
        main.pipeline = FakePipeline()

        for testing_mode, expect_warning in ((True, True), (False, False)):
            with self.subTest(testing_mode=testing_mode):
                with patch.object(main, "TESTING_MODE", testing_mode), \
                        self.assertLogs(main.logger, level="INFO") as logs:
                    with TestClient(main.app):
                        pass

                warned = [
                    record
                    for record in logs.records
                    if record.levelname == "WARNING"
                    and "authentication is DISABLED" in record.getMessage()
                ]
                self.assertEqual(bool(warned), expect_warning)


class BackendEnvLoadingTests(unittest.TestCase):
    def test_testing_flag_keeps_backend_dotenv_out_of_the_process(self):
        with patch.dict(os.environ, {"MANGA_TRANSLATOR_TESTING": "1"}), \
                patch.object(main, "load_dotenv") as load_dotenv:
            self.assertFalse(main.load_backend_env())

        load_dotenv.assert_not_called()

    def test_dotenv_is_loaded_when_the_testing_flag_is_off(self):
        for value in (None, "0", ""):
            with self.subTest(flag=value):
                overrides = {} if value is None else {"MANGA_TRANSLATOR_TESTING": value}
                with env_without("MANGA_TRANSLATOR_TESTING", **overrides), \
                        patch.object(main, "load_dotenv") as load_dotenv:
                    self.assertTrue(main.load_backend_env())

                load_dotenv.assert_called_once()
                self.assertEqual(Path(load_dotenv.call_args.args[0]).name, ".env")
                self.assertEqual(Path(load_dotenv.call_args.args[0]).parent, BACKEND_DIR)


if __name__ == "__main__":
    unittest.main()
