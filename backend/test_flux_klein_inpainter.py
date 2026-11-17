import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import flux_klein_inpainter
from models.flux_klein_inpainter import (
    GGUF_NAME,
    LLM_NAME,
    VAE_NAME,
    FluxKleinInpainter,
)
from test_support import writable_temp_directory


def _ready_inpainter():
    inpainter = FluxKleinInpainter()
    inpainter._initialized = True
    return inpainter


def _image_and_mask():
    image = np.full((16, 16, 3), 50, dtype=np.uint8)
    mask = np.zeros((16, 16), dtype=np.uint8)
    mask[4:12, 4:12] = 255
    return image, mask


def _output_option(cmd):
    return cmd[cmd.index("-o") + 1]


class FluxKleinInitializeTests(unittest.TestCase):
    def test_missing_cli_names_env_var_without_absolute_paths(self):
        with patch.dict(os.environ, {"SD_CPP_CLI": r"C:\Users\nobody\sd-cli.exe"}):
            with self.assertRaises(RuntimeError) as raised:
                asyncio.run(FluxKleinInpainter().initialize())

        message = str(raised.exception)
        self.assertIn("SD_CPP_CLI", message)
        self.assertNotIn("C:\\Users", message)
        self.assertNotIn("nobody", message)

    def test_missing_llm_names_models_dir_env_var(self):
        with writable_temp_directory() as td:
            cli = os.path.join(td, "sd-cli.exe")
            Path(cli).write_bytes(b"x")
            with patch.dict(
                os.environ, {"SD_CPP_CLI": cli, "SD_CPP_MODELS_DIR": td}
            ), patch(
                "models.flux_klein_inpainter.os.path.exists",
                side_effect=lambda path: path == cli
                or path.endswith(GGUF_NAME),
            ):
                with self.assertRaises(RuntimeError) as raised:
                    asyncio.run(FluxKleinInpainter().initialize())

        message = str(raised.exception)
        self.assertIn("SD_CPP_MODELS_DIR", message)
        self.assertNotIn(td, message)

    def test_initialize_succeeds_when_every_file_exists(self):
        with writable_temp_directory() as td:
            cli = os.path.join(td, "sd-cli.exe")
            Path(cli).write_bytes(b"x")
            with patch.dict(
                os.environ, {"SD_CPP_CLI": cli, "SD_CPP_MODELS_DIR": td}
            ), patch(
                "models.flux_klein_inpainter.os.path.exists", return_value=True
            ), patch(
                "models.flux_klein_inpainter.os.path.getsize", return_value=1
            ):
                inpainter = FluxKleinInpainter()
                asyncio.run(inpainter.initialize())

        self.assertTrue(inpainter._initialized)


class FluxKleinSubprocessTests(unittest.TestCase):
    def _run(self, fake_run, env=None):
        image, mask = _image_and_mask()
        inpainter = _ready_inpainter()
        with patch.dict(os.environ, env or {}), patch(
            "models.flux_klein_inpainter.subprocess.run", side_effect=fake_run
        ) as run:
            result = asyncio.run(inpainter.inpaint(image, mask))
        return image, mask, result, run

    def test_command_and_timeout_come_from_env(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["kwargs"] = kwargs
            Image.fromarray(np.full((16, 16, 3), 200, dtype=np.uint8)).save(
                _output_option(cmd)
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        env = {
            "SD_CPP_CLI": "custom-sd-cli",
            "SD_CPP_MODELS_DIR": "models-dir",
            "SD_CPP_TIMEOUT_SECONDS": "45",
        }
        image, mask, result, _ = self._run(fake_run, env)

        cmd = seen["cmd"]
        self.assertEqual(cmd[0], "custom-sd-cli")
        self.assertEqual(cmd[cmd.index("--llm") + 1], os.path.join("models-dir", LLM_NAME))
        self.assertEqual(cmd[cmd.index("--vae") + 1], os.path.join("models-dir", VAE_NAME))
        self.assertTrue(cmd[cmd.index("--diffusion-model") + 1].endswith(GGUF_NAME))
        self.assertEqual(seen["kwargs"]["timeout"], 45.0)
        np.testing.assert_array_equal(result[mask == 0], image[mask == 0])
        np.testing.assert_array_equal(result[mask > 0], 200)

    def test_defaults_use_models_cache_and_120_second_timeout(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            seen["kwargs"] = kwargs
            Image.fromarray(np.full((16, 16, 3), 9, dtype=np.uint8)).save(
                _output_option(cmd)
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.dict(os.environ):
            for name in ("SD_CPP_CLI", "SD_CPP_MODELS_DIR", "SD_CPP_TIMEOUT_SECONDS"):
                os.environ.pop(name, None)
            self._run(fake_run)

        self.assertEqual(
            seen["cmd"][0],
            os.path.join(flux_klein_inpainter.FLUX_DIR, "sd-cli.exe"),
        )
        self.assertEqual(seen["kwargs"]["timeout"], 120.0)

    def test_non_zero_return_raises_with_stderr_tail(self):
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(returncode=3, stdout="", stderr="boom")

        with self.assertRaisesRegex(RuntimeError, "sd-cli Q4 failed 3: boom"):
            self._run(fake_run)

    def test_missing_output_raises(self):
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(returncode=0, stdout="nothing", stderr="")

        with self.assertRaisesRegex(RuntimeError, "sd-cli no output"):
            self._run(fake_run)

    def test_suffixed_output_file_is_used(self):
        def fake_run(cmd, **kwargs):
            suffixed = os.path.join(os.path.dirname(_output_option(cmd)), "out_0.png")
            Image.fromarray(np.full((16, 16, 3), 77, dtype=np.uint8)).save(suffixed)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        _, mask, result, _ = self._run(fake_run)

        np.testing.assert_array_equal(result[mask > 0], 77)

    def test_timeout_names_env_var(self):
        def fake_run(cmd, **kwargs):
            raise flux_klein_inpainter.subprocess.TimeoutExpired(cmd, 1)

        with self.assertRaisesRegex(RuntimeError, "SD_CPP_TIMEOUT_SECONDS"):
            self._run(fake_run)

    def test_invalid_timeout_falls_back_to_default(self):
        with patch.dict(os.environ, {"SD_CPP_TIMEOUT_SECONDS": "soon"}):
            self.assertEqual(flux_klein_inpainter._sd_timeout_seconds(), 120.0)


if __name__ == "__main__":
    unittest.main()
