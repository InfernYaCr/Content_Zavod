from datetime import UTC, datetime

import pytest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from content_zavod.telegram import (
    ArticleId,
    ArticleSummary,
    ArticleVersionSummary,
    ArticleVersionView,
    ArticleView,
    ExportArticle,
    HistoryVersion,
    HistoryVersions,
    HistoryWeek,
    Page,
    PlanId,
    PlanItemId,
    PlanItemView,
    PlanSummary,
    PlanView,
    SimpleAction,
    TelegramCommentPrompt,
    TelegramGateway,
    decode_callback_data,
)
from content_zavod.telegram.gateway import (
    ITEMS_PER_PAGE,
    MESSAGE_LIMIT,
    build_history_articles_keyboard,
    build_history_version_keyboard,
    build_history_versions_keyboard,
    build_history_weeks_keyboard,
    build_plan_keyboard,
    chunk_text,
    format_week_range,
    render_history_articles_text,
    render_history_version_text,
    render_history_versions_text,
    render_history_weeks_text,
    render_plan_text,
)


class FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str, InlineKeyboardMarkup | None]] = []
        self.sent_documents: list[tuple[int, BufferedInputFile, str | None]] = []
        self.sent_photos: list[tuple[int, BufferedInputFile, str | None]] = []
        self.edited_messages: list[tuple[int, int, str, InlineKeyboardMarkup | None]] = []

    async def send_message(self, chat_id, text, reply_markup=None) -> int:
        self.sent_messages.append((chat_id, text, reply_markup))
        return len(self.sent_messages)

    async def send_document(self, chat_id, document, caption=None) -> None:
        self.sent_documents.append((chat_id, document, caption))

    async def send_photo(self, chat_id, photo, caption=None) -> None:
        self.sent_photos.append((chat_id, photo, caption))

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None) -> None:
        self.edited_messages.append((chat_id, message_id, text, reply_markup))

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None) -> None:
        self.edited_messages.append((chat_id, message_id, "", reply_markup))

    async def set_my_commands(self, commands, *, scope) -> None:
        pass


def make_plan(item_count: int = 2) -> PlanView:
    items = [
        PlanItemView(id=PlanItemId(f"item-{i}"), title=f"Тема {i}", status="draft")
        for i in range(item_count)
    ]
    return PlanView(id=PlanId("plan-1"), week_label="2026-W32", items=items)


@pytest.mark.asyncio
async def test_send_plan_sends_single_message_with_keyboard() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await gateway.send_plan(chat_id=123, plan=make_plan())

    assert len(bot.sent_messages) == 1
    chat_id, text, keyboard = bot.sent_messages[0]
    assert chat_id == 123
    assert "Тема 0" in text and "Тема 1" in text
    assert keyboard is not None
    # 2 item rows (regenerate + delete) + 1 approve_all row
    assert len(keyboard.inline_keyboard) == 3


@pytest.mark.asyncio
async def test_send_plan_keyboard_callback_data_round_trips() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)
    plan = make_plan(1)

    await gateway.send_plan(chat_id=1, plan=plan)

    _, _, keyboard = bot.sent_messages[0]
    regenerate_button, delete_button = keyboard.inline_keyboard[0]
    assert decode_callback_data(regenerate_button.callback_data) == SimpleAction(
        "regenerate", "item-0"
    )
    assert decode_callback_data(delete_button.callback_data) == SimpleAction("delete", "item-0")
    (approve_button,) = keyboard.inline_keyboard[1]
    assert decode_callback_data(approve_button.callback_data) == SimpleAction(
        "approve_all", "plan-1"
    )


@pytest.mark.asyncio
async def test_send_plan_sends_a_single_message_even_for_many_items() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)
    plan = make_plan(item_count=40)  # many items, paginated instead of split into messages

    await gateway.send_plan(chat_id=1, plan=plan)

    assert len(bot.sent_messages) == 1
    _, text, keyboard = bot.sent_messages[0]
    assert len(text) <= MESSAGE_LIMIT
    assert keyboard is not None


