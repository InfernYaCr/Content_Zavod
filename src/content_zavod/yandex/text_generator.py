"""TextGenerator: a narrow client to YandexGPT.

Hides retries with backoff on rate limiting, credential refresh, and
mapping of Yandex's error responses onto RateLimited / AuthError /
ContentPolicyError.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from ._resilience import with_backoff_retry
from .credentials import CredentialProvider, IamTokenProvider, StaticApiKeyProvider
from .errors import YandexError
from .http import HttpResponse, HttpTransport, HttpxTransport

COMPLETION_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

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
    ) -> None:
        self._transport = transport
        self._credentials = credentials
        self._folder_id = folder_id
        self._model = model
        self._max_retries = max_retries
        self._sleep = sleep
        self._url = url

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

    async def complete(self, messages: list[Message], *, temperature: float = 0.7) -> str:
        completion = await self.complete_with_usage(messages, temperature=temperature)
        return completion.text

    async def complete_with_usage(
        self, messages: list[Message], *, temperature: float = 0.7
    ) -> Completion:
        async def call() -> HttpResponse:
            headers = await self._credentials.auth_header()
            return await self._transport.post(
                self._url,
                headers=headers,
                json=self._request_body(messages, temperature),
            )

        response = await with_backoff_retry(call, max_retries=self._max_retries, sleep=self._sleep)
        return self._extract_completion(response.body)

    def _request_body(self, messages: list[Message], temperature: float) -> dict[str, Any]:
        return {
            "modelUri": f"gpt://{self._folder_id}/{self._model}",
            "completionOptions": {"temperature": temperature},
            "messages": [{"role": m.role, "text": m.text} for m in messages],
        }

    @staticmethod
    def _extract_completion(body: dict[str, Any]) -> Completion:
        try:
            result = body["result"]
            text = str(result["alternatives"][0]["message"]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            raise YandexError(f"Malformed YandexGPT response: {body}") from exc
        # usage/modelVersion are absent from some sandbox responses; tokens/model
        # default rather than fail the whole completion over accounting fields.
        usage = result.get("usage") or {}
        try:
            tokens = int(usage.get("totalTokens", 0))
        except (TypeError, ValueError):
            tokens = 0
        model = str(result.get("modelVersion", ""))
        return Completion(text=text, model=model, tokens=tokens)
