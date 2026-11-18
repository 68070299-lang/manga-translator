"""Authentication boundary tests for the local backend."""

import io
import logging
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from manga_backend.api.app import create_app
from manga_backend.config import Limits, Settings

API_LOGGER = logging.getLogger("manga_backend.api")
TOKEN_HEADER = "X-Manga-Translator-Token"
EXPENSIVE_POST_ROUTES = ("/translate", "/detect", "/ocr", "/paddle-ocr")


class RecordingPipeline:
    """Pipeline that records any call so auth tests can prove none happened."""

    is_ready = True
    device = "cpu"
    model_status = {"detector": {"status": "loaded"}}

    def __init__(self):
        self.calls = []

    async def translate(self, **kwargs):
        self.calls.append("translate")
        return {"blocks": [], "original_texts": [], "translated_texts": []}

    async def detect(self, *args, **kwargs):
        self.calls.append("detect")
        return []

    async def ocr(self, *args, **kwargs):
        self.calls.append("ocr")
        return []

    async def paddle_ocr_full(self, *args, **kwargs):
        self.calls.append("paddle_ocr_full")
        return []


def make_png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


class BackendAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = RecordingPipeline()
        self.use_app()

    def use_app(self, auth_token="test-backend-token", limits=None):
        """(Re)build the app and client with the given token configuration."""
        client = getattr(self, "client", None)
        if client is not None:
            client.close()
        settings = Settings(auth_token=auth_token, testing=False)
        self.app = create_app(settings, limits=limits, pipeline=self.pipeline)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()

    def post_image(self, path, headers=None):
        return self.client.post(
            path,
            files={"image": ("page.png", make_png_bytes(), "image/png")},
            headers=headers or {},
        )

    def test_health_rejects_missing_or_invalid_tokens(self):
        self.assertEqual(self.client.get("/health").status_code, 401)
        self.assertEqual(
            self.client.get("/health", headers={"X-Manga-Translator-Token": "wrong"}).status_code,
            401,
        )

    def test_health_accepts_the_configured_token(self):
        response = self.client.get(
            "/health",
            headers={"X-Manga-Translator-Token": "test-backend-token"},
        )

        self.assertEqual(response.status_code, 200)

    def test_backend_refuses_requests_until_a_token_is_configured(self):
        self.use_app(auth_token="")

        response = self.client.get("/health")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Backend authentication is not configured")

    def test_non_ascii_token_header_is_rejected_with_401_not_500(self):
        for raw_value in (b"\xe9", b"caf\xe9-token", "é".encode("latin-1")):
            with self.subTest(raw_value=raw_value):
                response = self.client.get("/health", headers={TOKEN_HEADER: raw_value})

                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["detail"], "Invalid backend authentication token")

    def test_non_ascii_configured_token_does_not_crash_authentication(self):
        self.use_app(auth_token="töken")

        response = self.client.get("/health", headers={TOKEN_HEADER: "wrong"})

        self.assertEqual(response.status_code, 401)

    def test_expensive_routes_reject_missing_and_wrong_tokens_without_calling_the_pipeline(self):
        for path in EXPENSIVE_POST_ROUTES:
            for label, headers in (("missing", {}), ("wrong", {TOKEN_HEADER: "wrong"})):
                with self.subTest(path=path, token=label):
                    response = self.post_image(path, headers)

                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()["detail"], "Invalid backend authentication token")
        self.assertEqual(self.pipeline.calls, [])

    def test_expensive_routes_accept_the_configured_token(self):
        for path in EXPENSIVE_POST_ROUTES:
            with self.subTest(path=path):
                response = self.post_image(path, {TOKEN_HEADER: "test-backend-token"})

                self.assertEqual(response.status_code, 200)
        self.assertTrue(self.pipeline.calls)

    def test_generated_docs_are_not_exposed(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            with self.subTest(path=path, token="missing"):
                self.assertIn(self.client.get(path).status_code, (401, 404))
            with self.subTest(path=path, token="valid"):
                response = self.client.get(path, headers={TOKEN_HEADER: "test-backend-token"})

                self.assertEqual(response.status_code, 404)

    def test_every_path_except_liveness_and_preflight_requires_the_token(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/anything-else").status_code, 401)
        self.assertEqual(self.client.post("/").status_code, 401)
        self.assertEqual(self.client.delete("/health").status_code, 401)

        preflight = self.client.options(
            "/translate",
            headers={
                "Origin": "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-manga-translator-token",
            },
        )
        self.assertEqual(preflight.status_code, 200)

    def test_unauthenticated_oversize_requests_get_401_before_the_size_check(self):
        self.use_app(limits=Limits(max_upload_size=4, upload_overhead_bytes=1000))
        files = {"image": ("page.png", b"x" * 3000, "image/png")}

        unauthenticated = self.client.post("/translate", files=files)
        authenticated = self.client.post(
            "/translate", files=files, headers={TOKEN_HEADER: "test-backend-token"}
        )

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(authenticated.status_code, 413)
        self.assertEqual(self.pipeline.calls, [])

    def test_auth_and_size_rejections_are_logged_without_the_token_or_last_error(self):
        limits = Limits(max_upload_size=4, upload_overhead_bytes=1000)
        self.use_app(limits=limits)
        oversize = {"image": ("page.png", b"x" * 3000, "image/png")}
        wrong_token = "wrong-secret-token"

        with self.assertLogs(API_LOGGER, level="INFO") as logs:
            self.client.get(
                "/health", headers={TOKEN_HEADER: wrong_token, "X-Request-ID": "req-401"}
            )
            self.client.post(
                "/translate",
                files=oversize,
                headers={TOKEN_HEADER: "test-backend-token", "X-Request-ID": "req-413"},
            )
            self.assertIsNone(self.app.state.backend.last_error)
            self.use_app(auth_token="", limits=limits)
            self.client.get("/health", headers={"X-Request-ID": "req-503"})

        joined = "\n".join(logs.output)
        self.assertIn("request_id=req-401 method=GET path=/health status=401", joined)
        self.assertIn("request_id=req-413 method=POST path=/translate status=413", joined)
        self.assertIn("request_id=req-503 method=GET path=/health status=503", joined)
        self.assertNotIn(wrong_token, joined)
        self.assertNotIn("test-backend-token", joined)
        self.assertIsNone(self.app.state.backend.last_error)


if __name__ == "__main__":
    unittest.main()