def test_build_plan_keyboard_paginates_beyond_page_size() -> None:
    plan = make_plan(item_count=ITEMS_PER_PAGE + 3)

    keyboard = build_plan_keyboard(plan, page=0)

    # ITEMS_PER_PAGE item rows + a Next-only nav row + approve_all row
    assert len(keyboard.inline_keyboard) == ITEMS_PER_PAGE + 2
    nav_row = keyboard.inline_keyboard[ITEMS_PER_PAGE]
    assert len(nav_row) == 1
    assert decode_callback_data(nav_row[0].callback_data) == Page("plan-1", 1)


def test_build_plan_keyboard_last_page_only_shows_back_button() -> None:
    plan = make_plan(item_count=ITEMS_PER_PAGE + 3)

    keyboard = build_plan_keyboard(plan, page=1)

    remaining_items = 3
    nav_row = keyboard.inline_keyboard[remaining_items]
    assert len(nav_row) == 1
    assert decode_callback_data(nav_row[0].callback_data) == Page("plan-1", 0)


def test_build_plan_keyboard_single_page_has_no_nav_row() -> None:
    plan = make_plan(item_count=2)

    keyboard = build_plan_keyboard(plan, page=0)

    assert len(keyboard.inline_keyboard) == 3  # 2 item rows + approve_all, no nav row


def test_render_plan_text_shows_absolute_item_numbers_across_pages() -> None:
    plan = make_plan(item_count=ITEMS_PER_PAGE + 3)

    text = render_plan_text(plan, page=1)

    assert f"{ITEMS_PER_PAGE + 1}." in text
    assert f"{ITEMS_PER_PAGE + 3}." in text
    assert "\n1. " not in text


def test_render_plan_text_shows_date_range_not_week_label() -> None:
    plan = make_plan(item_count=1)

    text = render_plan_text(plan)

    assert "2026-W32" not in text
    assert "3–9 августа 2026" in text


def test_format_week_range_within_single_month() -> None:
    assert format_week_range("2026-W32") == "3–9 августа 2026"


def test_format_week_range_spanning_two_months() -> None:
    assert format_week_range("2026-W31") == "27 июля – 2 августа 2026"


def test_format_week_range_spanning_year_boundary() -> None:
    assert format_week_range("2026-W01") == "29 декабря 2025 – 4 января 2026"


@pytest.mark.asyncio
async def test_edit_plan_calls_edit_message_text_with_rendered_page() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)
    plan = make_plan(1)

    await gateway.edit_plan(chat_id=1, message_id=99, plan=plan, page=0)

    assert len(bot.edited_messages) == 1
    chat_id, message_id, text, keyboard = bot.edited_messages[0]
    assert (chat_id, message_id) == (1, 99)
    assert "Тема 0" in text
    assert keyboard is not None


@pytest.mark.asyncio
async def test_edit_notice_calls_edit_message_text_with_plain_text() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await gateway.edit_notice(chat_id=1, message_id=99, text="⏳ Генерирую...")

    assert bot.edited_messages == [(1, 99, "⏳ Генерирую...", None)]


@pytest.mark.asyncio
async def test_send_message_returns_the_sent_message_id() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    message_id = await gateway.send_message(chat_id=1, text="hi")

    assert message_id == 1
    assert bot.sent_messages == [(1, "hi", None)]


@pytest.mark.asyncio
async def test_send_error_with_retry_attaches_retry_keyboard() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await gateway.send_error_with_retry(chat_id=1, text="failed", job_id=42)

    assert len(bot.sent_messages) == 1
    _, _, keyboard = bot.sent_messages[0]
    (retry_button,) = keyboard.inline_keyboard[0]
    assert decode_callback_data(retry_button.callback_data) == SimpleAction("retry", "42")


def make_article() -> ArticleView:
    return ArticleView(
        id=ArticleId("article-1"),
        plan_item_id=PlanItemId("item-1"),
        title="Best Niche Guide",
        platform="zen",
        content=b"Hello, world.",
    )


@pytest.mark.asyncio
async def test_send_article_ready_sends_no_document_only_format_choice() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await gateway.send_article_ready(chat_id=42, article=make_article())

    assert bot.sent_documents == []
    assert len(bot.sent_messages) == 1
    chat_id, text, _ = bot.sent_messages[0]
    assert chat_id == 42
    assert "Best Niche Guide" in text
    assert "zen" in text


