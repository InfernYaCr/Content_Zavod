from __future__ import annotations

import logging

import pytest

from content_zavod.domain import ArticleId, ArticleView, PlanItemId
from content_zavod.settings import PROJECT_KEY
from content_zavod.telegraph import (
    ACCESS_TOKEN_SETTING_KEY,
    FooterLink,
    TelegraphError,
    TelegraphPage,
    TelegraphPublisher,
    page_url,
    project_footer,
)
from content_zavod.telegraph.nodes import Node
from tests.tenancy import WS_A, WS_B


class FakeTelegraph:
    def __init__(self) -> None:
        self.accounts_created = 0
        self.created: list[tuple[str, str, list[Node]]] = []
        self.edited: list[tuple[str, str, str, list[Node]]] = []
        self.fail_create: Exception | None = None
        self.fail_edit: Exception | None = None
        self._next_page = 0

    async def create_account(self, *, short_name: str, author_name: str) -> str:
        self.accounts_created += 1
        return f"token-{self.accounts_created}"

    async def create_page(
        self, access_token: str, *, title: str, content: list[Node], author_name: str
    ) -> TelegraphPage:
        if self.fail_create is not None:
            raise self.fail_create
        self._next_page += 1
        self.created.append((access_token, title, content))
        path = f"page-{self._next_page}"
        return TelegraphPage(path=path, url=f"https://telegra.ph/{path}")

    async def edit_page(
        self,
        access_token: str,
        path: str,
        *,
        title: str,
        content: list[Node],
        author_name: str,
    ) -> TelegraphPage:
        if self.fail_edit is not None:
            raise self.fail_edit
        self.edited.append((access_token, path, title, content))
        return TelegraphPage(path=path, url=f"https://telegra.ph/{path}")


class FakePages:
    """Workspace A's pages; another Workspace's are kept apart in `other`."""

    def __init__(self) -> None:
        self.paths: dict[str, str] = {}
        self.other: dict[tuple[int, str], str] = {}

    async def get_telegraph_path(self, workspace_id: int, article_id: ArticleId) -> str | None:
        if workspace_id != WS_A:
            return self.other.get((workspace_id, article_id))
        return self.paths.get(article_id)

    async def set_telegraph_path(self, workspace_id: int, article_id: ArticleId, path: str) -> None:
        if workspace_id != WS_A:
            self.other[workspace_id, article_id] = path
        else:
            self.paths[article_id] = path


class FakeSettings:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value


def _article(content: str = "## Раздел\nТекст.", article_id: str = "a1") -> ArticleView:
    return ArticleView(
        id=ArticleId(article_id),
        plan_item_id=PlanItemId("i1"),
        title="Как выбрать CRM",
        platform="zen",
        content=content.encode("utf-8"),
    )


async def test_first_publish_creates_account_and_page_and_stores_both() -> None:
    telegraph, pages, settings = FakeTelegraph(), FakePages(), FakeSettings()
    publisher = TelegraphPublisher(telegraph, pages, settings)

    url = await publisher.publish(WS_A, _article())

    assert url == "https://telegra.ph/page-1"
    assert telegraph.accounts_created == 1
    assert settings.values == {ACCESS_TOKEN_SETTING_KEY: "token-1"}
    assert pages.paths == {"a1": "page-1"}
    token, title, content = telegraph.created[0]
    assert (token, title) == ("token-1", "Как выбрать CRM")
    assert content == [
        {"tag": "h3", "children": ["Раздел"]},
        {"tag": "p", "children": ["Текст."]},
    ]


async def test_regeneration_edits_the_same_page_keeping_the_url() -> None:
    telegraph, pages, settings = FakeTelegraph(), FakePages(), FakeSettings()
    publisher = TelegraphPublisher(telegraph, pages, settings)
    first_url = await publisher.publish(WS_A, _article("Первая версия."))

    second_url = await publisher.publish(WS_A, _article("Вторая версия."))

    assert second_url == first_url
    assert len(telegraph.created) == 1
    (_, path, _, content) = telegraph.edited[0]
    assert path == "page-1"
    assert content == [{"tag": "p", "children": ["Вторая версия."]}]


async def test_stored_token_is_reused_and_no_account_is_created() -> None:
    telegraph, pages, settings = FakeTelegraph(), FakePages(), FakeSettings()
    settings.values[ACCESS_TOKEN_SETTING_KEY] = "stored-token"

    await TelegraphPublisher(telegraph, pages, settings).publish(WS_A, _article())

    assert telegraph.accounts_created == 0
    assert telegraph.created[0][0] == "stored-token"


async def test_env_token_wins_over_the_stored_one() -> None:
    telegraph, pages, settings = FakeTelegraph(), FakePages(), FakeSettings()
    settings.values[ACCESS_TOKEN_SETTING_KEY] = "stored-token"
    publisher = TelegraphPublisher(telegraph, pages, settings, access_token="env-token")

    await publisher.publish(WS_A, _article())

    assert telegraph.created[0][0] == "env-token"
    assert telegraph.accounts_created == 0


async def test_account_is_created_once_across_publishes() -> None:
    telegraph, pages, settings = FakeTelegraph(), FakePages(), FakeSettings()
    publisher = TelegraphPublisher(telegraph, pages, settings)

    await publisher.publish(WS_A, _article(article_id="a1"))
    await publisher.publish(WS_A, _article(article_id="a2"))

    assert telegraph.accounts_created == 1


async def test_failed_edit_falls_back_to_a_new_page() -> None:
    telegraph, pages, settings = FakeTelegraph(), FakePages(), FakeSettings()
    pages.paths["a1"] = "foreign-page"
    telegraph.fail_edit = TelegraphError("editPage: 400 'PAGE_ACCESS_DENIED'")

    url = await TelegraphPublisher(telegraph, pages, settings).publish(WS_A, _article())

    assert url == "https://telegra.ph/page-1"
    assert pages.paths == {"a1": "page-1"}


