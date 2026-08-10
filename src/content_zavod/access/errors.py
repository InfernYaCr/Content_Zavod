"""Error types raised by the access module."""

from __future__ import annotations


class AccessError(Exception):
    """Base class for errors raised by Membership."""


class MemberNotFound(AccessError):
    """No member exists with the given Telegram id."""

    def __init__(self, telegram_id: int) -> None:
        super().__init__(f"No member with telegram_id={telegram_id!r}")


class JoinRequestNotFound(AccessError):
    """No join request exists with the given id."""

    def __init__(self, join_request_id: int) -> None:
        super().__init__(f"No join request with id={join_request_id!r}")