@pytest.mark.asyncio
async def test_send_article_ready_attaches_export_regenerate_and_approve_keyboard() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await gateway.send_article_ready(chat_id=42, article=make_article())

    assert len(bot.sent_messages) == 1
    chat_id, _, keyboard = bot.sent_messages[0]
    assert chat_id == 42
    (docx_button, md_button) = keyboard.inline_keyboard[0]
    (regenerate_button, approve_button) = keyboard.inline_keyboard[1]
    assert decode_callback_data(docx_button.callback_data) == ExportArticle("article-1", "docx")
    assert decode_callback_data(md_button.callback_data) == ExportArticle("article-1", "md")
    assert decode_callback_data(regenerate_button.callback_data) == SimpleAction(
        "regenerate_article", "article-1"
    )
    assert decode_callback_data(approve_button.callback_data) == SimpleAction(
        "approve", "article-1"
    )
    (cover_button,) = keyboard.inline_keyboard[2]
    assert decode_callback_data(cover_button.callback_data) == SimpleAction(
        "request_cover", "item-1"
    )


@pytest.mark.asyncio
async def test_send_cover_sends_photo_with_filename_and_caption() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await gateway.send_cover(
        chat_id=42, image=b"fake jpeg bytes", mime_type="image/jpeg", title="Topic A"
    )

    assert len(bot.sent_photos) == 1
    chat_id, photo, caption = bot.sent_photos[0]
    assert chat_id == 42
    assert isinstance(photo, BufferedInputFile)
    assert photo.filename == "cover.jpeg"
    assert photo.data == b"fake jpeg bytes"
    assert "Topic A" in caption


@pytest.mark.asyncio
async def test_send_article_document_sends_docx_with_built_filename_and_caption() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await gateway.send_article_document(chat_id=42, article=make_article(), article_format="docx")

    assert len(bot.sent_documents) == 1
    chat_id, document, caption = bot.sent_documents[0]
    assert chat_id == 42
    assert isinstance(document, BufferedInputFile)
    assert document.filename == "best-niche-guide-zen.docx"
    assert "Best Niche Guide" in caption
    assert "zen" in caption


@pytest.mark.asyncio
async def test_send_article_document_sends_md_as_plain_text_content() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await gateway.send_article_document(chat_id=42, article=make_article(), article_format="md")

    assert len(bot.sent_documents) == 1
    _, document, _ = bot.sent_documents[0]
    assert document.filename == "best-niche-guide-zen.md"
    assert document.data == b"Hello, world."


@pytest.mark.asyncio
async def test_send_error_chunks_long_text() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)
    long_text = "\n".join(f"line {i}" for i in range(1000))

    await gateway.send_error(chat_id=1, text=long_text)

    assert len(bot.sent_messages) > 1
    for _, text, keyboard in bot.sent_messages:
        assert len(text) <= MESSAGE_LIMIT
        assert keyboard is None


def make_plan_summaries(count: int) -> list[PlanSummary]:
    return [
        PlanSummary(id=PlanId(f"plan-{i}"), week_label="2026-W32", status="pending_review")
        for i in range(count)
    ]


def test_render_history_weeks_text_lists_week_range_and_status() -> None:
    text = render_history_weeks_text(make_plan_summaries(1), page=0, page_count=1)

    assert "3–9 августа 2026 — pending_review" in text


def test_render_history_weeks_text_empty_page() -> None:
    text = render_history_weeks_text([], page=0, page_count=1)

    assert "Планов пока нет." in text


def test_build_history_weeks_keyboard_one_button_per_week() -> None:
    keyboard = build_history_weeks_keyboard(make_plan_summaries(2), page=0, page_count=1)

    assert len(keyboard.inline_keyboard) == 2
    payload = decode_callback_data(keyboard.inline_keyboard[0][0].callback_data)
    assert payload == HistoryWeek("plan-0", 0)


