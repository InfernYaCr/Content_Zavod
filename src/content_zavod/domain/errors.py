"""Error types raised by the domain layer."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for errors raised by the domain layer."""


class PlanNotFound(DomainError):
    """No plan exists with the given id."""

    def __init__(self, plan_id: object) -> None:
        super().__init__(f"No plan with id={plan_id!r}")


class PlanItemNotFound(DomainError):
    """No plan item exists with the given id."""

    def __init__(self, plan_item_id: object) -> None:
        super().__init__(f"No plan item with id={plan_item_id!r}")


class PlanItemNotEditable(DomainError):
    """Raised when delete/regenerate is attempted on an item that is already approved."""

    def __init__(self, plan_item_id: object, status: str) -> None:
        super().__init__(f"Plan item {plan_item_id!r} is not editable (status={status!r})")


class ArticleNotFound(DomainError):
    """No article exists with the given id."""

    def __init__(self, article_id: object) -> None:
        super().__init__(f"No article with id={article_id!r}")


class ArticleNotReady(DomainError):
    """Raised when an operation requires a generated version that does not exist yet."""

    def __init__(self, article_id: object) -> None:
        super().__init__(f"Article {article_id!r} has no ready version")


class ArticleNotRegenerable(DomainError):
    """Raised when regeneration is requested while a generation is already in flight or the article is locked."""

    def __init__(self, article_id: object, status: str) -> None:
        super().__init__(f"Article {article_id!r} cannot be regenerated (status={status!r})")


class ArticleVersionNotFound(DomainError):
    """No article version exists with the given id for the given article."""

    def __init__(self, article_id: object, version_id: object) -> None:
        super().__init__(f"No version {version_id!r} for article {article_id!r}")
