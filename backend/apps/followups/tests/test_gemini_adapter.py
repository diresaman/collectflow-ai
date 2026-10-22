from unittest import mock

import httpx
from django.test import SimpleTestCase, override_settings
from google.genai import errors

from integrations.gemini import GeminiClient, GeminiQuotaExceeded, GeminiResponseError, GeminiUnavailable

SCHEMA = {"type": "object", "properties": {"subject": {"type": "string"}}}


@mock.patch("integrations.gemini.time.sleep")  # keep retry backoff out of test time
@mock.patch("integrations.gemini.genai.Client")
class GeminiClientTests(SimpleTestCase):
    def call(self) -> dict:  # type: ignore[type-arg]
        return GeminiClient(api_key="test-key", model="gemini-test").generate_json(
            system_instruction="system", prompt="prompt", response_schema=SCHEMA
        )

    def generate(self, client_class: mock.Mock) -> mock.Mock:
        return client_class.return_value.models.generate_content

    def test_returns_parsed_json_and_requests_structured_output(self, client_class: mock.Mock, sleep: mock.Mock) -> None:
        self.generate(client_class).return_value = mock.Mock(text='{"subject": "Hi"}')

        self.assertEqual(self.call(), {"subject": "Hi"})

        kwargs = self.generate(client_class).call_args.kwargs
        self.assertEqual(kwargs["model"], "gemini-test")
        self.assertEqual(kwargs["config"].response_mime_type, "application/json")
        self.assertEqual(kwargs["config"].response_json_schema, SCHEMA)
        http_options = client_class.call_args.kwargs["http_options"]
        self.assertEqual(http_options.timeout, 30_000)

    def test_invalid_json_is_rejected(self, client_class: mock.Mock, sleep: mock.Mock) -> None:
        self.generate(client_class).return_value = mock.Mock(text="Sure! Here is your email: {")
        with self.assertRaisesMessage(GeminiResponseError, "invalid JSON"), self.assertLogs("integrations.gemini"):
            self.call()

    def test_non_object_json_is_rejected(self, client_class: mock.Mock, sleep: mock.Mock) -> None:
        self.generate(client_class).return_value = mock.Mock(text='["a"]')
        with self.assertRaises(GeminiResponseError):
            self.call()

    def test_empty_response_is_rejected(self, client_class: mock.Mock, sleep: mock.Mock) -> None:
        self.generate(client_class).return_value = mock.Mock(text=None)
        with self.assertRaises(GeminiResponseError), self.assertLogs("integrations.gemini"):
            self.call()

    def test_timeout_is_unavailable(self, client_class: mock.Mock, sleep: mock.Mock) -> None:
        self.generate(client_class).side_effect = httpx.ReadTimeout("timed out")
        with self.assertRaisesMessage(GeminiUnavailable, "did not respond in time"), self.assertLogs("integrations.gemini"):
            self.call()

    def test_server_error_and_rate_limit_are_unavailable(self, client_class: mock.Mock, sleep: mock.Mock) -> None:
        for error in (errors.ServerError(503, {"error": {"message": "overloaded"}}), errors.ClientError(429, {})):
            with self.subTest(code=error.code):
                self.generate(client_class).side_effect = error
                with self.assertRaises(GeminiUnavailable), self.assertLogs("integrations.gemini"):
                    self.call()

    def test_quota_error_is_classified_and_never_retried(self, client_class: mock.Mock, sleep: mock.Mock) -> None:
        # Retrying a 429 within seconds only spends more quota; callers back off instead.
        self.generate(client_class).side_effect = errors.ClientError(429, {})
        with self.assertRaises(GeminiQuotaExceeded) as ctx, self.assertLogs("integrations.gemini"):
            self.call()
        self.assertEqual(ctx.exception.code, "ai_quota_exhausted")
        self.assertEqual(self.generate(client_class).call_count, 1)
        sleep.assert_not_called()

    def test_overload_is_retried_then_succeeds(self, client_class: mock.Mock, sleep: mock.Mock) -> None:
        self.generate(client_class).side_effect = [
            errors.ServerError(503, {"error": {"message": "overloaded"}}),
            mock.Mock(text='{"subject": "Hi"}'),
        ]
        with self.assertLogs("integrations.gemini", level="INFO"):
            self.assertEqual(self.call(), {"subject": "Hi"})
        self.assertEqual(self.generate(client_class).call_count, 2)
        sleep.assert_called_once_with(1)

    def test_persistent_overload_gives_up_after_three_attempts(self, client_class: mock.Mock, sleep: mock.Mock) -> None:
        self.generate(client_class).side_effect = errors.ServerError(503, {})
        with self.assertRaises(GeminiUnavailable), self.assertLogs("integrations.gemini", level="INFO"):
            self.call()
        self.assertEqual(self.generate(client_class).call_count, 3)

    def test_client_errors_and_timeouts_are_not_retried(self, client_class: mock.Mock, sleep: mock.Mock) -> None:
        for error in (errors.ClientError(400, {}), errors.ClientError(404, {}), httpx.ReadTimeout("slow")):
            with self.subTest(error=type(error).__name__):
                self.generate(client_class).reset_mock()
                self.generate(client_class).side_effect = error
                with self.assertRaises((GeminiResponseError, GeminiUnavailable)), self.assertLogs("integrations.gemini"):
                    self.call()
                self.assertEqual(self.generate(client_class).call_count, 1)
        sleep.assert_not_called()

    def test_bad_key_is_bad_response_without_leaking_details(self, client_class: mock.Mock, sleep: mock.Mock) -> None:
        self.generate(client_class).side_effect = errors.ClientError(403, {"error": {"message": "key AIza-secret invalid"}})
        with self.assertRaises(GeminiResponseError) as ctx, self.assertLogs("integrations.gemini") as logs:
            self.call()
        self.assertNotIn("AIza-secret", str(ctx.exception))
        self.assertNotIn("AIza-secret", "".join(logs.output))

    @override_settings(GEMINI_API_KEY="", GEMINI_MODEL="gemini-test")
    def test_missing_key_is_unavailable(self, client_class: mock.Mock, sleep: mock.Mock) -> None:
        with self.assertRaisesMessage(GeminiUnavailable, "GEMINI_API_KEY"):
            GeminiClient.from_settings()
        client_class.assert_not_called()
