"""
Gemini adapter: the only module that talks to the Gemini API.

It knows nothing about invoices or follow-ups; callers supply the instructions,
prompt and JSON schema, and receive a parsed JSON object.
"""

import json
import logging
import time
from typing import Any

import httpx
from django.conf import settings
from google import genai
from google.genai import errors, types

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30
# Gemini returns 503 when a model is temporarily overloaded; retry briefly, but never start a
# new attempt after RETRY_WINDOW_SECONDS so a request stays within the frontend proxy timeout.
MAX_ATTEMPTS = 3
RETRY_WINDOW_SECONDS = 15
RETRYABLE_STATUS_CODES = frozenset({429, 500, 503})


class GeminiError(Exception):
    """Base class. Messages are safe to show to API consumers."""

    code = "gemini_error"


class GeminiUnavailable(GeminiError):
    """Gemini could not be reached or is temporarily refusing requests (retryable)."""

    code = "ai_unavailable"


class GeminiResponseError(GeminiError):
    """Gemini answered, but the request was rejected or the output was unusable."""

    code = "ai_bad_response"


class GeminiClient:
    def __init__(self, api_key: str, model: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.model = model
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout_seconds * 1000),
        )

    @classmethod
    def from_settings(cls) -> "GeminiClient":
        missing = [name for name in ("GEMINI_API_KEY", "GEMINI_MODEL") if not getattr(settings, name)]
        if missing:
            raise GeminiUnavailable(f"Gemini is not configured. Missing: {', '.join(missing)}.")
        return cls(api_key=settings.GEMINI_API_KEY, model=settings.GEMINI_MODEL)

    def generate_json(
        self,
        *,
        system_instruction: str,
        prompt: str,
        response_schema: dict[str, Any],
        temperature: float = 0.4,
    ) -> dict[str, Any]:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_json_schema=response_schema,
            temperature=temperature,
        )
        try:
            response = self._generate_with_retry(prompt, config)
        except errors.ClientError as exc:
            # Log only the status code; messages may echo request content.
            logger.warning("Gemini rejected the request (HTTP %s)", exc.code)
            if exc.code == 429:
                raise GeminiUnavailable("Gemini rate limit reached. Try again shortly.") from None
            if exc.code in (401, 403):
                raise GeminiResponseError("Gemini rejected the API key. Check GEMINI_API_KEY.") from None
            if exc.code == 404:
                raise GeminiResponseError("Gemini model not found. Check GEMINI_MODEL.") from None
            raise GeminiResponseError(f"Gemini rejected the request (HTTP {exc.code}).") from None
        except errors.ServerError as exc:
            logger.warning("Gemini server error (HTTP %s)", exc.code)
            raise GeminiUnavailable("Gemini is temporarily unavailable. Try again shortly.") from None
        except httpx.TimeoutException:
            logger.warning("Gemini request timed out")
            raise GeminiUnavailable("Gemini did not respond in time. Try again.") from None
        except httpx.HTTPError:
            logger.warning("Network error contacting Gemini")
            raise GeminiUnavailable("Could not reach Gemini (network error).") from None

        text = response.text
        if not text:
            # Empty output usually means the response was blocked or cut off.
            logger.warning("Gemini returned no text")
            raise GeminiResponseError("Gemini returned an empty response.")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Gemini returned invalid JSON")
            raise GeminiResponseError("Gemini returned invalid JSON.") from None
        if not isinstance(data, dict):
            raise GeminiResponseError("Gemini returned JSON that is not an object.")
        return data

    def _generate_with_retry(self, prompt: str, config: types.GenerateContentConfig) -> types.GenerateContentResponse:
        """Retries briefly when Gemini is overloaded (5xx/429). Timeouts are not retried."""
        started = time.monotonic()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._client.models.generate_content(model=self.model, contents=prompt, config=config)
            except errors.APIError as exc:
                retryable = exc.code in RETRYABLE_STATUS_CODES
                out_of_time = time.monotonic() - started > RETRY_WINDOW_SECONDS
                if not retryable or attempt == MAX_ATTEMPTS or out_of_time:
                    raise
                logger.info("Gemini returned HTTP %s; retrying (attempt %s)", exc.code, attempt + 1)
                time.sleep(attempt)  # 1s, then 2s
        raise AssertionError("unreachable")