def test_build_history_weeks_keyboard_paginates() -> None:
    keyboard = build_history_weeks_keyboard(make_plan_summaries(1), page=0, page_count=2)

    nav_row = keyboard.inline_keyboard[1]
    assert len(nav_row) == 1
    assert decode_callback_data(nav_row[0].callback_data) == SimpleAction("history_page", "1")


def test_render_history_articles_text_shows_every_status_untranslated() -> None:
    plan_summary = PlanSummary(id=PlanId("plan-1"), week_label="2026-W32", status="approved")
    articles = [
        ArticleSummary(id=ArticleId("a-1"), title="Topic A", platform="zen", status="queued"),
        ArticleSummary(id=ArticleId("a-2"), title="Topic A", platform="vc", status="ready"),
    ]

    text = render_history_articles_text(plan_summary, articles)

    assert "Topic A (zen) — queued" in text
    assert "Topic A (vc) — ready" in text


def test_build_history_articles_keyboard_back_button_returns_to_the_given_page() -> None:
    keyboard = build_history_articles_keyboard([], back_page=3)

    (back_button,) = keyboard.inline_keyboard[0]
    assert decode_callback_data(back_button.callback_data) == SimpleAction("history_page", "3")


def test_build_history_articles_keyboard_adds_no_row_for_articles_with_no_version_yet() -> None:
    articles = [
        ArticleSummary(id=ArticleId("a-1"), title="Topic A", platform="zen", status="queued"),
        ArticleSummary(id=ArticleId("a-2"), title="Topic A", platform="vc", status="generating"),
    ]

    keyboard = build_history_articles_keyboard(articles, back_page=0)

    # No rows for a Статья with no recorded Версия yet - just the trailing "Назад" row.
    assert len(keyboard.inline_keyboard) == 1


def test_build_history_articles_keyboard_error_status_gets_a_versions_only_row() -> None:
    """`error` isn't downloadable (no *last* ready Версия), but a prior successful Версия
    could still exist from before a later regeneration failed - so it still gets a "Версии"
    row, just without the export buttons (#26)."""
    articles = [
        ArticleSummary(id=ArticleId("a-1"), title="Topic A", platform="zen", status="error")
    ]

    keyboard = build_history_articles_keyboard(articles, back_page=0)

    assert len(keyboard.inline_keyboard) == 2
    (versions_button,) = keyboard.inline_keyboard[0]
    assert decode_callback_data(versions_button.callback_data) == HistoryVersions("a-1", 0)


def test_build_history_articles_keyboard_adds_a_download_row_per_article_with_content() -> None:
    articles = [
        ArticleSummary(id=ArticleId("a-1"), title="Topic A", platform="zen", status="ready"),
        ArticleSummary(id=ArticleId("a-2"), title="Topic B", platform="vc", status="regenerating"),
        ArticleSummary(id=ArticleId("a-3"), title="Topic C", platform="zen", status="exported"),
    ]

    keyboard = build_history_articles_keyboard(articles, back_page=1)

    # 3 download rows + trailing "Назад" row.
    assert len(keyboard.inline_keyboard) == 4
    docx_button, md_button, versions_button = keyboard.inline_keyboard[0]
    assert decode_callback_data(docx_button.callback_data) == ExportArticle("a-1", "docx")
    assert decode_callback_data(md_button.callback_data) == ExportArticle("a-1", "md")
    assert decode_callback_data(versions_button.callback_data) == HistoryVersions("a-1", 1)
    docx_button, md_button, versions_button = keyboard.inline_keyboard[1]
    assert decode_callback_data(docx_button.callback_data) == ExportArticle("a-2", "docx")
    docx_button, md_button, versions_button = keyboard.inline_keyboard[2]
    assert decode_callback_data(docx_button.callback_data) == ExportArticle("a-3", "docx")
    (back_button,) = keyboard.inline_keyboard[3]
    assert decode_callback_data(back_button.callback_data) == SimpleAction("history_page", "1")


@pytest.mark.asyncio
async def test_send_history_weeks_sends_a_single_message_with_keyboard() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await gateway.send_history_weeks(
        chat_id=1, plans_page=make_plan_summaries(1), page=0, page_count=1
    )

    assert len(bot.sent_messages) == 1
    chat_id, text, keyboard = bot.sent_messages[0]
    assert chat_id == 1
    assert "pending_review" in text
    assert keyboard is not None


