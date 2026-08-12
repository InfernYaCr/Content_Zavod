"""ImageGenerator: a narrow client to YandexART.

Hides the whole async submit -> operationId -> poll -> result cycle behind
a single call; the caller never sees an operation id.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Awaitable, Callable
from typing import Any

from ._resilience import with_backoff_retry
from .credentials import CredentialProvider, IamTokenProvider, StaticApiKeyProvider
from .errors import YandexError
from .http import HttpResponse, HttpTransport, HttpxTransport

GENERATE_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/imageGenerationAsync"
OPERATION_URL_TEMPLATE = "https://operation.api.cloud.yandex.net/operations/{operation_id}"


class ImageGenerator:
    def __init__(
        self,
        transport: HttpTransport,
        credentials: CredentialProvider,
        *,
        folder_id: str,
        model: str = "yandex-art",
        max_retries: int = 3,
        poll_interval: float = 2.0,
        poll_timeout: float = 120.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        generate_url: str = GENERATE_URL,
        operation_url_template: str = OPERATION_URL_TEMPLATE,
    ) -> None:
        self._transport = transport
        self._credentials = credentials
        self._folder_id = folder_id
        self._model = model
        self._max_retries = max_retries
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout
        self._sleep = sleep
        self._clock = clock
        self._generate_url = generate_url
        self._operation_url_template = operation_url_template

    @classmethod
    def with_service_account_key(
        cls, api_key: str, *, folder_id: str, **kwargs: Any
    ) -> ImageGenerator:
        """Build against a Yandex Cloud service-account API key (no expiry to manage)."""
        return cls(HttpxTransport(), StaticApiKeyProvider(api_key), folder_id=folder_id, **kwargs)

    @classmethod
    def with_oauth_token(cls, oauth_token: str, *, folder_id: str, **kwargs: Any) -> ImageGenerator:
        """Build against a long-lived OAuth token; IAM tokens are refreshed internally."""
        transport = HttpxTransport()
        return cls(
            transport,
            IamTokenProvider(transport, oauth_token=oauth_token),
            folder_id=folder_id,
            **kwargs,
        )

    async def generate_cover(self, prompt: str) -> bytes:
        operation_id = await self._submit(prompt)
        result = await self._poll(operation_id)
        try:
            return base64.b64decode(result["image"])
        except KeyError as exc:
            raise YandexError(f"Malformed YandexART result: {result}") from exc

    async def _submit(self, prompt: str) -> str:
        async def call() -> HttpResponse:
            headers = await self._credentials.auth_header()
            return await self._transport.post(
                self._generate_url,
                headers=headers,
                json=self._request_body(prompt),
            )

        response = await with_backoff_retry(call, max_retries=self._max_retries, sleep=self._sleep)
        try:
            return str(response.body["id"])
        except KeyError as exc:
            raise YandexError(f"Malformed YandexART submit response: {response.body}") from exc

    async def _poll(self, operation_id: str) -> dict[str, Any]:
        url = self._operation_url_template.format(operation_id=operation_id)
        deadline = self._clock() + self._poll_timeout
        while True:

            async def call() -> HttpResponse:
                headers = await self._credentials.auth_header()
                return await self._transport.get(url, headers=headers)

            response = await with_backoff_retry(
                call, max_retries=self._max_retries, sleep=self._sleep
            )
            if response.body.get("done"):
                if "error" in response.body:
                    raise YandexError(f"YandexART operation failed: {response.body['error']}")
                result: dict[str, Any] = response.body["response"]
                return result
            if self._clock() >= deadline:
                raise YandexError("YandexART operation timed out")
            await self._sleep(self._poll_interval)

    def _request_body(self, prompt: str) -> dict[str, Any]:
        return {
            "modelUri": f"art://{self._folder_id}/{self._model}",
            "messages": [{"weight": 1, "text": prompt}],
        }
