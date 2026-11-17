"""Model-free tests for backend translation helpers."""

import asyncio
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models.translator import (
    Translator,
    TranslatorError,
    TranslatorTimeoutError,
    validate_local_llm_base_url,
)


class TranslatorParserTests(unittest.TestCase):
    def setUp(self):
        self.translator = Translator()

    def test_parses_common_numbered_response_formats(self):
        response = """
        1. First line
        2) Second line
        3 - Third line
        [4]: Fourth line
        """

        result = self.translator._parse_numbered_response(response, 4)

        self.assertEqual(result, ["First line", "Second line", "Third line", "Fourth line"])

    def test_parses_json_array_response(self):
        result = self.translator._parse_numbered_response('["One", "Two"]', 2)

        self.assertEqual(result, ["One", "Two"])

    def test_parses_fenced_json_object_response(self):
        response = """```json
        {"translations": ["One", "Two"]}
        ```"""

        result = self.translator._parse_numbered_response(response, 2)

        self.assertEqual(result, ["One", "Two"])

    def test_returns_only_parsed_translations_when_response_is_short(self):
        result = self.translator._parse_numbered_response("1. One", 2)

        self.assertEqual(result, ["One"])

    def test_skipped_number_leaves_a_blank_instead_of_shifting_later_items(self):
        response = "1. One\n3. Three\n4. Four"

        result = self.translator._parse_numbered_response(response, 4)

        self.assertEqual(result, ["One", "", "Three", "Four"])

    def test_empty_item_keeps_later_translations_on_their_own_numbers(self):
        response = "1. One\n2.\n3. Three"

        result = self.translator._parse_numbered_response(response, 3)

        self.assertEqual(result, ["One", "", "Three"])

    def test_multi_line_item_is_joined_without_shifting_later_items(self):
        response = "1. First line\ncontinues here\n2. Second\n\nNote: all done"

        result = self.translator._parse_numbered_response(response, 2)

        self.assertEqual(result, ["First line continues here", "Second"])

    def test_trailing_prose_after_the_last_item_is_not_added_to_it(self):
        response = "1. One\n2. Two\nLet me know if you want changes."

        result = self.translator._parse_numbered_response(response, 2)

        self.assertEqual(result, ["One", "Two"])

    def test_out_of_range_and_duplicate_numbers_are_ignored(self):
        response = "1. One\n1. Again\n5. Far away\n2. Two"

        result = self.translator._parse_numbered_response(response, 2)

        self.assertEqual(result, ["One", "Two"])

    def test_unsupported_translator_type_is_rejected_on_initialize(self):
        with self.assertRaises(ValueError):
            asyncio.run(Translator(translator_type="deepl").initialize())


class ProviderErrorTests(unittest.TestCase):
    def translate_with_provider_error(self, error):
        translator = Translator(translator_type="openai")
        with patch.object(translator, "_translate_openai", side_effect=error):
            return asyncio.run(translator.translate(["text"], "English"))

    def test_provider_failure_is_logged_without_exception_text(self):
        class APIStatusError(Exception):
            status_code = 401

        error = APIStatusError("Incorrect API key provided: sk-secret")

        with self.assertLogs("models.translator", level="ERROR") as logs:
            with self.assertRaises(TranslatorError) as raised:
                self.translate_with_provider_error(error)

        output = "\n".join(logs.output)
        self.assertIn("APIStatusError", output)
        self.assertIn("HTTP 401", output)
        self.assertNotIn("sk-secret", output)
        self.assertNotIn("sk-secret", str(raised.exception))
        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(
            raised.exception.public_detail, "Translation provider request failed"
        )
        self.assertNotIsInstance(raised.exception, TranslatorTimeoutError)

    def test_provider_timeout_maps_to_timeout_error(self):
        with self.assertLogs("models.translator", level="ERROR"):
            with self.assertRaises(TranslatorTimeoutError) as raised:
                self.translate_with_provider_error(asyncio.TimeoutError("sk-secret"))

        self.assertEqual(raised.exception.status_code, 504)

    def test_http_status_is_read_from_the_error_response(self):
        class HTTPStatusError(Exception):
            def __init__(self):
                super().__init__("body with sk-secret")
                self.response = type("Response", (), {"status_code": 429})()

        with self.assertLogs("models.translator", level="ERROR") as logs:
            with self.assertRaises(TranslatorError):
                self.translate_with_provider_error(HTTPStatusError())

        output = "\n".join(logs.output)
        self.assertIn("HTTP 429", output)
        self.assertNotIn("sk-secret", output)