@pytest.mark.asyncio
async def test_edit_history_weeks_edits_the_message() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await gateway.edit_history_weeks(
        chat_id=1, message_id=9, plans_page=make_plan_summaries(1), page=0, page_count=1
    )

    assert len(bot.edited_messages) == 1
    chat_id, message_id, _text, keyboard = bot.edited_messages[0]
    assert (chat_id, message_id) == (1, 9)
    assert keyboard is not None


@pytest.mark.asyncio
async def test_edit_history_articles_edits_the_message_with_a_back_button() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)
    plan_summary = PlanSummary(id=PlanId("plan-1"), week_label="2026-W32", status="approved")
    articles = [
        ArticleSummary(id=ArticleId("a-1"), title="Topic A", platform="zen", status="queued")
    ]

    await gateway.edit_history_articles(
        chat_id=1, message_id=9, plan_summary=plan_summary, articles=articles, back_page=2
    )

    chat_id, message_id, text, keyboard = bot.edited_messages[0]
    assert (chat_id, message_id) == (1, 9)
    assert "Topic A (zen) — queued" in text
    (back_button,) = keyboard.inline_keyboard[0]
    assert decode_callback_data(back_button.callback_data) == SimpleAction("history_page", "2")


@pytest.mark.asyncio
async def test_edit_history_articles_adds_a_download_row_for_a_ready_article() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)
    plan_summary = PlanSummary(id=PlanId("plan-1"), week_label="2026-W32", status="approved")
    articles = [
        ArticleSummary(id=ArticleId("a-1"), title="Topic A", platform="zen", status="ready")
    ]

    await gateway.edit_history_articles(
        chat_id=1, message_id=9, plan_summary=plan_summary, articles=articles, back_page=2
    )

    _, _, _, keyboard = bot.edited_messages[0]
    docx_button, md_button, versions_button = keyboard.inline_keyboard[0]
    assert decode_callback_data(docx_button.callback_data) == ExportArticle("a-1", "docx")
    assert decode_callback_data(md_button.callback_data) == ExportArticle("a-1", "md")
    assert decode_callback_data(versions_button.callback_data) == HistoryVersions("a-1", 2)
    (back_button,) = keyboard.inline_keyboard[1]
    assert decode_callback_data(back_button.callback_data) == SimpleAction("history_page", "2")


def test_chunk_text_returns_single_chunk_when_under_limit() -> None:
    assert chunk_text("hello") == ["hello"]


def test_chunk_text_splits_on_newline_boundary() -> None:
    text = ("a" * 4093 + "\n") + "tail"
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert chunks[0] == "a" * 4093
    assert chunks[1] == "tail"


@pytest.mark.asyncio
async def test_comment_prompt_sends_message_with_skip_button_encoding_regenerate() -> None:
    bot = FakeBot()
    prompt = TelegramCommentPrompt(bot)

    await prompt.prompt_for_comment(chat_id=1, id_="item-1")

    assert len(bot.sent_messages) == 1
    chat_id, _, keyboard = bot.sent_messages[0]
    assert chat_id == 1
    (skip_button,) = keyboard.inline_keyboard[0]
    assert decode_callback_data(skip_button.callback_data) == SimpleAction("regenerate", "item-1")


@pytest.mark.asyncio
async def test_comment_prompt_with_article_action_encodes_regenerate_article_on_skip() -> None:
    bot = FakeBot()
    prompt = TelegramCommentPrompt(bot, action="regenerate_article")

    await prompt.prompt_for_comment(chat_id=1, id_="article-1")

    (skip_button,) = bot.sent_messages[0][2].inline_keyboard[0]
    assert decode_callback_data(skip_button.callback_data) == SimpleAction(
        "regenerate_article", "article-1"
    )


def make_article_summary() -> ArticleSummary:
    return ArticleSummary(id=ArticleId("a-1"), title="Topic A", platform="zen", status="ready")


def make_version_summary(id_: int = 2, model: str = "yandexgpt") -> ArticleVersionSummary:
    return ArticleVersionSummary(
        id=id_,
        model=model,
        tokens=42,
        cost=0.0123,
        created_at=datetime(2026, 8, 11, 14, 3, tzinfo=UTC),
    )


