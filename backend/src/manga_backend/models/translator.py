"""Translation using LLM APIs.

Supports multiple translation backends:
- Gemini
- OpenAI / OpenRouter
- Local (llama.cpp, vLLM, or any OpenAI-compatible local server)
- None (returns original text)

Vision-capable providers also receive the page as a downscaled image with one
small number drawn at each translation unit (Lippmann et al., COLING 2025:
page plus image with reading-order numbers was their best Japanese-to-English
setup). The image is built by the caller, is only ever sent inside the same
request the text already goes to the provider the user chose, and is never
written to disk.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Sequence
from types import ModuleType
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import urlparse

if TYPE_CHECKING:
    import httpx
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# v1beta carries the thinking settings and the Gemma models; v1 does not.
GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
# The free tier answers a share of requests with an immediate 5xx or never
# answers at all, and the same request then succeeds, so a failed attempt is
# repeated a few times.
GEMINI_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
GEMINI_RETRY_DELAYS_SECONDS = (1.0, 2.0, 3.0, 5.0, 8.0)
# Longest silence accepted from a streamed reply before the attempt is abandoned.
GEMINI_STALL_SECONDS = 12.0
GEMINI_THINKING_STALL_SECONDS = 60.0

# Which provider routes accept an image part. The rule is documented per route
# because sending the page to a text-only model fails the request (and would
# hand page art to a provider that cannot use it):
# - gemini/google: the whole current line-up is multimodal, so yes.
# - openai/openrouter: only the models named here are known to take an image
#   part on the OpenAI-compatible chat API; a custom model name keeps the
#   text-only prompt unless it matches.
# - local: text-only. The project's local route is an OpenAI-compatible text
#   server; LOCAL_LLM_VISION=true opts a genuinely multimodal local server in.
VISION_MODEL_MARKERS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "gpt-5",
    "o3",
    "o4",
    "gemini",
    "claude-3",
    "claude-4",
    "claude-sonnet",
    "claude-opus",
    "claude-haiku",
    "qwen-vl",
    "qwen2.5-vl",
    "qwen3-vl",
    "llava",
    "pixtral",
    "llama-3.2-vision",
    "internvl",
    "minicpm-v",
    "glm-4v",
    "deepseek-vl",
)


def gemini_thinking_level(model_name: str) -> str | None:
    """Thinking level to request, or None to leave the model's default.

    Gemma 4 reasons at length by default (about 4,000 thinking tokens and 90 s
    for five short lines); "minimal" answers the same request in under 3 s.
    GEMINI_THINKING_LEVEL overrides it for any model; "off" sends no setting.
    """
    configured = os.environ.get("GEMINI_THINKING_LEVEL")
    if configured is not None:
        level = configured.strip().lower()
        return None if level in ("", "off", "none", "default") else level
    return "minimal" if "gemma-4" in model_name.lower() else None


def effective_model_name(translator_type: str, model: str | None = None) -> str:
    """The model a route will use, resolving the same environment defaults as init."""
    route = (translator_type or "").strip().lower()
    if route in ("gemini", "google"):
        return model or os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    if route == "openrouter":
        return model or os.environ.get("OPENROUTER_MODEL", "google/gemini-2.0-flash-001")
    if route == "openai":
        return model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    if route == "local":
        return model or os.environ.get("LOCAL_LLM_MODEL", "")
    return model or ""


def supports_page_image(translator_type: str, model: str | None = None) -> bool:
    """Whether this route may receive the numbered page image.

    Gemini is multimodal by default; the OpenAI-compatible routes are gated on
    the model name (VISION_MODEL_MARKERS) and the local route on the explicit
    LOCAL_LLM_VISION=true opt-in, so a text-only provider keeps the text-only
    prompt. "none" and unknown routes never get an image.
    """
    route = (translator_type or "").strip().lower()
    if route in ("gemini", "google"):
        return True
    if route == "local":
        return os.environ.get("LOCAL_LLM_VISION", "false").strip().lower() == "true"
    if route in ("openai", "openrouter"):
        name = effective_model_name(route, model).lower()
        return any(marker in name for marker in VISION_MODEL_MARKERS)
    return False


def _is_loopback_host(hostname: str) -> bool:
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def validate_local_llm_base_url(base_url: str) -> str:
    """Only allow local LLM endpoints on loopback by default."""
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Local LLM base URL must be an http(s) URL")

    allow_remote = os.environ.get("ALLOW_REMOTE_LOCAL_LLM", "false").lower() == "true"
    if not allow_remote and not _is_loopback_host(parsed.hostname or ""):
        raise ValueError("Local LLM base URL must point to localhost or a loopback address")

    return base_url.rstrip("/")


class TranslatorError(RuntimeError):
    """Sanitized translation provider failure."""

    status_code = 502
    public_detail = "Translation provider request failed"


class TranslatorTimeoutError(TranslatorError):
    """The translation provider did not respond before the request timeout."""

    status_code = 504
    public_detail = "Translation provider request timed out"


class Translator:
    """Translation using LLM APIs."""

    def __init__(
        self,
        translator_type: str = "none",
        api_key: str | None = None,
        model: str | None = None,
        local_llm_base_url: str | None = None,
        local_llm_api_key: str | None = None,
    ) -> None:
        self.translator_type = translator_type
        self.api_key = api_key
        self.model_name = model
        self.local_llm_base_url = local_llm_base_url
        self.local_llm_api_key = local_llm_api_key
        # An AsyncOpenAI client, the google.generativeai module when that SDK is
        # installed, or "gemini_http" for the plain HTTP fallback.
        self.client: AsyncOpenAI | ModuleType | Literal["gemini_http"] | None = None
        # google.generativeai.GenerativeModel when the SDK is installed.
        self.model: Any = None

    async def initialize(self) -> None:
        """Initialize the translation client."""
        if self.translator_type == "none":
            logger.info("Translation disabled (pass-through mode)")
            return

        if self.translator_type in ["gemini", "google"]:
            await self._init_gemini()
        elif self.translator_type in ["openai", "openrouter"]:
            await self._init_openai()
        elif self.translator_type == "local":
            await self._init_local()
        else:
            raise ValueError(f"Unsupported translator type: {self.translator_type}")

    async def close(self) -> None:
        """Release the HTTP connections held by an OpenAI-compatible client."""
        close = getattr(self.client, "close", None)
        if self.translator_type in ["openai", "openrouter", "local"] and callable(close):
            try:
                await cast("Callable[[], Awaitable[None]]", close)()
            except Exception as error:
                # A finished translation must not fail because cleanup did.
                logger.warning("Translator client close failed: %s", type(error).__name__)
        self.client = None

    async def _init_gemini(self) -> None:
        """Initialize Gemini API client."""
        api_key = self.api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not provided. Set it in environment or pass via api_key parameter.")

        self.model_name = self.model_name or os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

        try:
            import google.generativeai as genai  # pyright: ignore[reportMissingImports]
            genai.configure(api_key=api_key)
            self.client = genai
            self.model = genai.GenerativeModel(self.model_name)
            logger.info("Gemini translator initialized: %s", self.model_name)
        except ImportError:
            # Fall back to HTTP API
            logger.info("Using Gemini HTTP API")
            self.client = "gemini_http"
            self.api_key = api_key

    async def _init_openai(self) -> None:
        """Initialize OpenAI/OpenRouter API client."""
        if self.translator_type == "openrouter":
            api_key = self.api_key or os.environ.get("OPENROUTER_API_KEY")
            base_url = "https://openrouter.ai/api/v1"
            self.model_name = self.model_name or os.environ.get("OPENROUTER_MODEL", "google/gemini-2.0-flash-001")
        else:
            api_key = self.api_key or os.environ.get("OPENAI_API_KEY")
            base_url = "https://api.openai.com/v1"
            self.model_name = self.model_name or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

        if not api_key:
            raise ValueError(f"API key not set for {self.translator_type}")

        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        logger.info("OpenAI-compatible translator initialized: %s", base_url)

    async def _init_local(self) -> None:
        """Initialize local LLM client (llama.cpp, vLLM, etc.)."""
        base_url = self.local_llm_base_url or os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:8000/v1")
        base_url = validate_local_llm_base_url(base_url)
        api_key = self.local_llm_api_key or self.api_key or os.environ.get("LOCAL_LLM_API_KEY", "not-needed")
        self.model_name = self.model_name or os.environ.get("LOCAL_LLM_MODEL", "")

        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        logger.info(
            "Local LLM translator initialized: %s (model: %s)",
            base_url,
            self.model_name or "server default",
        )

    async def translate(
        self,
        texts: list[str],
        target_lang: str = "English",
        *,
        budgets: Sequence[int | None] | None = None,
        page_image: bytes | None = None,
    ) -> list[str]:
        """Translate a list of texts, one per translation unit.

        Args:
            texts: Source text of each unit, in reading order.
            target_lang: Target language name or code.
            budgets: Optional per-unit character budget (a unit's bubble
                capacity at the page's lettering size). None for a unit with
                no usable room.
            page_image: Optional PNG of the page with a number drawn at each
                unit. Only sent to a route that can take an image part.

        Returns:
            List of translated texts
        """
        if not texts:
            return []

        if self.translator_type == "none":
            return texts

        image = page_image if self._accepts_page_image(page_image) else None
        prompt = self._build_prompt(texts, target_lang, budgets, has_page_image=image is not None)
        try:
            return await self._request(prompt, len(texts), image)
        except Exception as error:
            raise self._provider_error(error) from error

    async def shorten(
        self,
        texts: list[str],
        limits: Sequence[int | None],
        target_lang: str = "English",
    ) -> list[str]:
        """Ask once for shorter versions of translations that did not fit.

        This is the single re-ask after the first translation: the caller
        decides which units are too long (they cannot be set at the minimum
        readable size), and a second failure is rendered as the fitter can do
        it rather than asked about again.
        """
        if not texts:
            return []
        if self.translator_type == "none":
            return texts

        prompt = self._build_shorten_prompt(texts, limits, target_lang)
        try:
            return await self._request(prompt, len(texts), None)
        except Exception as error:
            raise self._provider_error(error) from error

    def _accepts_page_image(self, page_image: bytes | None) -> bool:
        """True when this route may receive the page image (privacy gate)."""
        return page_image is not None and supports_page_image(
            self.translator_type, self.model_name
        )

    @staticmethod
    def _provider_error(error: Exception) -> Exception:
        """Map a provider failure to the sanitised public error."""
        # Provider errors can embed upstream response bodies or credentials, so
        # only the exception class and HTTP status are logged.
        status = getattr(error, "status_code", None)
        if status is None:
            status = getattr(getattr(error, "response", None), "status_code", None)
        logger.error(
            "Translation failed: %s%s",
            type(error).__name__,
            f" (HTTP {status})" if status else "",
        )
        is_timeout = any("Timeout" in cls.__name__ for cls in type(error).__mro__)
        return (TranslatorTimeoutError if is_timeout else TranslatorError)(
            "Translation request failed"
        )

    async def _request(self, prompt: str, count: int, page_image: bytes | None) -> list[str]:
        """Send one prompt to the configured provider and parse its numbering."""
        if self.translator_type in ["gemini", "google"]:
            return await self._translate_gemini(prompt, count, page_image)
        if self.translator_type in ["local", "openai", "openrouter"]:
            return await self._translate_openai(prompt, count, page_image)
        raise ValueError(f"Unsupported translator type: {self.translator_type}")

    @staticmethod
    def _numbered_input(
        texts: Sequence[str], budgets: Sequence[int | None] | None
    ) -> str:
        """Numbered source list; a budgeted unit carries its character limit."""
        lines: list[str] = []
        for index, text in enumerate(texts):
            limit = budgets[index] if budgets is not None and index < len(budgets) else None
            prefix = f"{index + 1}. " + (f"(≤ {int(limit)} characters) " if limit else "")
            lines.append(prefix + str(text))
        return "\n".join(lines)

    def _build_prompt(
        self,
        texts: list[str],
        target_lang: str,
        budgets: Sequence[int | None] | None = None,
        *,
        has_page_image: bool = False,
    ) -> str:
        """Build the translation prompt."""
        numbered_texts = self._numbered_input(texts, budgets)
        image_note = (
            "The attached page image marks each numbered item where it appears on the page; "
            "use it for speaker, tone and scene context.\n"
            if has_page_image
            else ""
        )
        budget_note = (
            "Each item's \"≤ N characters\" is its bubble's capacity at the page's lettering "
            "size, in letters and spaces (not words); stay within it.\n"
            if any(budget for budget in (budgets or ()))
            else ""
        )

        return f"""Translate the following manga dialogue from Japanese to {target_lang}.
Keep translations concise to fit speech bubbles.
Preserve character voice and tone.
The numbered items follow the page's reading order, and one item may be a whole speech bubble.
{image_note}{budget_note}Return ONLY the translations, numbered to match input.

Input:
{numbered_texts}

Output (translated, same numbering):"""

    def _build_shorten_prompt(
        self, texts: Sequence[str], limits: Sequence[int | None], target_lang: str
    ) -> str:
        """Build the one re-ask: shorter versions that fit the stated limits."""
        numbered_texts = self._numbered_input(texts, limits)
        return f"""These manga dialogue translations are too long for their speech bubbles.
Rewrite each one shorter so that it fits in at most the stated number of characters ({target_lang} letters, spaces and punctuation).
Keep the meaning, the speaker's voice and the tone. Do not add, drop or reorder items.
Return ONLY the rewritten translations, numbered to match the input.

Input:
{numbered_texts}

Output (shortened, same numbering):"""

    async def _translate_gemini(
        self, prompt: str, count: int, page_image: bytes | None = None
    ) -> list[str]:
        """Translate using Gemini API."""
        if self.client == "gemini_http":
            return await self._translate_gemini_http(prompt, count, page_image)

        # The SDK takes a text prompt plus an image part; the blob dict keeps
        # the page in memory only.
        request: Any = prompt
        if page_image is not None:
            request = [prompt, {"mime_type": "image/png", "data": page_image}]
        response = await self.model.generate_content_async(request)
        return self._parse_numbered_response(response.text, count)

    async def _translate_gemini_http(
        self, prompt: str, count: int, page_image: bytes | None = None
    ) -> list[str]:
        """Translate using the Gemini HTTP API (Gemini and Gemma models).

        The reply is streamed so a request that never starts answering is given
        up after the stall timeout instead of after a whole-response timeout.
        """
        import base64

        import httpx

        model_name = self.model_name or "gemini-2.0-flash"
        model_path = model_name if model_name.startswith("models/") else f"models/{model_name}"
        url = f"{GEMINI_API_ROOT}/{model_path}:streamGenerateContent?alt=sse"
        headers = {"x-goog-api-key": self.api_key or "", "Content-Type": "application/json"}
        parts: list[dict[str, Any]] = [{"text": prompt}]
        if page_image is not None:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(page_image).decode("ascii"),
                    }
                }
            )
        body: dict[str, Any] = {"contents": [{"parts": parts}]}
        thinking_level = gemini_thinking_level(model_name)
        if thinking_level:
            body["generationConfig"] = {"thinkingConfig": {"thinkingLevel": thinking_level}}

        def stall_timeout(level: str | None) -> httpx.Timeout:
            # Without thinking the first words arrive within a couple of seconds; a
            # model left to think may stay silent for much longer before it answers.
            stall = GEMINI_STALL_SECONDS if level == "minimal" else GEMINI_THINKING_STALL_SECONDS
            return httpx.Timeout(stall, connect=10.0)

        text: str | None = None
        failure: Exception = RuntimeError("Gemini request was not attempted")
        timeout = stall_timeout(thinking_level)
        async with httpx.AsyncClient(timeout=timeout) as client:
            delays = iter(GEMINI_RETRY_DELAYS_SECONDS)
            while True:
                try:
                    async with client.stream(
                        "POST", url, headers=headers, json=body, timeout=timeout
                    ) as response:
                        if response.status_code == 200:
                            text = await self._read_gemini_stream(response)
                            break
                        error_body = (await response.aread()).decode("utf-8", "replace")
                        if (
                            response.status_code == 400
                            and "generationConfig" in body
                            and "hinking" in error_body
                        ):
                            # This model has no such thinking setting; ask again without it.
                            logger.info("Gemini model rejected the thinking level; retrying without it")
                            del body["generationConfig"]
                            # The model now thinks with its default budget, so the
                            # short stall timeout chosen for "minimal" no longer fits.
                            timeout = stall_timeout(None)
                            continue
                        if response.status_code not in GEMINI_RETRY_STATUSES:
                            response.raise_for_status()
                        failure = httpx.HTTPStatusError(
                            f"Gemini returned HTTP {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                except httpx.TimeoutException as error:
                    failure = error

                delay = next(delays, None)
                if delay is None:
                    raise failure
                logger.warning(
                    "Gemini request failed (%s); retrying in %.0fs",
                    getattr(getattr(failure, "response", None), "status_code", None)
                    or type(failure).__name__,
                    delay,
                )
                await asyncio.sleep(delay)

        if text is None or not text.strip():
            raise RuntimeError("Gemini returned no text")
        return self._parse_numbered_response(text, count)

    @staticmethod
    async def _read_gemini_stream(response: httpx.Response) -> str:
        """Join the answer text of a server-sent-events reply.

        Thinking models send their reasoning as separate parts marked "thought";
        those are left out.
        """
        pieces: list[str] = []
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            chunk = json.loads(line[5:])
            for candidate in chunk.get("candidates", [])[:1]:
                for part in candidate.get("content", {}).get("parts", []):
                    if not part.get("thought"):
                        pieces.append(part.get("text", ""))
        return "".join(pieces)

    async def _translate_openai(
        self, prompt: str, count: int, page_image: bytes | None = None
    ) -> list[str]:
        """Translate using OpenAI-compatible API."""
        import base64

        client = cast("AsyncOpenAI", self.client)
        model = self.model_name if self.model_name else "default"
        logger.info("Sending request to %s (model: %s)...", self.translator_type, model)
        content: Any = prompt
        if page_image is not None:
            # The OpenAI chat schema takes content parts; the image travels as
            # a data URI inside the same request as the text.
            content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(page_image).decode("ascii")
                    },
                },
            ]
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a professional manga translator."},
                {"role": "user", "content": content},
            ],
            temperature=0.3,
        )

        text = response.choices[0].message.content
        logger.info("Received response from %s", self.translator_type)
        return self._parse_numbered_response(text, count)

    def _parse_numbered_response(self, text: str | None, expected_count: int) -> list[str]:
        """Parse numbered translation response."""
        cleaned = self._strip_code_fence(text)

        parsed_json = self._parse_json_response(cleaned, expected_count)
        if parsed_json:
            return parsed_json

        lines = cleaned.split("\n")
        # Place each translation by its number so a skipped or empty item cannot
        # shift every later translation onto the wrong bubble.
        parts: list[list[str]] = [[] for _ in range(expected_count)]
        seen: set[int] = set()
        current: int | None = None
        # Unnumbered lines count as a wrapped item only once another numbered line
        # follows them; trailing prose after the last item must not reach a bubble.
        pending: list[str] = []
        accepting = False

        for line in lines:
            stripped = line.strip()
            # Accept common numbered formats: "1. text", "1) text", "1 - text".
            match = re.match(r'^(?:(\d+)|[\[(](\d+)[\])])\s*[\.\):\]-]\s*(.*)$', stripped)
            if match:
                if current is not None:
                    parts[current].extend(pending)
                pending = []
                index = int(match.group(1) or match.group(2))
                if 1 <= index <= expected_count and index not in seen:
                    seen.add(index)
                    current = index - 1
                    accepting = True
                    parts[current].append(match.group(3))
                else:
                    # Out-of-range or duplicate numbers are dropped, with their continuation lines.
                    current = None
                    accepting = False
            elif not stripped:
                accepting = False
            elif accepting:
                pending.append(stripped)

        translations = [" ".join(piece.strip() for piece in item if piece.strip()).strip('"\'').strip() for item in parts]
        while translations and not translations[-1]:
            translations.pop()

        if len(translations) < expected_count:
            logger.warning(
                "Parser returned %s translations but expected %s. "
                "Missing blocks will be left blank (not rendered).",
                len(translations),
                expected_count,
            )

        return translations

    def _strip_code_fence(self, text: str | None) -> str:
        """Remove a single Markdown code fence around a model response."""
        stripped = (text or "").strip()
        if not stripped.startswith("```"):
            return stripped

        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()

    def _parse_json_response(self, text: str, expected_count: int) -> list[str]:
        """Parse JSON array/object responses some models return despite prompting."""
        try:
            parsed = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return []

        if isinstance(parsed, dict):
            parsed = parsed.get("translations", [])

        if not isinstance(parsed, list):
            return []

        translations = ["" if value is None else str(value).strip() for value in parsed]

        if len(translations) < expected_count:
            logger.warning(
                "Parser returned %s translations but expected %s. "
                "Missing blocks will be left blank (not rendered).",
                len(translations),
                expected_count,
            )

        return translations[:expected_count]
