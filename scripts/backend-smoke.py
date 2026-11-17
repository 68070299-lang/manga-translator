"""Smoke-test the local backend /translate endpoint.

This script is intentionally model-agnostic by default: it uses translator=none
so a running backend can be checked without external API keys.
"""

import argparse
import json
import os
import sys
import time
from io import BytesIO
from pathlib import Path

import httpx
from dotenv import dotenv_values
from PIL import Image, ImageChops, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ENV_FILE = REPO_ROOT / "backend" / ".env"
AUTH_HEADER = "X-Manga-Translator-Token"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test the manga translation backend.")
    parser.add_argument("--image", type=Path, help="Input image path. Defaults to a generated smoke PNG.")
    parser.add_argument("--backend-url", default="http://127.0.0.1:8001", help="Backend base URL.")
    parser.add_argument(
        "--auth-token",
        default=None,
        help="Backend authentication token. Defaults to BACKEND_AUTH_TOKEN, then backend/.env.",
    )
    parser.add_argument("--target-lang", default="English", help="Target translation language.")
    parser.add_argument(
        "--detector",
        default="rtdetr",
        help="Detector: rtdetr or animetext-yolo.",
    )
    parser.add_argument("--translator", default="none", help="Backend translator: none, gemini, openai, openrouter, local.")
    parser.add_argument("--ocr", default="manga-ocr", help="OCR engine: manga-ocr, paddle-ocr, google-ocr.")
    parser.add_argument("--inpainter", default="none", help="Inpainter: flux2_klein_lanpaint (backend default), lama_large, flux2_klein_q4, none (smoke default).")
    parser.add_argument("--render", action=argparse.BooleanOptionalAction, default=True, help="Request rendered image output.")
    parser.add_argument("--timeout", type=float, default=90.0, help="Request timeout in seconds.")
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=600.0,
        help="Seconds to wait for the backend to finish loading models (a cold first start may download them).",
    )
    parser.add_argument(
        "--expect-device",
        choices=("cpu", "cuda", "directml"),
        help="Require the backend health response to report this device.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("test-results/backend-smoke/translated.png"),
        help="Output path for rendered image responses.",
    )
    return parser.parse_args()


def make_default_image_bytes() -> bytes:
    buffer = BytesIO()
    image = Image.new("RGB", (512, 256), (245, 245, 245))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((48, 42, 464, 214), radius=28, fill="white", outline="black", width=5)
    draw.text((170, 108), "TEST 123", fill="black", stroke_width=1)
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def resolve_auth_token(cli_token: str | None) -> str:
    if cli_token and cli_token.strip():
        return cli_token.strip()
    env_token = os.environ.get("BACKEND_AUTH_TOKEN", "").strip()
    if env_token:
        return env_token
    file_token = dotenv_values(BACKEND_ENV_FILE).get("BACKEND_AUTH_TOKEN")
    return str(file_token or "").strip()


def required_components(args: argparse.Namespace) -> list[str]:
    normalized_detector = args.detector.strip().lower().replace("_", "-")
    components = [] if normalized_detector in {
        "animetext",
        "animetext-yolo",
        "anime-text-yolo",
    } else ["detector"]
    normalized_ocr = args.ocr.strip().lower()
    components.append("paddle_ocr" if normalized_ocr in {"paddle-ocr", "paddleocr", "paddle-vl", "paddlevl"} else "ocr")
    if args.inpainter.strip().lower() not in {"", "none"}:
        components.append("inpainter")
    return components


def report_wait_timeout(args: argparse.Namespace, health_url: str, last_problem: str) -> bool:
    print(
        f"Backend did not become ready at {health_url} within {args.wait_timeout:.1f}s: {last_problem}. "
        "Start it with `powershell -File backend/start.ps1 -Runtime gpu` after configuring BACKEND_AUTH_TOKEN.",
        file=sys.stderr,
    )
    return False


def wait_for_backend(args: argparse.Namespace, headers: dict[str, str]) -> bool:
    health_url = f"{args.backend_url.rstrip('/')}/health"
    deadline = time.monotonic() + max(0.0, args.wait_timeout)
    last_problem = "backend did not respond"

    while True:
        try:
            response = httpx.get(health_url, headers=headers, timeout=min(max(args.timeout, 0.1), 10.0))
        except httpx.RequestError as error:
            last_problem = f"cannot reach backend ({error})"
        else:
            if response.status_code in {401, 403}:
                print("Backend health authentication failed. Check BACKEND_AUTH_TOKEN or backend/.env.", file=sys.stderr)
                return False
            if response.status_code >= 400:
                last_problem = f"health returned HTTP {response.status_code}"
            else:
                try:
                    payload = response.json()
                except ValueError:
                    print("Backend health returned invalid JSON.", file=sys.stderr)
                    return False
                if not isinstance(payload, dict):
                    print("Backend health response must be an object.", file=sys.stderr)
                    return False

                device = str(payload.get("device", "")).lower()
                if args.expect_device and device != args.expect_device:
                    print(
                        f"Backend device mismatch: expected {args.expect_device}, got {device or 'unknown'}.",
                        file=sys.stderr,
                    )
                    return False

                if payload.get("status") == "loading":
                    # The port is open while models download and load in the background.
                    last_problem = "backend is still loading models"
                else:
                    components = payload.get("components")
                    missing = [
                        component
                        for component in required_components(args)
                        if not isinstance(components, dict) or components.get(component) is not True
                    ]
                    if not missing:
                        print(f"Health: ready, device: {device or 'unknown'}")
                        return True
                    last_problem = f"components not ready: {', '.join(missing)}"

        if time.monotonic() >= deadline:
            return report_wait_timeout(args, health_url, last_problem)
        time.sleep(1.0)


