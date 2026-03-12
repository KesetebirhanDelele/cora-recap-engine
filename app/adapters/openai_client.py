"""
OpenAI adapter — Phase 5.

Executes chat completions against the OpenAI API. Returns raw parsed dicts;
schema validation and business logic live in app/services/ai.py.

Model name handling:
  Settings may store litellm-style prefixed names (e.g. "openai/gpt-4o-mini").
  The _strip_prefix() helper removes the "openai/" prefix before SDK calls.

Retry policy (mirrors GHLClient):
  Retries on RateLimitError (429), APIStatusError 5xx, APITimeoutError.
  Bounded by settings.openai_retry_max. Delay doubles per attempt (2^n seconds).
  AuthenticationError is NOT retried — surfaces immediately as OpenAIError.

Injectable _client for tests:
  Pass a mock openai.OpenAI instance via the _client kwarg to avoid real API calls.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import openai

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_RETRYABLE_5XX = frozenset({500, 502, 503, 504})


class OpenAIError(RuntimeError):
    """Raised when an OpenAI API call fails after all retries."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class OpenAIClient:
    """
    OpenAI API client.

    Usage:
        client = OpenAIClient()
        result = client.chat_completion(
            messages=[{"role": "user", "content": "Hello"}],
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
        )
        # result is a dict parsed from the model's JSON response

    Injectable for tests:
        mock_oai = MagicMock()
        mock_oai.chat.completions.create.return_value = mock_response
        client = OpenAIClient(settings=s, _client=mock_oai)
    """

    def __init__(self, settings: Settings | None = None, _client: Any | None = None):
        self.settings = settings or get_settings()
        # _client injected in tests; production creates a real openai.OpenAI instance.
        # base_url is passed explicitly so our validated/normalised settings value wins
        # over any raw OPENAI_BASE_URL env var the SDK would otherwise pick up directly.
        if _client is not None:
            self._oai = _client
        else:
            kwargs: dict[str, Any] = {
                "api_key": self.settings.openai_api_key,
                "timeout": float(self.settings.openai_timeout_seconds),
            }
            if self.settings.openai_base_url:
                kwargs["base_url"] = self.settings.openai_base_url
            self._oai = openai.OpenAI(**kwargs)

    @staticmethod
    def _strip_prefix(model: str) -> str:
        """Strip 'openai/' litellm-style prefix before passing to OpenAI SDK."""
        if model.startswith("openai/"):
            return model[len("openai/"):]
        return model

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str,
        response_format: dict | None = None,
        *,
        _retry_delay: float = 1.0,
    ) -> dict:
        """
        Execute a chat completion and return the parsed JSON response content.

        messages: OpenAI chat messages list.
        model: model name (may include 'openai/' prefix — stripped automatically).
        response_format: e.g. {"type": "json_object"} to request JSON output.
        _retry_delay: base delay seconds; pass 0.0 in tests to skip sleeps.

        Returns: parsed dict from the model's response content.
        Raises: OpenAIError on auth failure or exhausted retries.
                json.JSONDecodeError if the model returns non-JSON despite request.
        """
        self.settings.validate_for_openai()
        clean_model = self._strip_prefix(model)
        kwargs: dict[str, Any] = {"model": clean_model, "messages": messages}
        if response_format:
            kwargs["response_format"] = response_format

        for attempt in range(self.settings.openai_retry_max + 1):
            try:
                resp = self._oai.chat.completions.create(**kwargs)
                content = resp.choices[0].message.content or ""
                return json.loads(content)

            except openai.AuthenticationError as exc:
                raise OpenAIError(
                    f"OpenAI authentication failed — check OPENAI_API_KEY: {exc}"
                ) from exc

            except openai.RateLimitError as exc:
                if attempt < self.settings.openai_retry_max:
                    logger.warning(
                        "OpenAI rate limit | attempt=%d/%d model=%s",
                        attempt + 1, self.settings.openai_retry_max, clean_model,
                    )
                    time.sleep(_retry_delay * (2**attempt))
                    continue
                raise OpenAIError(
                    f"OpenAI rate limit exhausted after {attempt + 1} attempts"
                ) from exc

            except openai.APITimeoutError as exc:
                if attempt < self.settings.openai_retry_max:
                    logger.warning(
                        "OpenAI timeout | attempt=%d/%d model=%s",
                        attempt + 1, self.settings.openai_retry_max, clean_model,
                    )
                    time.sleep(_retry_delay * (2**attempt))
                    continue
                raise OpenAIError(
                    f"OpenAI timeout after {attempt + 1} attempts: {exc}"
                ) from exc

            except openai.APIStatusError as exc:
                if exc.status_code in _RETRYABLE_5XX:
                    if attempt < self.settings.openai_retry_max:
                        logger.warning(
                            "OpenAI server error | status=%d attempt=%d/%d",
                            exc.status_code, attempt + 1, self.settings.openai_retry_max,
                        )
                        time.sleep(_retry_delay * (2**attempt))
                        continue
                raise OpenAIError(
                    f"OpenAI API error: {exc.status_code}", status_code=exc.status_code
                ) from exc

            except json.JSONDecodeError as exc:
                raise OpenAIError(
                    f"OpenAI returned non-JSON content for model={clean_model}"
                ) from exc

        raise OpenAIError(
            f"OpenAI request exhausted {self.settings.openai_retry_max} retries"
        )
