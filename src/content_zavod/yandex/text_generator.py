"""TextGenerator: a narrow client to YandexGPT.

Hides retries with backoff on rate limiting, credential refresh, and
mapping of Yandex's error responses onto RateLimited / AuthError /
ContentPolicyError / TruncatedCompletion.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from ._resilience import with_backoff_retry
from .credentials import CredentialProvider, IamTokenProvider, StaticApiKeyProvider
from .errors import ContentPolicyError, TruncatedCompletion, YandexError
from .http import HttpResponse, HttpTransport, HttpxTransport

COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

# The single source of truth for `complete`/`complete_with_usage`'s default `temperature` -
# callers that record provenance (see `pipelines.provenance`) import this instead of
# re-hardcoding the number, so the recorded `params` can't drift from what was actually sent.
DEFAULT_TEMPERATURE = 0.7

# Explicit cap on generated tokens, with headroom over the longest step (a VC.ru Статья of
# ~9000 знаков is ~3000 tokens) - without it the API's own default cuts long drafts (#93).
DEFAULT_MAX_TOKENS = 8000

# `result.alternatives[].status` values meaning the text is not a usable finished answer (#93).
_STATUS_TRUNCATED = "ALTERNATIVE_STATUS_TRUNCATED_FINAL"
_STATUS_CONTENT_FILTER = "ALTERNATIVE_STATUS_CONTENT_FILTER"

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    text: str


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    tokens: int
    usage_missing: bool = False
    latency_ms: int = 0
    cost: float | None = None


class TextGenerator:
    def __init__(
        self,
        transport: HttpTransport,
        credentials: CredentialProvider,
        *,
        folder_id: str,
        model: str = "yandexgpt",
        max_retries: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        url: str = COMPLETION_URL,
        clock: Callable[[], float] = time.monotonic,
        cost_per_1k_tokens: float | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._transport = transport
        self._credentials = credentials
        self._folder_id = folder_id
        self._model = model
        self._max_retries = max_retries
        self._sleep = sleep
        self._url = url
        self._clock = clock
        self._cost_per_1k_tokens = cost_per_1k_tokens
        self._max_tokens = max_tokens

    @classmethod
    def with_service_account_key(
        cls, api_key: str, *, folder_id: str, **kwargs: Any
    ) -> TextGenerator:
        """Build against a Yandex Cloud service-account API key (no expiry to manage)."""
        return cls(HttpxTransport(), StaticApiKeyProvider(api_key), folder_id=folder_id, **kwargs)

    @classmethod
    def with_oauth_token(cls, oauth_token: str, *, folder_id: str, **kwargs: Any) -> TextGenerator:
        """Build against a long-lived OAuth token; IAM tokens are refreshed internally."""
        transport = HttpxTransport()
        return cls(
            transport,
            IamTokenProvider(transport, oauth_token=oauth_token),
            folder_id=folder_id,
            **kwargs,
        )

    async def complete(
        self, messages: list[Message], *, temperature: float = DEFAULT_TEMPERATURE
    ) -> str:
        completion = await self.complete_with_usage(messages, temperature=temperature)
        return completion.text

    async def complete_with_usage(
        self, messages: list[Message], *, temperature: float = DEFAULT_TEMPERATURE
    ) -> Completion:
        async def call() -> HttpResponse:
            headers = await self._credentials.auth_header()
            return await self._transport.post(
                self._url,
                headers=headers,
                json=self._request_body(messages, temperature),
            )

        started = self._clock()
        response = await with_backoff_retry(call, max_retries=self._max_retries, sleep=self._sleep)
        latency_ms = max(0, round((self._clock() - started) * 1000))
        return self._extract_completion(response.body, latency_ms=latency_ms)

    def _request_body(self, messages: list[Message], temperature: float) -> dict[str, Any]:
        return {
            "modelUri": f"gpt://{self._folder_id}/{self._model}",
            # int64 is a JSON string in the API's REST mapping, as in Yandex's own examples.
            "completionOptions": {"temperature": temperature, "maxTokens": str(self._max_tokens)},
            "messages": [{"role": m.role, "text": m.text} for m in messages],
        }

    def _extract_completion(self, body: dict[str, Any], *, latency_ms: int) -> Completion:
        try:
            result = body["result"]
            alternative = result["alternatives"][0]
            text = str(alternative["message"]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            raise YandexError(f"Malformed YandexGPT response: {body}") from exc
        # A truncated or filtered answer still arrives as HTTP 200 with text - without this
        # check it would be stored as a finished Версия (#93).
        status = alternative.get("status")
        if status == _STATUS_TRUNCATED:
            raise TruncatedCompletion(
                f"YandexGPT response truncated at maxTokens={self._max_tokens}"
            )
        if status == _STATUS_CONTENT_FILTER:
            raise ContentPolicyError(f"YandexGPT response blocked by content filter: {text!r}")
        # `usage` is absent entirely from some sandbox responses - that's a real gap in
        # what we know this call cost, not a legitimate zero, so it's tracked explicitly
        # via `usage_missing` rather than folded into `tokens` defaulting to 0.
        usage_missing = not isinstance(result.get("usage"), dict)
        usage = result.get("usage") or {}
        try:
            tokens = int(usage.get("totalTokens", 0))
        except (TypeError, ValueError):
            tokens = 0
        model = str(result.get("modelVersion", ""))
        cost = None
        if not usage_missing and self._cost_per_1k_tokens is not None:
            cost = tokens * self._cost_per_1k_tokens / 1000
        return Completion(
            text=text,
            model=model,
            tokens=tokens,
            usage_missing=usage_missing,
            latency_ms=latency_ms,
            cost=cost,
        )
