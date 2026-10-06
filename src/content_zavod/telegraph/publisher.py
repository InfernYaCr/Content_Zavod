"""TelegraphPublisher: keeps one Страница для чтения per Статья on telegra.ph (#92).

The first ready Версия creates the page; every later Версия (Перегенерация) edits the same
page, so the «📖 Читать» link a Контент-менеджер already has keeps working. The page path is
stored per Статья (`ArticlePages`). The access token comes from `TELEGRAPH_ACCESS_TOKEN`
when set, otherwise an account is created on first use and its token kept in
`owner_settings` - one account for the whole installation.

`publish` never raises: Telegraph is an optional convenience, so any failure is logged and
the caller delivers the Article card without the «Читать» button.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Protocol

from ..domain import ArticleId, ArticleView
from ..settings import SettingsReader
from .client import TelegraphClient, TelegraphError
from .nodes import FooterLink, Node, fit_content, footer_nodes, markdown_to_nodes

logger = logging.getLogger(__name__)

ACCESS_TOKEN_SETTING_KEY = "telegraph_access_token"
DEFAULT_AUTHOR_NAME = "Content Zavod"
_SHORT_NAME = "content-zavod"
_TITLE_LIMIT = 256

FooterLinkSource = Callable[[], Awaitable[FooterLink | None]]


class ArticlePages(Protocol):
    async def get_telegraph_path(self, article_id: ArticleId) -> str | None: ...

    async def set_telegraph_path(self, article_id: ArticleId, path: str) -> None: ...


class SettingsStore(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...


class TelegraphPublisher:
    def __init__(
        self,
        client: TelegraphClient,
        pages: ArticlePages,
        settings: SettingsStore,
        *,
        access_token: str | None = None,
        author_name: str = DEFAULT_AUTHOR_NAME,
        footer_link: FooterLinkSource | None = None,
    ) -> None:
        """`footer_link`, if given, is asked on every publish for a closing link (e.g. the
        customer's project); returning `None` leaves the page without one. The footer is also
        skipped when the text already links that URL - with a Проект set the pipeline ends
        every Статья with a CTA to it (#98), so only Статьи written before the Проект was
        set (or changed) get the footer."""
        self._client = client
        self._pages = pages
        self._settings = settings
        self._access_token = access_token
        self._author_name = author_name
        self._footer_link = footer_link
        self._token_lock = asyncio.Lock()

    async def publish(self, article: ArticleView) -> str | None:
        """Creates or updates the Статья's page and returns its URL, or `None` on failure."""
        try:
            return await self._publish(article)
        except Exception:
            logger.exception("Telegraph publish failed for article_id=%s", article.id)
            return None

    async def _publish(self, article: ArticleView) -> str:
        token = await self._token()
        title = _page_title(article.title)
        content = await self._content(article, title)
        path = await self._pages.get_telegraph_path(article.id)
        if path is not None:
            try:
                page = await self._client.edit_page(
                    token, path, title=title, content=content, author_name=self._author_name
                )
                return page.url
            except TelegraphError:
                # E.g. the token changed and the old page belongs to another account: a fresh
                # page (new URL) beats a card with no «Читать» at all.
                logger.warning(
                    "Telegraph editPage failed for article_id=%s path=%s, creating a new page",
                    article.id,
                    path,
                    exc_info=True,
                )
        page = await self._client.create_page(
            token, title=title, content=content, author_name=self._author_name
        )
        await self._pages.set_telegraph_path(article.id, page.path)
        return page.url

    async def _content(self, article: ArticleView, title: str) -> list[Node]:
        markdown = article.content.decode("utf-8")
        nodes = markdown_to_nodes(markdown, title=title)
        footer = await self._footer_link() if self._footer_link is not None else None
        if footer is not None and _mentions_url(markdown, footer.url):
            # The pipeline already closes the text with a CTA to this exact link (#98);
            # a second copy in the footer would just repeat it on the page.
            footer = None
        return fit_content(nodes, tail=footer_nodes(footer) if footer is not None else None)

    async def _token(self) -> str:
        if self._access_token:
            return self._access_token
        async with self._token_lock:
            if self._access_token:
                return self._access_token
            stored = await self._settings.get(ACCESS_TOKEN_SETTING_KEY)
            if not stored:
                stored = await self._client.create_account(
                    short_name=_SHORT_NAME, author_name=self._author_name
                )
                await self._settings.set(ACCESS_TOKEN_SETTING_KEY, stored)
                logger.info("Created a Telegraph account and stored its token")
            self._access_token = stored
            return stored


def project_footer(settings: SettingsReader) -> FooterLinkSource:
    """The Owner's Проект (#98) as the page's closing link, re-read on every publish so a
    `/set_project` change reaches the next published Версия; `None` while no Проект is set."""

    async def footer() -> FooterLink | None:
        project = (await settings.read()).project
        if project is None:
            return None
        return FooterLink(text=project.description, url=project.url)

    return footer


def _mentions_url(text: str, url: str) -> bool:
    """`url` appears in `text` as a whole link - `https://t.me/name_2` is not `t.me/name`."""
    return re.search(rf"{re.escape(url)}(?![\w/?#=&%~+@-]|[.:]\w)", text) is not None


def _page_title(title: str) -> str:
    title = title.strip() or "Статья"
    return title if len(title) <= _TITLE_LIMIT else title[: _TITLE_LIMIT - 1].rstrip() + "…"