def make_version_view(content: str = "Hello, world.") -> ArticleVersionView:
    return ArticleVersionView(
        id=2,
        content=content,
        model="yandexgpt",
        tokens=42,
        cost=0.0123,
        created_at=datetime(2026, 8, 11, 14, 3, tzinfo=UTC),
    )


def test_render_history_versions_text_lists_every_version_newest_first() -> None:
    text = render_history_versions_text(
        make_article_summary(),
        [make_version_summary(id_=2, model="yandexgpt-2"), make_version_summary(id_=1)],
    )

    assert "Topic A (zen)" in text
    assert "1. 11.08.2026 14:03 — yandexgpt-2, 42 ток." in text
    assert "2. 11.08.2026 14:03 — yandexgpt, 42 ток." in text


def test_render_history_versions_text_empty() -> None:
    text = render_history_versions_text(make_article_summary(), [])

    assert "Версий пока нет." in text


def test_build_history_versions_keyboard_one_button_per_version_and_a_back_button() -> None:
    versions = [make_version_summary(id_=2), make_version_summary(id_=1)]

    keyboard = build_history_versions_keyboard("article-1", "plan-1", versions, back_page=3)

    assert len(keyboard.inline_keyboard) == 3
    payload = decode_callback_data(keyboard.inline_keyboard[0][0].callback_data)
    assert payload == HistoryVersion("article-1", 2, 3)
    payload = decode_callback_data(keyboard.inline_keyboard[1][0].callback_data)
    assert payload == HistoryVersion("article-1", 1, 3)
    (back_button,) = keyboard.inline_keyboard[2]
    assert decode_callback_data(back_button.callback_data) == HistoryWeek("plan-1", 3)


def test_render_history_version_text_shows_header_and_content() -> None:
    text = render_history_version_text(make_article_summary(), make_version_view())

    assert "Topic A (zen)" in text
    assert "11.08.2026 14:03 — yandexgpt, 42 ток." in text
    assert text.endswith("Hello, world.")


def test_render_history_version_text_truncates_content_over_the_message_limit() -> None:
    version = make_version_view(content="x" * (MESSAGE_LIMIT * 2))

    text = render_history_version_text(make_article_summary(), version)

    assert len(text) <= MESSAGE_LIMIT
    assert "обрезано" in text


def test_build_history_version_keyboard_back_button_returns_to_the_versions_list() -> None:
    keyboard = build_history_version_keyboard("article-1", back_page=3)

    (back_button,) = keyboard.inline_keyboard[0]
    assert decode_callback_data(back_button.callback_data) == HistoryVersions("article-1", 3)


@pytest.mark.asyncio
async def test_edit_history_versions_edits_the_message_with_version_buttons() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)
    versions = [make_version_summary()]

    await gateway.edit_history_versions(
        chat_id=1,
        message_id=9,
        article=make_article_summary(),
        plan_id="plan-1",
        versions=versions,
        back_page=2,
    )

    chat_id, message_id, text, keyboard = bot.edited_messages[0]
    assert (chat_id, message_id) == (1, 9)
    assert "Topic A (zen)" in text
    payload = decode_callback_data(keyboard.inline_keyboard[0][0].callback_data)
    assert payload == HistoryVersion("a-1", 2, 2)
    (back_button,) = keyboard.inline_keyboard[1]
    assert decode_callback_data(back_button.callback_data) == HistoryWeek("plan-1", 2)


@pytest.mark.asyncio
async def test_edit_history_version_edits_the_message_with_the_versions_content() -> None:
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await gateway.edit_history_version(
        chat_id=1,
        message_id=9,
        article=make_article_summary(),
        version=make_version_view(),
        back_page=2,
    )

    chat_id, message_id, text, keyboard = bot.edited_messages[0]
    assert (chat_id, message_id) == (1, 9)
    assert text.endswith("Hello, world.")
    (back_button,) = keyboard.inline_keyboard[0]
    assert decode_callback_data(back_button.callback_data) == HistoryVersions("a-1", 2)
