"""Tests for the live-backend smoke-test command."""

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpx


SMOKE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backend-smoke.py"
SPEC = importlib.util.spec_from_file_location("backend_smoke", SMOKE_SCRIPT)
backend_smoke = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(backend_smoke)


class BackendSmokeTests(unittest.TestCase):
    @staticmethod
    def ready_health(device="cpu", **components):
        readiness = {"detector": True, "ocr": True, "paddle_ocr": True, "inpainter": True}
        readiness.update(components)
        return httpx.Response(200, json={"device": device, "components": readiness})

    @staticmethod
    def json_translation():
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"blocks": [], "original_texts": [], "translated_texts": []},
        )

    def test_offline_backend_reports_recovery_command_without_traceback(self):
        stderr = io.StringIO()

        with (
            patch.object(
                sys,
                "argv",
                ["backend-smoke.py", "--auth-token", "test-token", "--wait-timeout", "0"],
            ),
            patch.object(backend_smoke.httpx, "get", side_effect=httpx.ConnectError("offline")),
            redirect_stderr(stderr),
        ):
            result = backend_smoke.main()

        self.assertEqual(result, 1)
        self.assertIn("Backend did not become ready at http://127.0.0.1:8001/health", stderr.getvalue())
        self.assertIn("backend/start.ps1 -Runtime gpu", stderr.getvalue())
        self.assertNotIn("test-token", stderr.getvalue())

    def test_missing_auth_token_is_actionable_without_network_request(self):
        stderr = io.StringIO()
        with (
            patch.object(sys, "argv", ["backend-smoke.py"]),
            patch.dict("os.environ", {"BACKEND_AUTH_TOKEN": ""}, clear=False),
            patch.object(backend_smoke, "dotenv_values", return_value={}),
            patch.object(backend_smoke.httpx, "get") as get,
            patch.object(backend_smoke.httpx, "post") as post,
            redirect_stderr(stderr),
        ):
            result = backend_smoke.main()

        self.assertEqual(result, 2)
        get.assert_not_called()
        post.assert_not_called()
        self.assertIn("backend/.env", stderr.getvalue())

    def test_auth_token_can_come_from_environment_and_is_sent_as_header(self):
        with (
            patch.object(sys, "argv", ["backend-smoke.py", "--no-render"]),
            patch.dict("os.environ", {"BACKEND_AUTH_TOKEN": "env-token"}, clear=False),
            patch.object(backend_smoke.httpx, "get", return_value=self.ready_health()) as get,
            patch.object(
                backend_smoke.httpx,
                "post",
                return_value=self.json_translation(),
            ) as post,
        ):
            result = backend_smoke.main()

        self.assertEqual(result, 0)
        self.assertEqual(get.call_args.kwargs["headers"], {backend_smoke.AUTH_HEADER: "env-token"})
        self.assertEqual(post.call_args.kwargs["headers"], {"X-Manga-Translator-Token": "env-token"})

    def test_auth_token_falls_back_to_backend_dotenv_without_printing_secret(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(sys, "argv", ["backend-smoke.py", "--no-render"]),
            patch.dict("os.environ", {"BACKEND_AUTH_TOKEN": ""}, clear=False),
            patch.object(backend_smoke, "dotenv_values", return_value={"BACKEND_AUTH_TOKEN": "file-secret"}),
            patch.object(backend_smoke.httpx, "get", return_value=self.ready_health()),
            patch.object(backend_smoke.httpx, "post", return_value=self.json_translation()) as post,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = backend_smoke.main()

        self.assertEqual(result, 0)
        self.assertEqual(post.call_args.kwargs["headers"], {backend_smoke.AUTH_HEADER: "file-secret"})
        self.assertNotIn("file-secret", stdout.getvalue())
        self.assertNotIn("file-secret", stderr.getvalue())

    def test_expected_device_mismatch_stops_before_translation(self):
        stderr = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                ["backend-smoke.py", "--auth-token", "test-token", "--expect-device", "cuda"],
            ),
            patch.object(backend_smoke.httpx, "get", return_value=self.ready_health(device="cpu")),
            patch.object(backend_smoke.httpx, "post") as post,
            redirect_stderr(stderr),
        ):
            result = backend_smoke.main()

        self.assertEqual(result, 1)
        post.assert_not_called()
        self.assertIn("expected cuda, got cpu", stderr.getvalue())

    def test_health_waits_for_required_components_before_translation(self):
        not_ready = self.ready_health(ocr=False)
        with (
            patch.object(sys, "argv", ["backend-smoke.py", "--auth-token", "test-token", "--no-render"]),
            patch.object(backend_smoke.httpx, "get", side_effect=[not_ready, self.ready_health()]) as get,
            patch.object(backend_smoke.httpx, "post", return_value=self.json_translation()) as post,
            patch.object(backend_smoke.time, "sleep"),
        ):
            result = backend_smoke.main()

        self.assertEqual(result, 0)
        self.assertEqual(get.call_count, 2)
        post.assert_called_once()

    def test_animetext_smoke_does_not_wait_for_rtdetr_and_sends_selection(self):
        with (
            patch.object(
                sys,
                "argv",
                [
                    "backend-smoke.py",
                    "--auth-token",
                    "test-token",
                    "--detector",
                    "animetext-yolo",
                    "--no-render",
                ],
            ),
            patch.object(
                backend_smoke.httpx,
                "get",
                return_value=self.ready_health(detector=False),
            ),
            patch.object(
                backend_smoke.httpx,
                "post",
                return_value=self.json_translation(),
            ) as post,
        ):
            result = backend_smoke.main()

        self.assertEqual(result, 0)
        self.assertEqual(post.call_args.kwargs["data"]["detector"], "animetext-yolo")

    def test_requested_inpainter_must_be_ready(self):
        stderr = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                [
                    "backend-smoke.py",
                    "--auth-token",
                    "test-token",
                    "--inpainter",
                    "lama_large",
                    "--wait-timeout",
                    "0",
                ],
            ),
            patch.object(backend_smoke.httpx, "get", return_value=self.ready_health(inpainter=False)),
            patch.object(backend_smoke.httpx, "post") as post,
            redirect_stderr(stderr),
        ):
            result = backend_smoke.main()

        self.assertEqual(result, 1)
        post.assert_not_called()
        self.assertIn("components not ready: inpainter", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