class TranslatorCloseTests(unittest.TestCase):
    def test_close_releases_openai_compatible_client(self):
        translator = Translator(translator_type="openai")
        client = types.SimpleNamespace(close=AsyncMock())
        translator.client = client

        asyncio.run(translator.close())

        client.close.assert_awaited_once()
        self.assertIsNone(translator.client)

    def test_close_failure_does_not_escape(self):
        translator = Translator(translator_type="local")
        translator.client = types.SimpleNamespace(close=AsyncMock(side_effect=OSError("sk-secret")))

        with self.assertLogs("models.translator", level="WARNING") as logs:
            asyncio.run(translator.close())

        self.assertNotIn("sk-secret", "\n".join(logs.output))

    def test_close_is_safe_before_initialize_and_for_gemini_http(self):
        asyncio.run(Translator(translator_type="openai").close())
        gemini = Translator(translator_type="gemini")
        gemini.client = "gemini_http"
        asyncio.run(gemini.close())
        self.assertIsNone(gemini.client)


class GeminiHttpTests(unittest.TestCase):
    """The Gemini HTTP path: thinking level, retries and thought parts."""

    def setUp(self):
        env_patch = patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("GEMINI_THINKING_LEVEL", None)

    def translate(self, model, replies):
        """Run one translation against scripted replies; return (result, requests, sleeps)."""
        import httpx
        import json

        self.requests = requests = []
        pending = list(replies)

        def handler(request):
            requests.append(
                {
                    "url": str(request.url),
                    "body": json.loads(request.content),
                    "read_timeout": request.extensions.get("timeout", {}).get("read"),
                }
            )
            reply = pending.pop(0)
            if isinstance(reply, Exception):
                raise reply
            status, payload = reply
            if isinstance(payload, str):
                return httpx.Response(status, text=payload)
            return httpx.Response(status, json=payload)

        real_client = httpx.AsyncClient

        def client_factory(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        translator = Translator(translator_type="gemini", api_key="test-key", model=model)
        sleep = AsyncMock()

        async def run():
            await translator.initialize()
            return await translator.translate(["一", "二"], "English")

        with patch.dict(sys.modules, {"google.generativeai": None}), patch(
            "httpx.AsyncClient", client_factory
        ), patch("models.translator.asyncio.sleep", sleep):
            result = asyncio.run(run())
        return result, requests, [call.args[0] for call in sleep.await_args_list]

    @staticmethod
    def answer(*parts):
        """A streamed reply: one server-sent event per part."""
        import json

        events = [
            "data: " + json.dumps({"candidates": [{"content": {"parts": [part]}}]})
            for part in parts
        ]
        return 200, "\n\n".join(events) + "\n\n"

    def test_gemma_4_asks_for_minimal_thinking_and_ignores_thought_parts(self):
        result, requests, _sleeps = self.translate(
            "gemma-4-26b-a4b-it",
            [self.answer({"text": "1. wrong\n2. wrong", "thought": True}, {"text": "1. One\n2. Two"})],
        )

        self.assertEqual(result, ["One", "Two"])
        self.assertEqual(len(requests), 1)
        self.assertIn("/v1beta/models/gemma-4-26b-a4b-it:streamGenerateContent", requests[0]["url"])
        self.assertEqual(
            requests[0]["body"]["generationConfig"],
            {"thinkingConfig": {"thinkingLevel": "minimal"}},
        )

    def test_gemini_models_keep_their_default_thinking(self):
        _result, requests, _sleeps = self.translate(
            "gemini-2.0-flash", [self.answer({"text": "1. One\n2. Two"})]
        )

        self.assertNotIn("generationConfig", requests[0]["body"])

    def test_thinking_level_can_be_overridden_or_switched_off(self):
        os.environ["GEMINI_THINKING_LEVEL"] = "low"
        _result, forced, _sleeps = self.translate(
            "gemini-3-flash", [self.answer({"text": "1. One\n2. Two"})]
        )
        os.environ["GEMINI_THINKING_LEVEL"] = "off"
        _result, disabled, _sleeps = self.translate(
            "gemma-4-26b-a4b-it", [self.answer({"text": "1. One\n2. Two"})]
        )

        self.assertEqual(
            forced[0]["body"]["generationConfig"]["thinkingConfig"]["thinkingLevel"], "low"
        )
        self.assertNotIn("generationConfig", disabled[0]["body"])

    def test_server_errors_and_timeouts_are_retried(self):
        import httpx

        result, requests, sleeps = self.translate(
            "gemma-4-26b-a4b-it",
            [
                (500, {"error": {"message": "Internal error encountered."}}),
                httpx.ReadTimeout("no answer"),
                self.answer({"text": "1. One\n2. Two"}),
            ],
        )

        self.assertEqual(result, ["One", "Two"])
        self.assertEqual(len(requests), 3)
        self.assertEqual(sleeps, [1.0, 2.0])

    def test_gives_up_after_the_last_retry(self):
        from models.translator import GEMINI_RETRY_DELAYS_SECONDS

        # The first attempt plus one retry per configured delay.
        attempts = len(GEMINI_RETRY_DELAYS_SECONDS) + 1
        failures = [(500, {"error": {"message": "Internal error encountered."}})] * attempts

        with self.assertRaises(TranslatorError):
            self.translate("gemma-4-26b-a4b-it", failures)

        self.assertEqual(len(self.requests), attempts)

    def test_persistent_timeouts_report_a_timeout(self):
        import httpx
        from models.translator import GEMINI_RETRY_DELAYS_SECONDS

        attempts = len(GEMINI_RETRY_DELAYS_SECONDS) + 1
        with self.assertRaises(TranslatorTimeoutError):
            self.translate("gemma-4-26b-a4b-it", [httpx.ReadTimeout("no answer")] * attempts)

    def test_unsupported_thinking_level_is_dropped_and_asked_again(self):
        result, requests, sleeps = self.translate(
            "gemma-4-future",
            [
                (400, {"error": {"message": "Thinking level is not supported for this model."}}),
                self.answer({"text": "1. One\n2. Two"}),
            ],
        )

        self.assertEqual(result, ["One", "Two"])
        self.assertIn("generationConfig", requests[0]["body"])
        self.assertNotIn("generationConfig", requests[1]["body"])
        self.assertEqual(sleeps, [])

    def test_dropped_thinking_level_gets_the_default_thinking_stall_timeout(self):
        from models.translator import GEMINI_STALL_SECONDS, GEMINI_THINKING_STALL_SECONDS

        _result, requests, _sleeps = self.translate(
            "gemma-4-future",
            [
                (400, {"error": {"message": "Thinking level is not supported for this model."}}),
                self.answer({"text": "1. One\n2. Two"}),
            ],
        )

        # "minimal" answers quickly, so the first request keeps the short stall
        # timeout; without the setting the model thinks silently for longer.
        self.assertEqual(requests[0]["read_timeout"], GEMINI_STALL_SECONDS)
        self.assertEqual(requests[1]["read_timeout"], GEMINI_THINKING_STALL_SECONDS)

    def test_answer_split_across_stream_events_is_joined(self):
        result, _requests, _sleeps = self.translate(
            "gemma-4-26b-a4b-it",
            [self.answer({"text": "1. On"}, {"text": "e\n2. T"}, {"text": "wo"})],
        )

        self.assertEqual(result, ["One", "Two"])

    def test_empty_answer_is_an_error_not_a_blank_page(self):
        with self.assertRaises(TranslatorError):
            self.translate("gemma-4-26b-a4b-it", [self.answer({"text": "ignored", "thought": True})])

        self.assertEqual(len(self.requests), 1)

    def test_other_client_errors_are_not_retried(self):
        replies = [(400, {"error": {"message": "API key not valid."}})]

        with self.assertRaises(TranslatorError):
            self.translate("gemini-2.0-flash", replies)


class LocalLlmUrlValidationTests(unittest.TestCase):
    def setUp(self):
        # Default-policy tests must not depend on the developer's environment or .env.
        env_patch = patch.dict(os.environ)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        os.environ.pop("ALLOW_REMOTE_LOCAL_LLM", None)

    def test_accepts_loopback_urls_and_strips_trailing_slash(self):
        self.assertEqual(
            validate_local_llm_base_url("http://localhost:8000/v1/"),
            "http://localhost:8000/v1",
        )
        self.assertEqual(
            validate_local_llm_base_url("http://127.0.0.1:8000/v1"),
            "http://127.0.0.1:8000/v1",
        )
        self.assertEqual(
            validate_local_llm_base_url("http://[::1]:8000/v1"),
            "http://[::1]:8000/v1",
        )

    def test_rejects_remote_urls_by_default(self):
        with self.assertRaises(ValueError):
            validate_local_llm_base_url("https://example.com/v1")

    def test_allows_remote_urls_only_when_explicitly_enabled(self):
        with patch.dict(os.environ, {"ALLOW_REMOTE_LOCAL_LLM": "true"}):
            self.assertEqual(
                validate_local_llm_base_url("https://example.com/v1/"),
                "https://example.com/v1",
            )

    def test_rejects_non_http_urls(self):
        with self.assertRaises(ValueError):
            validate_local_llm_base_url("file:///tmp/server")


if __name__ == "__main__":
    unittest.main()
