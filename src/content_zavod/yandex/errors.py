"""Error types shared across Yandex API clients."""

from __future__ import annotations


class YandexError(Exception):
    """Base class for errors raised by Yandex API clients."""


class RateLimited(YandexError):
    """The API rejected the request with a rate-limit response (HTTP 429)."""

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__("Yandex API rate limit exceeded")
        self.retry_after = retry_after


class AuthError(YandexError):
    """The API rejected the request's credentials (HTTP 401/403)."""


class ContentPolicyError(YandexError):
    """The API refused to generate content due to its content policy."""
