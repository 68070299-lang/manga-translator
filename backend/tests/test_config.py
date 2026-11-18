"""Settings parsing, bootstrap helpers, the module entry point and JSON byte format."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from manga_backend import __main__ as entrypoint
from manga_backend.api.app import create_app
from manga_backend.config import Settings, load_settings


def clean_env(**extra):
    """Process environment without any backend setting, plus extras."""
    names = {
        "BACKEND_AUTH_TOKEN", "BACKEND_ALLOWED_ORIGINS", "BACKEND_MAX_CONCURRENT",
        "BACKEND_QUEUE_TIMEOUT_SECONDS", "MANGA_TRANSLATOR_TESTING", "USE_GPU",
        "BACKEND_HOST", "BACKEND_PORT", "LOCAL_LLM_BASE_URL", "GEMINI_API_KEY",
        "OPENAI_API_KEY", "OPENROUTER_API_KEY",
    }
    env = {key: value for key, value in os.environ.items() if key not in names}
    env.update(extra)
    return patch.dict(os.environ, env, clear=True)


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        with clean_env():
            settings = Settings(_env_file=None)

        self.assertEqual(settings.auth_token, "")
        self.assertEqual(settings.allowed_origins, frozenset())
        self.assertEqual((settings.max_concurrent, settings.queue_timeout_seconds), (2, 600.0))
        self.assertFalse(settings.testing)
        self.assertFalse(settings.use_gpu)
        self.assertEqual((settings.host, settings.port), ("127.0.0.1", 8001))
        self.assertEqual(settings.local_llm_base_url, "http://localhost:8000/v1")

    def test_environment_values_are_parsed_like_the_old_module_globals(self):
        with clean_env(
            BACKEND_AUTH_TOKEN="  secret  ",
            BACKEND_ALLOWED_ORIGINS=" http://localhost:5173 , ,http://127.0.0.1:3000",
            MANGA_TRANSLATOR_TESTING="1",
            USE_GPU="True",
            BACKEND_HOST="0.0.0.0",
            BACKEND_PORT="9000",
            LOCAL_LLM_BASE_URL="http://localhost:9/v1",
            GEMINI_API_KEY=" g ",
        ):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.auth_token, "secret")
        self.assertEqual(
            settings.allowed_origins, {"http://localhost:5173", "http://127.0.0.1:3000"}
        )
        self.assertTrue(settings.testing)
        self.assertTrue(settings.use_gpu)
        self.assertEqual((settings.host, settings.port), ("0.0.0.0", 9000))
        self.assertEqual(settings.local_llm_base_url, "http://localhost:9/v1")
        self.assertEqual(settings.translator_api_key("gemini"), "g")
        self.assertEqual(settings.translator_api_key("openai"), "")
        self.assertEqual(settings.translator_api_key("local"), "")

    def test_only_exact_flag_values_enable_testing_and_gpu(self):
        for raw in ("0", "", "true", "yes"):
            with self.subTest(testing=raw), clean_env(MANGA_TRANSLATOR_TESTING=raw):
                self.assertFalse(Settings(_env_file=None).testing)
        for raw in ("false", "1", "", "TRUE "):
            with self.subTest(gpu=raw), clean_env(USE_GPU=raw):
                self.assertFalse(Settings(_env_file=None).use_gpu)
        with clean_env(USE_GPU="TRUE"):
            self.assertTrue(Settings(_env_file=None).use_gpu)

    def test_init_arguments_win_over_the_environment(self):
        with clean_env(BACKEND_AUTH_TOKEN="from-env"):
            self.assertEqual(Settings(auth_token="explicit").auth_token, "explicit")

    def test_generic_and_field_names_in_the_environment_are_ignored(self):
        # Only the exact variable names configure the backend. TESTING=1 from an
        # unrelated tool must not disable auth, and HOST/PORT must not move the bind.
        generic = {
            "TESTING": "1", "testing": "1", "AUTH_TOKEN": "x", "auth_token": "x",
            "ALLOWED_ORIGINS": "http://evil.example", "allowed_origins": "http://evil.example",
            "MAX_CONCURRENT": "9", "QUEUE_TIMEOUT_SECONDS": "5", "HOST": "0.0.0.0",
            "PORT": "3000", "GPU": "true", "GEMINI_KEY": "k", "gemini_api_key_extra": "k",
        }
        with clean_env(**generic):
            settings = Settings(_env_file=None)

        self.assertFalse(settings.testing)
        self.assertEqual(settings.auth_token, "")
        self.assertEqual(settings.allowed_origins, frozenset())
        self.assertEqual((settings.max_concurrent, settings.queue_timeout_seconds), (2, 600.0))
        self.assertEqual((settings.host, settings.port), ("127.0.0.1", 8001))

    def test_generic_names_in_the_env_file_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory, clean_env():
            env_file = Path(directory) / ".env"
            env_file.write_text("TESTING=1\nAUTH_TOKEN=x\nHOST=0.0.0.0\n", encoding="utf-8")
            settings = Settings(_env_file=env_file)

        self.assertFalse(settings.testing)
        self.assertEqual(settings.auth_token, "")
        self.assertEqual(settings.host, "127.0.0.1")

    def test_testing_env_without_the_full_name_keeps_auth_on(self):
        class Pipeline:
            is_ready = True
            device = "cpu"
            model_status: dict[str, object] = {}

        with clean_env(TESTING="1", BACKEND_AUTH_TOKEN="secret-token"):
            client = TestClient(create_app(Settings(_env_file=None), pipeline=Pipeline()))
            response = client.get("/health")

        self.assertEqual(response.status_code, 401)

    def test_field_names_are_accepted_as_init_arguments(self):
        with clean_env():
            settings = Settings(_env_file=None, testing=True, host="::1", port=1, auth_token=" t ")

        self.assertTrue(settings.testing)
        self.assertEqual((settings.host, settings.port, settings.auth_token), ("::1", 1, "t"))

    def test_settings_are_immutable(self):
        with self.assertRaises(Exception):
            Settings().max_concurrent = 9


class EnvFileTests(unittest.TestCase):
    def write_env(self, directory):
        path = Path(directory) / ".env"
        path.write_text("BACKEND_AUTH_TOKEN=file-token\nGEMINI_MODEL=ignored\n", encoding="utf-8")
        return path

    def test_backend_env_file_is_read_outside_testing_mode(self):
        with tempfile.TemporaryDirectory() as directory, clean_env():
            env_file = self.write_env(directory)
            with patch.dict(Settings.model_config, {"env_file": env_file}):
                settings = load_settings()

        self.assertEqual(settings.auth_token, "file-token")

    def test_process_environment_beats_the_env_file(self):
        with tempfile.TemporaryDirectory() as directory, clean_env(BACKEND_AUTH_TOKEN="env-token"):
            env_file = self.write_env(directory)
            with patch.dict(Settings.model_config, {"env_file": env_file}):
                settings = load_settings()

        self.assertEqual(settings.auth_token, "env-token")

    def test_testing_mode_never_reads_the_env_file(self):
        with tempfile.TemporaryDirectory() as directory, clean_env(MANGA_TRANSLATOR_TESTING="1"):
            env_file = self.write_env(directory)
            with patch.dict(Settings.model_config, {"env_file": env_file}):
                settings = load_settings()

        self.assertEqual(settings.auth_token, "")
        self.assertTrue(settings.testing)


class EntryPointTests(unittest.TestCase):
    def test_defaults_come_from_settings(self):
        with clean_env(MANGA_TRANSLATOR_TESTING="1", BACKEND_PORT="9100"):
            args = entrypoint.parse_args([])

        self.assertEqual((args.host, args.port), ("127.0.0.1", 9100))

    def test_generic_host_and_port_variables_do_not_move_the_bind(self):
        with clean_env(MANGA_TRANSLATOR_TESTING="1", HOST="0.0.0.0", PORT="3000"):
            args = entrypoint.parse_args([])

        self.assertEqual((args.host, args.port), ("127.0.0.1", 8001))

    def test_cli_overrides_settings(self):
        with clean_env(MANGA_TRANSLATOR_TESTING="1"):
            args = entrypoint.parse_args(["--host", "localhost", "--port", "8123"])

        self.assertEqual((args.host, args.port), ("localhost", 8123))

    def test_main_runs_uvicorn_with_an_app_factory(self):
        with clean_env(MANGA_TRANSLATOR_TESTING="1"), patch("uvicorn.run") as run:
            entrypoint.main(["--port", "8123"])

        run.assert_called_once_with(
            "manga_backend.api.app:create_app", factory=True, host="127.0.0.1", port=8123
        )


class JsonFormatTests(unittest.TestCase):
    """The extension parses these bodies; the byte format is compact UTF-8 JSON."""

    def test_root_and_health_bodies_are_compact_unescaped_json(self):
        class Pipeline:
            is_ready = True
            device = "cpu"
            model_status = {"detector": {"status": "loaded", "name": "日本語"}}

        client = TestClient(create_app(Settings(testing=True), pipeline=Pipeline()))

        root = client.get("/")
        self.assertEqual(
            root.content, b'{"status":"ok","message":"Manga Translation API is running"}'
        )
        health = client.get("/health")
        self.assertEqual(health.headers["content-type"], "application/json")
        self.assertEqual(
            list(health.json()),
            [
                "status", "models_loaded", "device", "models", "components",
                "uptime_seconds", "active_translate_requests", "config", "last_error",
            ],
        )
        self.assertIn("日本語".encode("utf-8"), health.content)
        self.assertEqual(
            health.content,
            json.dumps(health.json(), ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )
        self.assertIsNone(health.json()["last_error"])
        self.assertEqual(
            list(health.json()["config"]),
            ["allowed_browser_origins_count", "max_upload_size", "max_image_pixels", "use_gpu"],
        )


if __name__ == "__main__":
    unittest.main()
