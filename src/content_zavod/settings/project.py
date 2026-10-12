"""Проект: the Owner's Telegram channel or site that a Статья's closing invitation
leads to (#98) - one link plus an optional short description of what is there (#133).

Optional: with nothing stored, `OwnerSettings.project` is `None` and Статьи
are generated exactly as before. Stored as `"<url> <description>"`, or just
`"<url>"` without a description - a normalized URL never contains whitespace, so
the first space is an unambiguous separator and no JSON is needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

PROJECT_KEY = "project"
# `/set_project -` removes the Проект; stored as an empty value, which reads back as `None`.
CLEAR_PROJECT = "-"

_TELEGRAM_USERNAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")
_TELEGRAM_HOSTS = frozenset({"t.me", "www.t.me", "telegram.me", "www.telegram.me"})
_SCHEME_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*)://")
# Dotted labels ending in a letters-only TLD (`ru`, `рф`) or a punycode one - so `1.5` or
# `localhost` never pass for a site. `\w` is Unicode, so Cyrillic domains are fine.
_HOST_RE = re.compile(r"(?:[^\W_](?:[\w-]*[^\W_])?\.)+(?:[^\W\d_]{2,}|xn--[a-z0-9-]+)")
# What people wrap a pasted link in, and the sentence punctuation that sticks to its end.
_WRAPPING = " \t\n\"'<>«»“”„"
_TRAILING = _WRAPPING + ".,;)"


@dataclass(frozen=True, slots=True)
class Project:
    url: str
    description: str = ""

    @property
    def kind(self) -> str:
        """`telegram` or `site` - so the closing sentence says «в канале» or «на сайте»."""
        return "telegram" if self.url.startswith("https://t.me/") else "site"

    @property
    def label(self) -> str:
        """The description, or a neutral one derived from the link when there is none."""
        if self.description:
            return self.description
        return "Telegram-канал автора" if self.kind == "telegram" else "сайт автора"


def normalize_project_url(value: str) -> str:
    """Any common spelling of a channel or site link -> one canonical `https://` URL:
    `@name`, `t.me/name`, `telegram.me/name`, `t.me/s/name` -> `https://t.me/name`;
    `example.ru`, `http://...` -> `https://...`, host lowercased, a trailing `/` dropped.
    Raises `ValueError` on anything else, so a typo never reaches a Статья as a dead link."""

    value = value.lstrip(_WRAPPING).rstrip(_TRAILING)
    if value.startswith("@"):
        if not _TELEGRAM_USERNAME_RE.fullmatch(value[1:]):
            raise ValueError(value)
        return f"https://t.me/{value[1:]}"
    rest = value
    if scheme := _SCHEME_RE.match(value):
        if scheme.group(1).lower() not in ("http", "https"):
            raise ValueError(value)
        rest = value[scheme.end() :]
    parts = urlsplit(f"https://{rest}")
    host = parts.hostname or ""
    if (
        any(char.isspace() for char in value)
        or parts.username is not None
        or parts.port is not None
        or not _HOST_RE.fullmatch(host)
    ):
        raise ValueError(value)
    path = parts.path
    if host in _TELEGRAM_HOSTS:
        host = "t.me"
        if path.startswith("/s/"):  # the channel's web preview, same channel
            path = path[2:]
        if not path.strip("/"):
            raise ValueError(value)  # `t.me` alone leads nowhere - the channel name is missing
    return urlunsplit(("https", host, path.removesuffix("/"), parts.query, parts.fragment))


def serialize_project(project: Project) -> str:
    return f"{project.url} {project.description}".rstrip()


def parse_stored_project(value: str | None) -> Project | None:
    url, _, description = (value or "").partition(" ")
    if not url:
        return None
    return Project(url=url, description=description.strip())


def project_detail_text(project: Project | None) -> str:
    """Shared by `/project` and `/settings` so the two can't drift apart."""

    if project is None:
        return "не задан"
    return f"{project.url} — {project.description}" if project.description else project.url
