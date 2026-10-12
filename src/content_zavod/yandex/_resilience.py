"""Error mapping and retry-with-backoff, shared internally by
TextGenerator and ImageGenerator. Not part of either client's public
interface."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .errors import AuthError, ContentPolicyError, RateLimited, YandexError
from .http import HttpResponse


def map_error(response: HttpResponse) -> YandexError:
    message = _error_message(response.body)
    if response.status == 429:
        retry_after = response.body.get("retryAfter") if response.body else None
        return RateLimited(retry_after=retry_after)
    if response.status in (401, 403):
        # 403 is also a missing role or API-key scope - the message says which.
        return AuthError(
            f"Yandex API rejected credentials (status={response.status}): {message or response.body}"
        )
    if response.status == 402:
        return YandexError(
            f"Yandex Cloud refused to bill the request - check the billing account balance "
            f"(status=402): {message or response.body}"
        )
    if response.status == 400 and _is_content_policy_violation(message):
        return ContentPolicyError(message or "Content policy violation")
    return YandexError(f"Yandex API request failed (status={response.status}): {response.body}")


def _error_message(body: dict[str, Any]) -> str:
    """`message` at the top (Yandex's own APIs) or under `error` (the OpenAI-compatible ones)."""
    error = body.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return str(body.get("message") or "")


def _is_content_policy_violation(message: str) -> bool:
    message = message.lower()
    return "content" in message and any(w in message for w in ("polic", "filter", "block"))


async def with_backoff_retry(
    call: Callable[[], Awaitable[HttpResponse]],
    *,
    max_retries: int,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    base_delay: float = 0.5,
) -> HttpResponse:
    attempt = 0
    while True:
        response = await call()
        if response.status == 200:
            return response
        error = map_error(response)
        if isinstance(error, RateLimited) and attempt < max_retries:
            delay = (
                error.retry_after if error.retry_after is not None else base_delay * (2**attempt)
            )
            await sleep(delay)
            attempt += 1
            continue
        raise error


async def with_connection_retry(
    call: Callable[[], Awaitable[HttpResponse]],
    *,
    max_retries: int,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    base_delay: float = 0.5,
) -> HttpResponse:
    """Retry `call` when it raises (SSL/connect errors from the transport),
    as opposed to `with_backoff_retry` which retries on HTTP-level rate
    limiting. Used for hosts observed to be flaky on first connection."""
    attempt = 0
    while True:
        try:
            return await call()
        except Exception:
            if attempt >= max_retries:
                raise
            await sleep(base_delay * (2**attempt))
            attempt += 1
