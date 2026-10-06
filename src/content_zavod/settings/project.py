"""Проект: the Owner's Telegram channel or site that a Статья's closing CTA
leads to (#98) - one link plus a short description of what is there.

Optional: with nothing stored, `OwnerSettings.project` is `None` and Статьи
are generated exactly as before. Stored as `"<url> <description>"` - a
normalized URL never contains whitespace, so the first space is an
unambiguous separator and no JSON is needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

PROJECT_KEY = "project"
# `/set_project -` removes the Проект; stored as an empty value, which reads back as `None`.
CLEAR_PROJECT = "-"

_TELEGRAM_USERNAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")


@dataclass(frozen=True, slots=True)
class Project:
    url: str
    description: str


def normalize_project_url(value: str) -> str:
    """`@name` and `t.me/name` become `https://t.me/name`; anything else must
    already be an `https://` URL with a dotted host. Raises `ValueError` on
    anything else, so a typo never reaches a Статья as a dead link."""

    value = value.strip()
    if value.startswith("@"):
        if not _TELEGRAM_USERNAME_RE.fullmatch(value[1:]):
            raise ValueError(value)
        return f"https://t.me/{value[1:]}"
    # Phone keyboards capitalize the first letter: `T.me/name`, `Https://...` are the same link.
    if value[:5].lower() == "t.me/":
        value = f"https://t.me/{value[5:]}"
    elif value[:8].lower() == "https://":
        value = f"https://{value[8:]}"
    parts = urlsplit(value) if value.startswith("https://") else None
    host = parts.hostname if parts else None
    if not host or "." not in host or any(char.isspace() for char in value):
        raise ValueError(value)
    if parts is not None and host == "t.me" and not parts.path.strip("/"):
        raise ValueError(value)  # `t.me` alone leads nowhere - the channel name is missing
    return value


def serialize_project(project: Project) -> str:
    return f"{project.url} {project.description}"


def parse_stored_project(value: str | None) -> Project | None:
    url, _, description = (value or "").partition(" ")
    if not url or not description.strip():
        return None
    return Project(url=url, description=description.strip())


def project_detail_text(project: Project | None) -> str:
    """Shared by `/project` and `/settings` so the two can't drift apart."""

    if project is None:
        return "не задан"
    return f"{project.url} — {project.description}"