def main() -> int:
    args = parse_args()
    auth_token = resolve_auth_token(args.auth_token)
    if not auth_token:
        print(
            "Backend authentication token is required; set BACKEND_AUTH_TOKEN or backend/.env, or pass --auth-token.",
            file=sys.stderr,
        )
        return 2
    if args.image and not args.image.exists():
        print(f"Input image not found: {args.image}", file=sys.stderr)
        return 2

    headers = {AUTH_HEADER: auth_token}
    if not wait_for_backend(args, headers):
        return 1

    image_bytes = args.image.read_bytes() if args.image else make_default_image_bytes()
    image_name = args.image.name if args.image else "smoke.png"
    data = {
        "target_lang": args.target_lang,
        "detector": args.detector,
        "translator": args.translator,
        "ocr": args.ocr,
        "inpainter": args.inpainter,
        "render": str(args.render).lower(),
    }

    started = time.time()
    endpoint = f"{args.backend_url.rstrip('/')}/translate"
    try:
        response = httpx.post(
            endpoint,
            files={"image": (image_name, BytesIO(image_bytes), "image/png")},
            data=data,
            headers=headers,
            timeout=args.timeout,
        )
    except httpx.RequestError as error:
        print(
            f"Cannot reach backend at {endpoint}. Start it with "
            "`powershell -File backend/start.ps1 -Runtime gpu` after configuring BACKEND_AUTH_TOKEN. "
            f"Details: {error}",
            file=sys.stderr,
        )
        return 1
    elapsed = time.time() - started

    print(f"Status: {response.status_code}, time: {elapsed:.1f}s")
    print(f"Content-Type: {response.headers.get('content-type', '')}")
    print(f"Content-Length: {len(response.content)}")

    if response.status_code >= 400:
        print(response.text[:1000], file=sys.stderr)
        return 1

    content_type = response.headers.get("content-type", "").lower()
    if args.render and not content_type.startswith("image/"):
        print(
            f"Expected rendered image response, got {content_type or 'missing Content-Type'}",
            file=sys.stderr,
        )
        print(response.text[:1000], file=sys.stderr)
        return 1

    if content_type.startswith("image/"):
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(response.content)
        try:
            image = Image.open(BytesIO(response.content))
            image.load()
        except Exception as error:
            print(f"Backend returned an invalid image: {error}", file=sys.stderr)
            return 1
        print(f"Image size: {image.size}, mode: {image.mode}")
        print(f"Saved to: {args.output}")

        if args.image is None:
            source = Image.open(BytesIO(image_bytes)).convert("RGB")
            rendered = image.convert("RGB")
            if source.size != rendered.size:
                print("Rendered smoke image size changed unexpectedly", file=sys.stderr)
                return 1
            if ImageChops.difference(source, rendered).getbbox() is None:
                print("Smoke image was unchanged; pipeline stages were not exercised", file=sys.stderr)
                return 1
    else:
        try:
            payload = response.json()
        except ValueError as error:
            print(f"Backend returned invalid JSON: {error}", file=sys.stderr)
            return 1
        if not isinstance(payload, dict):
            print(
                "Backend JSON response must be an object",
                file=sys.stderr,
            )
            return 1
        required_arrays = ("blocks", "original_texts", "translated_texts")
        if any(not isinstance(payload.get(key), list) for key in required_arrays):
            print(
                "Backend JSON response must contain blocks, original_texts, and translated_texts arrays",
                file=sys.stderr,
            )
            return 1
        block_count = len(payload["blocks"])
        if len(payload["original_texts"]) != block_count or len(payload["translated_texts"]) != block_count:
            print("Backend JSON response arrays have inconsistent lengths", file=sys.stderr)
            return 1
        if any(not isinstance(block, dict) or "bbox" not in block for block in payload["blocks"]):
            print("Backend JSON blocks must be objects with bbox fields", file=sys.stderr)
            return 1
        print(json.dumps(payload, ensure_ascii=True)[:1000])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