@pytest.mark.parametrize("error", [TelegraphError("createPage: 500"), RuntimeError("boom")])
async def test_any_failure_returns_none_and_logs(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    telegraph, pages, settings = FakeTelegraph(), FakePages(), FakeSettings()
    telegraph.fail_create = error

    with caplog.at_level(logging.ERROR, logger="content_zavod.telegraph.publisher"):
        url = await TelegraphPublisher(telegraph, pages, settings).publish(WS_A, _article())

    assert url is None
    assert pages.paths == {}
    assert "Telegraph publish failed for article_id=a1" in caplog.text


async def test_footer_link_is_appended_when_the_source_returns_one() -> None:
    telegraph, pages, settings = FakeTelegraph(), FakePages(), FakeSettings()

    async def footer(workspace_id: int) -> FooterLink | None:
        return FooterLink("Наш проект", "https://example.com")

    await TelegraphPublisher(telegraph, pages, settings, footer_link=footer).publish(
        WS_A, _article("Текст.")
    )

    content = telegraph.created[0][2]
    assert content[-2:] == [
        {"tag": "hr"},
        {
            "tag": "p",
            "children": [
                {"tag": "a", "attrs": {"href": "https://example.com"}, "children": ["Наш проект"]}
            ],
        },
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Текст.\n\nПодписывайтесь на наш проект: https://example.com",
        "Текст. [Наш проект](https://example.com).",
    ],
)
async def test_footer_is_skipped_when_the_text_already_links_the_project(text: str) -> None:
    telegraph, pages, settings = FakeTelegraph(), FakePages(), FakeSettings()

    async def footer(workspace_id: int) -> FooterLink | None:
        return FooterLink("Наш проект", "https://example.com")

    await TelegraphPublisher(telegraph, pages, settings, footer_link=footer).publish(
        WS_A, _article(text)
    )

    content = telegraph.created[0][2]
    assert {"tag": "hr"} not in content
    assert str(content).count("'href': 'https://example.com'") == 1


async def test_footer_is_kept_when_the_text_links_only_a_longer_url() -> None:
    telegraph, pages, settings = FakeTelegraph(), FakePages(), FakeSettings()

    async def footer(workspace_id: int) -> FooterLink | None:
        return FooterLink("Наш канал", "https://t.me/name")

    await TelegraphPublisher(telegraph, pages, settings, footer_link=footer).publish(
        WS_A, _article("Соседний канал: https://t.me/name_2")
    )

    assert telegraph.created[0][2][-2] == {"tag": "hr"}


async def test_overlong_title_is_cut_to_telegraph_limit() -> None:
    telegraph, pages, settings = FakeTelegraph(), FakePages(), FakeSettings()
    article = ArticleView(
        id=ArticleId("a1"),
        plan_item_id=PlanItemId("i1"),
        title="Т" * 300,
        platform="zen",
        content=b"x",
    )

    await TelegraphPublisher(telegraph, pages, settings).publish(WS_A, article)

    title = telegraph.created[0][1]
    assert len(title) == 256
    assert title.endswith("…")


class FakeWorkspaceSettings:
    def __init__(self) -> None:
        self.values: dict[tuple[int, str], str] = {}

    async def get(self, workspace_id: int, key: str) -> str | None:
        return self.values.get((workspace_id, key))


async def test_project_footer_links_the_owners_project_and_follows_changes() -> None:
    settings = FakeWorkspaceSettings()
    settings.values[WS_A, PROJECT_KEY] = "https://t.me/channel Наш канал"
    footer = project_footer(settings)

    assert await footer(WS_A) == FooterLink(text="Наш канал", url="https://t.me/channel")
    settings.values[WS_A, PROJECT_KEY] = ""
    assert await footer(WS_A) is None


async def test_project_footer_without_description_gets_a_derived_label() -> None:
    settings = FakeWorkspaceSettings()
    footer = project_footer(settings)

    settings.values[WS_A, PROJECT_KEY] = "https://t.me/channel"
    assert await footer(WS_A) == FooterLink(
        text="Telegram-канал автора", url="https://t.me/channel"
    )
    settings.values[WS_A, PROJECT_KEY] = "https://example.ru"
    assert await footer(WS_A) == FooterLink(text="сайт автора", url="https://example.ru")


async def test_project_footer_is_the_articles_own_workspaces_project() -> None:
    settings = FakeWorkspaceSettings()
    settings.values[WS_A, PROJECT_KEY] = "https://a.example Проект A"
    settings.values[WS_B, PROJECT_KEY] = "https://b.example Проект B"
    telegraph = FakeTelegraph()

    await TelegraphPublisher(
        telegraph, FakePages(), FakeSettings(), footer_link=project_footer(settings)
    ).publish(WS_B, _article("Текст."))

    content = str(telegraph.created[0][2])
    assert "https://b.example" in content
    assert "https://a.example" not in content


async def test_a_page_of_another_workspace_is_never_edited() -> None:
    telegraph, pages, settings = FakeTelegraph(), FakePages(), FakeSettings()
    pages.paths["a1"] = "page-of-a"

    await TelegraphPublisher(telegraph, pages, settings).publish(WS_B, _article())

    assert telegraph.edited == []
    assert pages.paths == {"a1": "page-of-a"}
    assert pages.other == {(WS_B, "a1"): "page-1"}


def test_page_url_is_built_from_the_stored_path() -> None:
    assert page_url("Statya-10-07") == "https://telegra.ph/Statya-10-07"
