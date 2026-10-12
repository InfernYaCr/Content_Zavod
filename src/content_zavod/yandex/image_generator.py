"""ImageGenerator: a narrow client to Alice AI ART via the OpenAI-compatible Images API (#123).

One synchronous `POST /v1/images/generations` per cover: the image comes back in the response
as `data[0].b64_json`, so there is no operation to poll (the old `imageGenerationAsync` API is
no longer supported).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ._resilience import with_backoff_retry
from .credentials import CredentialProvider, IamTokenProvider, StaticApiKeyProvider
from .errors import YandexError
from .http import HttpResponse, HttpTransport, HttpxTransport

GENERATE_URL = "https://ai.api.cloud.yandex.net/v1/images/generations"
DEFAULT_IMAGE_MODEL = "aliceai-image-art-3.0"
# Square, as the old YandexART call produced with no aspect ratio set; the Images API takes
# 1024x1024, 1536x1024, 1024x1536.
COVER_SIZE = "1024x1024"
# Alice AI ART's prompt limit, in characters.
PROMPT_LIMIT = 500
# A synchronous generation holds the request open until the picture is drawn.
_TIMEOUT = 120.0


@dataclass(frozen=True)
class GeneratedImage:
    image: bytes
    model: str
    latency_ms: int
    cost: float | None
    mime_type: str = "image/png"


class ImageGenerator:
    def __init__(
        self,
        transport: HttpTransport,
        credentials: CredentialProvider,
        *,
        folder_id: str,
        model: str = DEFAULT_IMAGE_MODEL,
        max_retries: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        generate_url: str = GENERATE_URL,
        cost_per_generation: float | None = None,
    ) -> None:
        self._transport = transport
        self._credentials = credentials
        self._folder_id = folder_id
        self._model = model
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._generate_url = generate_url
        self._cost_per_generation = cost_per_generation

    @classmethod
    def with_service_account_key(
        cls, api_key: str, *, folder_id: str, **kwargs: Any
    ) -> ImageGenerator:
        """Build against a Yandex Cloud service-account API key (no expiry to manage)."""
        return cls(
            HttpxTransport(timeout=_TIMEOUT),
            StaticApiKeyProvider(api_key),
            folder_id=folder_id,
            **kwargs,
        )

    @classmethod
    def with_oauth_token(cls, oauth_token: str, *, folder_id: str, **kwargs: Any) -> ImageGenerator:
        """Build against a long-lived OAuth token; IAM tokens are refreshed internally."""
        transport = HttpxTransport(timeout=_TIMEOUT)
        return cls(
            transport,
            IamTokenProvider(transport, oauth_token=oauth_token),
            folder_id=folder_id,
            **kwargs,
        )

    async def generate_cover(self, prompt: str) -> bytes:
        generated = await self.generate_cover_with_usage(prompt)
        return generated.image

    async def generate_cover_with_usage(self, prompt: str) -> GeneratedImage:
        async def call() -> HttpResponse:
            headers = await self._credentials.auth_header()
            return await self._transport.post(
                self._generate_url,
                headers={**headers, "OpenAI-Project": self._folder_id},
                json={
                    "model": f"art://{self._folder_id}/{self._model}",
                    "prompt": _fit_prompt(prompt),
                    "size": COVER_SIZE,
                },
            )

        started = self._clock()
        response = await with_backoff_retry(call, max_retries=self._max_retries, sleep=self._sleep)
        latency_ms = max(0, round((self._clock() - started) * 1000))
        try:
            image = base64.b64decode(response.body["data"][0]["b64_json"], validate=True)
        except (KeyError, IndexError, TypeError, binascii.Error) as exc:
            raise YandexError(f"Malformed Images API response: {response.body}") from exc
        return GeneratedImage(
            image=image,
            model=self._model,
            latency_ms=latency_ms,
            cost=self._cost_per_generation,
            mime_type="image/jpeg" if image.startswith(b"\xff\xd8\xff") else "image/png",
        )


def _fit_prompt(prompt: str) -> str:
    """Cuts a prompt over `PROMPT_LIMIT` at the last word boundary, marked with «…»."""
    if len(prompt) <= PROMPT_LIMIT:
        return prompt
    cut = prompt[: PROMPT_LIMIT - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:—-") + "…"
