"""Focused process-boundary tests for the PowerShell backend launcher."""

import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

START_SCRIPT = Path(__file__).resolve().parent / "start.ps1"
POWERSHELL = shutil.which("powershell")


@unittest.skipUnless(
    os.name == "nt" and POWERSHELL, "PowerShell launcher tests require Windows"
)
class BackendStartScriptTests(unittest.TestCase):
    def make_launcher_fixture(
        self,
        root: Path,
        with_token: bool = True,
        provider_probe_exit: int = 0,
    ) -> tuple[Path, Path]:
        backend = root / "backend"
        backend.mkdir()
        script = backend / "start.ps1"
        shutil.copyfile(START_SCRIPT, script)
        if with_token:
            (backend / ".env").write_text(
                "BACKEND_AUTH_TOKEN=test-launch-token\n", encoding="ascii"
            )

        capture = root / "uv-capture.txt"
        (root / "uv.cmd").write_text(
            "@echo off\n"
            '>> "%CAPTURE_FILE%" (\n'
            "  echo COMMAND=uv %*\n"
            "  echo CWD=%CD%\n"
            "  echo USE_GPU=%USE_GPU%\n"
            "  echo UV_PROJECT_ENVIRONMENT=%UV_PROJECT_ENVIRONMENT%\n"
            "  echo COMFY_LANPAINT_PROFILE=%COMFY_LANPAINT_PROFILE%\n"
            "  echo ARGS=%*\n"
            ")\n"
            f'if "%1 %2 %3" == "run --no-sync python" exit /b {provider_probe_exit}\n'
            "exit /b 0\n",
            encoding="ascii",
        )
        return script, capture

    @staticmethod
    def free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def launcher_env(self, root: Path, capture: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = f"{root}{os.pathsep}{env.get('PATH', '')}"
        env["CAPTURE_FILE"] = str(capture)
        env.pop("BACKEND_AUTH_TOKEN", None)
        # The suite itself runs with this set; the launcher refuses to start under it.
        env.pop("MANGA_TRANSLATOR_TESTING", None)
        return env

    def test_launcher_uses_isolated_gpu_env_and_restores_caller_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script, capture = self.make_launcher_fixture(root)
            initial = root / "initial"
            initial.mkdir()
            harness = root / "harness.ps1"
            harness.write_text(
                "$env:USE_GPU = 'before-gpu'\n"
                "$env:UV_PROJECT_ENVIRONMENT = 'before-project'\n"
                "$env:COMFY_LANPAINT_PROFILE = 'before-profile'\n"
                f"Set-Location -LiteralPath '{initial}'\n"
                f"& '{script}' -Runtime gpu -LanPaintProfile fast -Port {self.free_port()}\n"
                'Write-Output "RESTORED_USE_GPU=$env:USE_GPU"\n'
                'Write-Output "RESTORED_PROJECT=$env:UV_PROJECT_ENVIRONMENT"\n'
                'Write-Output "RESTORED_PROFILE=$env:COMFY_LANPAINT_PROFILE"\n'
                'Write-Output "RESTORED_CWD=$((Get-Location).Path)"\n',
                encoding="ascii",
            )

            result = subprocess.run(
                [
                    POWERSHELL,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(harness),
                ],
                cwd=root,
                env=self.launcher_env(root, capture),
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = capture.read_text(encoding="ascii")
            self.assertIn(f"CWD={script.parent}", output)
            self.assertIn("USE_GPU=true", output)
            self.assertIn(f"UV_PROJECT_ENVIRONMENT={script.parent}\\.venv-gpu", output)
            self.assertIn("COMFY_LANPAINT_PROFILE=fast", output)
            self.assertIn(
                "ARGS=sync --locked --no-default-groups --group gpu --python 3.12",
                output,
            )
            self.assertIn("ARGS=run --no-sync python -c", output)
            self.assertIn(
                "ARGS=run --no-sync uvicorn main:app --host 127.0.0.1",
                output,
            )
            self.assertIn("RESTORED_USE_GPU=before-gpu", result.stdout)
            self.assertIn("RESTORED_PROJECT=before-project", result.stdout)
            self.assertIn("RESTORED_PROFILE=before-profile", result.stdout)
            self.assertIn(f"RESTORED_CWD={initial}", result.stdout)
            self.assertNotIn("test-launch-token", result.stdout + result.stderr)

    def test_gpu_runtime_fails_closed_without_cuda_provider(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script, capture = self.make_launcher_fixture(
                root, provider_probe_exit=4
            )
            result = subprocess.run(
                [
                    POWERSHELL,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-Runtime",
                    "gpu",
                    "-Port",
                    str(self.free_port()),
                ],
                cwd=root,
                env=self.launcher_env(root, capture),
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 4)
            self.assertIn(
                "CUDAExecutionProvider is unavailable", result.stderr
            )
            self.assertNotIn("uvicorn main:app", capture.read_text(encoding="ascii"))

    def test_missing_auth_fails_before_uv_without_printing_a_secret(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script, capture = self.make_launcher_fixture(root, with_token=False)
            result = subprocess.run(
                [
                    POWERSHELL,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-Runtime",
                    "gpu",
                    "-Port",
                    str(self.free_port()),
                ],
                cwd=root,
                env=self.launcher_env(root, capture),
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2)
            self.assertFalse(capture.exists())
            self.assertIn("Backend authentication is not configured", result.stderr)
            self.assertNotIn("test-launch-token", result.stdout + result.stderr)

    def test_testing_mode_variable_is_rejected_before_uv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script, capture = self.make_launcher_fixture(root)
            env = self.launcher_env(root, capture)
            env["MANGA_TRANSLATOR_TESTING"] = "1"
            result = subprocess.run(
                [
                    POWERSHELL,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-Runtime",
                    "gpu",
                    "-Port",
                    str(self.free_port()),
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 5)
            self.assertFalse(capture.exists())
            self.assertIn("MANGA_TRANSLATOR_TESTING", result.stderr)
            self.assertNotIn("test-launch-token", result.stdout + result.stderr)

    def test_occupied_port_fails_before_uv_and_reports_owner(self):
        with tempfile.TemporaryDirectory() as temp_dir, socket.socket() as listener:
            root = Path(temp_dir)
            script, capture = self.make_launcher_fixture(root)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = int(listener.getsockname()[1])
            result = subprocess.run(
                [
                    POWERSHELL,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-Port",
                    str(port),
                ],
                cwd=root,
                env=self.launcher_env(root, capture),
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 3)
            self.assertFalse(capture.exists())
            self.assertIn(f"Port {port} is already in use by process", result.stderr)


if __name__ == "__main__":
    unittest.main()
