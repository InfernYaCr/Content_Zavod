"""Callback_data serialization only - see ADR-0011.

Payload is a union of six types: five immutable dataclasses for the composite
Действия that pack more than one field into their id (`Page`, `HistoryWeek`,
`HistoryVersions`, `HistoryVersion`, `ExportArticle`), plus `SimpleAction` for
the remaining fifteen Действия that carry a single opaque id.

The wire format (Action code + ":" separator + packed fields, 64-byte limit)
matches `gateway.py`'s existing codec byte-for-byte, so buttons already sent
to Telegram keep decoding the same way. `gateway.py` itself is untouched -
its thirteen functions keep working; nothing routes through this module yet
(that swap is out of scope for #61/#62).
"""

from __future__ import annotations

from dataclasses import dataclass

from .gateway import CALLBACK_DATA_LIMIT, Action
from .types import ArticleFormat

# Must stay identical to gateway.py's _ACTION_CODES - both encode the same
# twenty Действия to the same wire format.
_ACTION_CODES: dict[Action, str] = {
    "delete": "d",
    "regenerate": "r",
    "approve_all": "a",
    "regenerate_article": "ar",
    "request_cover": "cv",
    "approve": "p",
    "export_article": "ex",
    "page": "pg",
    "confirm_regenerate_plan": "cy",
    "cancel_regenerate_plan": "cn",
    "retry": "rt",
    "request_access": "ra",
    "approve_join": "aj",
    "decline_join": "dj",
    "remove_member": "rm",
    "history_page": "hp",
    "history_week": "hw",
    "history_versions": "hv",
    "history_version": "hd",
    "persona_template": "pt",
}
_CODE_ACTIONS: dict[str, Action] = {code: action for action, code in _ACTION_CODES.items()}

# The five composite Действия each get their own type below - see them out
# individually in _ACTION_CODES rather than by iterating, so a typo in one
# doesn't silently swallow another.
_PAGE_CODE = _ACTION_CODES["page"]
_HISTORY_WEEK_CODE = _ACTION_CODES["history_week"]
_HISTORY_VERSIONS_CODE = _ACTION_CODES["history_versions"]
_HISTORY_VERSION_CODE = _ACTION_CODES["history_version"]
_EXPORT_ARTICLE_CODE = _ACTION_CODES["export_article"]


@dataclass(frozen=True)
class Page:
    """`page`'s callback: which Plan and which page of it to show."""

    plan_id: str
    page: int


@dataclass(frozen=True)
class HistoryWeek:
    """`history_week`'s callback: which Plan, and the week-list page to
    return to once this Plan's article list is left."""

    plan_id: str
    page: int


@dataclass(frozen=True)
class HistoryVersions:
    """`history_versions`'s callback: which Статья, and the week-list page to
    return to once the whole (versions -> article list -> week list) back
    chain unwinds."""

    article_id: str
    back_page: int


@dataclass(frozen=True)
class HistoryVersion:
    """`history_version`'s callback: which Версия of which Статья, plus the
    same week-list return page as `HistoryVersions`."""

    article_id: str
    version_id: int
    back_page: int


@dataclass(frozen=True)
class ExportArticle:
    """`export_article`'s callback: which Статья and which export format."""

    article_id: str
    article_format: ArticleFormat


@dataclass(frozen=True)
class SimpleAction:
    """Shared payload for the fifteen Действия with a single opaque id.
    Carries `action` because one type covers fifteen different Действия -
    without this field they wouldn't be distinguishable."""

    action: Action
    id_: str


CallbackPayload = (
    Page | HistoryWeek | HistoryVersions | HistoryVersion | ExportArticle | SimpleAction
)


def _encode(action: Action, id_: str) -> str:
    data = f"{_ACTION_CODES[action]}:{id_}"
    if len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT:
        raise ValueError(f"callback_data exceeds {CALLBACK_DATA_LIMIT} bytes: {data!r}")
    return data


def encode_callback_data(payload: CallbackPayload) -> str:
    if isinstance(payload, Page):
        return _encode("page", f"{payload.plan_id}:{payload.page}")
    if isinstance(payload, HistoryWeek):
        return _encode("history_week", f"{payload.plan_id}:{payload.page}")
    if isinstance(payload, HistoryVersions):
        return _encode("history_versions", f"{payload.article_id}:{payload.back_page}")
    if isinstance(payload, HistoryVersion):
        return _encode(
            "history_version",
            f"{payload.article_id}:{payload.version_id}:{payload.back_page}",
        )
    if isinstance(payload, ExportArticle):
        return _encode("export_article", f"{payload.article_id}:{payload.article_format}")
    return _encode(payload.action, payload.id_)


def decode_callback_data(data: str) -> CallbackPayload:
    code, separator, id_ = data.partition(":")
    if not separator or code not in _CODE_ACTIONS:
        raise ValueError(f"unrecognized callback_data: {data!r}")
    if code == _PAGE_CODE:
        plan_id, _, page = id_.rpartition(":")
        return Page(plan_id, int(page))
    if code == _HISTORY_WEEK_CODE:
        plan_id, _, page = id_.rpartition(":")
        return HistoryWeek(plan_id, int(page))
    if code == _HISTORY_VERSIONS_CODE:
        article_id, _, back_page = id_.rpartition(":")
        return HistoryVersions(article_id, int(back_page))
    if code == _HISTORY_VERSION_CODE:
        rest, _, back_page = id_.rpartition(":")
        article_id, _, version_id = rest.rpartition(":")
        return HistoryVersion(article_id, int(version_id), int(back_page))
    if code == _EXPORT_ARTICLE_CODE:
        article_id, format_separator, article_format = id_.rpartition(":")
        if not format_separator or article_format not in ("docx", "md"):
            raise ValueError(f"unrecognized callback_data: {data!r}")
        return ExportArticle(article_id, article_format)  # type: ignore[arg-type]
    return SimpleAction(_CODE_ACTIONS[code], id_)
