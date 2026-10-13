"""The Хаб (#91): the approved Plan message as a live checklist, then a table of contents
into each Тема's result card - all in the one canonical Plan message."""

from __future__ import annotations

from dataclasses import replace

from content_zavod.domain import HubArticleCell, HubCellState, HubTopic, PlanHubView
from content_zavod.telegram import (
    ArticleId,
    ExportArticle,
    PlanId,
    PlanItemId,
    PlanMessageRef,
    SimpleAction,
    TelegramGateway,
    decode_callback_data,
    deliver_plan_hub,
)
from content_zavod.telegram.plan_hub import (
    MESSAGE_LIMIT,
    build_hub_article_keyboard,
    build_hub_keyboard,
    build_hub_topic_keyboard,
    render_hub_article_text,
    render_hub_screen,
    render_hub_text,
    render_hub_topic_text,
)
from tests.tenancy import WS_A


def _topic(
    number: int,
    *,
    cover: HubCellState | None = "ready",
    zen: HubCellState = "ready",
    vc: HubCellState = "ready",
    title: str | None = None,
) -> HubTopic:
    item_id = PlanItemId(f"item-{number}")
    return HubTopic(
        id=item_id,
        number=number,
        title=title or f"Тема {number}",
        cover=cover,
        has_cover=cover == "ready",
        articles=[
            HubArticleCell(
                platform="zen",
                state=zen,
                article_id=f"zen-{number}",
                has_content=zen == "ready",
                job_id=10 + number,
            ),
            HubArticleCell(
                platform="vc",
                state=vc,
                article_id=f"vc-{number}",
                has_content=vc == "ready",
                job_id=20 + number,
            ),
        ],
    )


def _hub(
    *topics: HubTopic,
    open_item_id: str | None = None,
    open_article_id: str | None = None,
    status="approved",
) -> PlanHubView:
    return PlanHubView(
        id=PlanId("plan-1"),
        week_label="2026-W41",
        status=status,
        topics=list(topics),
        open_item_id=PlanItemId(open_item_id) if open_item_id else None,
        open_article_id=ArticleId(open_article_id) if open_article_id else None,
    )


def _buttons(keyboard) -> list[tuple[str, object]]:
    return [
        (button.text, decode_callback_data(button.callback_data))
        for row in keyboard.inline_keyboard
        for button in row
    ]


def test_hub_in_progress_is_a_checklist_with_a_derived_count() -> None:
    hub = _hub(_topic(1, zen="pending", vc="failed"), _topic(2, cover="pending", vc="pending"))

    text = render_hub_text(hub)

    assert text.startswith("📋 План: 5–11 октября 2026")
    assert "готово 3 из 6" in text
    assert "1. Тема 1\n🖼 ✅ · Дзен ⏳ · VC.ru ❌" in text
    assert "2. Тема 2\n🖼 ⏳ · Дзен ✅ · VC.ru ⏳" in text
    # No Тема is finished yet; the ❌ can already be retried without waiting for the rest.
    assert _buttons(build_hub_keyboard(hub)) == [
        ("🔁 Повторить неудавшееся", SimpleAction("hub_retry", "plan-1"))
    ]
    assert build_hub_keyboard(_hub(_topic(1, zen="pending"))) is None
    assert "Что дальше" not in render_hub_text(hub)  # nothing to open yet


def test_finished_topic_gets_a_button_while_others_are_still_generating() -> None:
    hub = _hub(_topic(1), _topic(2, zen="pending"))

    assert render_hub_text(hub).endswith(
        "\n\nЧто дальше: откройте Тему кнопкой ниже — внутри её Статьи."
    )
    assert _buttons(build_hub_keyboard(hub)) == [
        ("📂 1. Тема 1", SimpleAction("hub_topic", "item-1"))
    ]


def test_all_done_hub_is_a_table_of_contents() -> None:
    hub = _hub(_topic(1), _topic(2))

    assert "\n✅ Всё готово.\n" in render_hub_text(hub)  # the advice is «Что дальше»
    assert [action for _, action in _buttons(build_hub_keyboard(hub))] == [
        SimpleAction("hub_topic", "item-1"),
        SimpleAction("hub_topic", "item-2"),
    ]


def test_done_with_failures_offers_retry() -> None:
    hub = _hub(_topic(1, vc="failed"))

    assert "не всё получилось (1 из 3)" in render_hub_text(hub)
    assert _buttons(build_hub_keyboard(hub))[-1] == (
        "🔁 Повторить неудавшееся",
        SimpleAction("hub_retry", "plan-1"),
    )


def test_with_covers_off_the_hub_has_no_cover_cell_line_or_button() -> None:
    """#123: `YANDEX_COVERS=off` - a Тема is done once its Статьи are."""
    topic = _topic(1, cover=None)
    hub = _hub(topic, _topic(2, cover=None, vc="pending"))

    text = render_hub_text(hub)

    assert "1. Тема 1\nДзен ✅ · VC.ru ✅" in text
    assert "готово 3 из 4" in text
    assert "🖼" not in text
    assert "✅ Всё готово" in render_hub_text(_hub(topic))
    card = render_hub_topic_text(_hub(topic, open_item_id="item-1"), topic)
    assert "Обложка" not in card
    assert all("Обложка" not in text for text, _ in _buttons(build_hub_topic_keyboard(hub, topic)))


def test_topic_card_lists_cells_and_offers_cover_articles_retry_and_back() -> None:
    topic = _topic(1, vc="failed")
    hub = _hub(topic, _topic(2), open_item_id="item-1")

    text = render_hub_topic_text(hub, topic)

    assert text.startswith("📂 Тема 1 из 2\n«Тема 1»")
    assert "\n\n• Дзен — ✅ готова, ждёт проверки\n• VC.ru — ❌ не получилась\n" in text
    assert "• 🖼 Обложка — ✅ готова" in text
    assert text.endswith("«Готово, публикую», нужно поправить — «Попросить доработать».")
    assert "📖" not in text  # #143: the 📖/📄 legend is gone
    assert _buttons(build_hub_topic_keyboard(hub, topic)) == [
        ("📄 Статья для Дзена ›", SimpleAction("hub_article", "zen-1")),
        ("🖼 Обложка", SimpleAction("hub_cover", "item-1")),
        ("🎨 Перерисовать", SimpleAction("request_cover", "item-1")),
        ("🔁 Повторить", SimpleAction("hub_retry_topic", "item-1")),
        ("◀ К Плану", SimpleAction("hub_back", "plan-1")),
    ]


def test_topic_being_redone_keeps_its_button_and_shows_the_kept_version() -> None:
    """✏️ Доработать / 🔁 Повторить on a Статья that already has a Версия: the Тема stays
    reachable from the checklist, and a failed redo says the previous Версия is still there."""
    topic = HubTopic(
        id=PlanItemId("item-1"),
        number=1,
        title="Тема 1",
        cover="ready",
        has_cover=True,
        articles=[
            HubArticleCell(platform="zen", state="pending", article_id="zen-1", has_content=True),
            HubArticleCell(platform="vc", state="failed", article_id="vc-1", has_content=True),
        ],
    )
    hub = _hub(topic)

    assert _buttons(build_hub_keyboard(hub))[0] == (
        "📂 1. Тема 1",
        SimpleAction("hub_topic", "item-1"),
    )
    text = render_hub_topic_text(hub, topic)
    assert "• Дзен — ⏳ переписывается" in text
    assert "• VC.ru — ❌ не получилась, открыта прежняя версия" in text
    assert ("📄 Статья для VC.ru ›", SimpleAction("hub_article", "vc-1")) in _buttons(
        build_hub_topic_keyboard(hub, topic)
    )


def test_topic_card_warns_about_a_version_written_without_sources() -> None:
    """#94's ⚠️ is on the result card too, not only on the Статья card behind «📄»."""
    topic = HubTopic(
        id=PlanItemId("item-1"),
        number=1,
        title="Тема 1",
        cover="ready",
        has_cover=True,
        articles=[
            HubArticleCell(
                platform="zen",
                state="ready",
                article_id="zen-1",
                has_content=True,
                research_status="no_evidence",
            ),
            HubArticleCell(
                platform="vc",
                state="ready",
                article_id="vc-1",
                has_content=True,
                research_status="ok",
            ),
        ],
    )

    text = render_hub_topic_text(_hub(topic), topic)

    assert "• 🖼 Обложка — ✅ готова\n⚠️ Источники не найдены — проверьте факты (Дзен)\n" in text
    assert text.count("⚠️") == 1


def test_screen_follows_the_open_topic_and_falls_back_to_the_checklist() -> None:
    open_text, _ = render_hub_screen(_hub(_topic(1), open_item_id="item-1"))
    gone_text, _ = render_hub_screen(_hub(_topic(1), open_item_id="item-9"))

    assert open_text.startswith("📂 Тема 1 из 1")
    assert gone_text.startswith("📋 План")


def test_hub_with_no_topics_says_so() -> None:
    assert "не осталось Тем" in render_hub_text(_hub())
    assert build_hub_keyboard(_hub()) is None


def test_huge_titles_stay_within_telegram_limits() -> None:
    topics = [_topic(n, title="Очень длинный заголовок " * 40) for n in range(1, 40)]
    hub = _hub(*topics)

    assert len(render_hub_text(hub)) <= MESSAGE_LIMIT
    for text, _ in _buttons(build_hub_keyboard(hub)):
        assert len(text) <= 60


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.edited: list[tuple[int, int, str]] = []

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None) -> int:
        self.sent.append((chat_id, text))
        return 77

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None) -> None:
        self.edited.append((chat_id, message_id, text))


class FakePlan:
    def __init__(self, hub: PlanHubView, ref: PlanMessageRef | None) -> None:
        self.hub = hub
        self.ref = ref
        self.recorded: list[tuple[str, int, int]] = []

    async def get_hub(self, workspace_id, plan_id):
        return self.hub

    async def get_message_ref(self, workspace_id, plan_id):
        return self.ref

    async def record_message_ref(self, workspace_id, plan_id, chat_id, message_id) -> None:
        self.recorded.append((plan_id, chat_id, message_id))


async def test_deliver_plan_hub_edits_the_canonical_plan_message() -> None:
    bot = FakeBot()
    plan = FakePlan(_hub(_topic(1)), PlanMessageRef(chat_id=-100, message_id=5))

    await deliver_plan_hub(plan, TelegramGateway(bot), WS_A, 42, PlanId("plan-1"))
    await deliver_plan_hub(plan, TelegramGateway(bot), WS_A, 42, PlanId("plan-1"))  # redelivery

    assert bot.sent == []
    assert [(c, m) for c, m, _ in bot.edited] == [(-100, 5), (-100, 5)]
    assert bot.edited[0][2] == bot.edited[1][2]


async def test_deliver_plan_hub_sends_and_records_when_no_message_exists_yet() -> None:
    bot = FakeBot()
    plan = FakePlan(_hub(_topic(1)), None)

    await deliver_plan_hub(plan, TelegramGateway(bot), WS_A, 42, PlanId("plan-1"))

    assert len(bot.sent) == 1
    assert plan.recorded == [("plan-1", 42, 77)]


async def test_deliver_plan_hub_leaves_an_archived_plan_alone() -> None:
    bot = FakeBot()
    plan = FakePlan(_hub(_topic(1), status="archived"), PlanMessageRef(chat_id=1, message_id=2))

    await deliver_plan_hub(plan, TelegramGateway(bot), WS_A, 42, PlanId("plan-1"))

    assert bot.sent == [] and bot.edited == []


def _cell(**changes) -> HubArticleCell:
    cell = HubArticleCell(
        platform="zen",
        state="ready",
        article_id="zen-1",
        has_content=True,
        telegraph_path="Kak-vybrat-10-13",
        chars=6230,
        opening="# Тема 1\n\nПервое предложение. Второе предложение. Третье.",
    )
    return replace(cell, **changes)


def _topic_with(*cells: HubArticleCell, cover: HubCellState | None = "ready") -> HubTopic:
    return HubTopic(
        id=PlanItemId("item-1"),
        number=1,
        title="Тема 1",
        cover=cover,
        has_cover=cover == "ready",
        articles=list(cells),
    )


def test_topic_next_line_follows_the_state_of_its_articles() -> None:
    writing = _topic_with(
        _cell(state="pending", has_content=False),
        _cell(platform="vc", state="pending", has_content=False),
    )
    done = _topic_with(_cell(exported=True), _cell(platform="vc", exported=True))
    failed = _topic_with(
        _cell(state="failed", has_content=False),
        _cell(platform="vc", state="failed", has_content=False),
    )

    assert render_hub_topic_text(_hub(writing), writing).endswith(
        "Что дальше: Статьи ещё пишутся — это сообщение обновится само."
    )
    assert render_hub_topic_text(_hub(done), done).endswith(
        "Что дальше: все Статьи Темы готовы к публикации. Вернитесь к Плану за следующей Темой."
    )
    assert render_hub_topic_text(_hub(failed), failed).endswith("нажмите «🔁 Повторить».")


def test_exported_article_is_a_thumbs_up_in_the_checklist_and_on_the_topic() -> None:
    topic = _topic_with(_cell(exported=True), _cell(platform="vc"))
    hub = _hub(topic)

    assert "🖼 ✅ · Дзен 👍 · VC.ru ✅" in render_hub_text(hub)
    assert "• Дзен — 👍 отмечена готовой" in render_hub_topic_text(hub, topic)


def test_article_screen_is_a_summary_with_status_size_and_opening() -> None:
    topic = _topic_with(_cell(research_status="no_evidence"), _cell(platform="vc"))
    hub = _hub(topic, open_item_id="item-1", open_article_id="zen-1")

    text, keyboard = render_hub_screen(hub)

    assert text == (
        "📄 Статья для Дзена\n«Тема 1»\n\n"
        "✅ Готова — прочитайте и проверьте\n"
        "≈ 6\u00a0200 знаков · 5 мин чтения\n"
        "⚠️ Источники не найдены — проверьте факты перед публикацией\n\n"
        "Начало: «Первое предложение. Второе предложение.»\n\n"
        "Что дальше: «Читать полностью» → если всё хорошо, «Готово, публикую». "
        "Для публикации скачайте Word."
    )
    rows = keyboard.inline_keyboard
    assert (rows[0][0].text, rows[0][0].url) == (
        "📖 Читать полностью ↗",
        "https://telegra.ph/Kak-vybrat-10-13",
    )
    assert [(b.text, decode_callback_data(b.callback_data)) for r in rows[1:] for b in r] == [
        ("✅ Готово, публикую", SimpleAction("hub_approve", "zen-1")),
        ("✏️ Попросить доработать", SimpleAction("regenerate_article", "zen-1")),
        ("⬇️ Word (.docx)", ExportArticle("zen-1", "docx")),
        ("⬇️ Текст (.md)", ExportArticle("zen-1", "md")),
        ("◀ К Теме", SimpleAction("hub_topic", "item-1")),
        ("◀ К Плану", SimpleAction("hub_back", "plan-1")),
    ]


def test_article_screen_status_lines_and_buttons_per_state() -> None:
    def screen(cell: HubArticleCell) -> tuple[str, list[str]]:
        topic = _topic_with(cell)
        text, keyboard = render_hub_screen(
            _hub(topic, open_item_id="item-1", open_article_id="zen-1")
        )
        return text, [b.text for r in keyboard.inline_keyboard for b in r]

    exported_text, exported_buttons = screen(_cell(exported=True))
    assert "👍 Отмечена готовой — можно публиковать" in exported_text
    assert "👍 Отмечено готовой" in exported_buttons
    assert "✅ Готово, публикую" not in exported_buttons

    pending_text, pending_buttons = screen(_cell(state="pending", telegraph_path=None))
    assert "⏳ Переписываю — пока открыта прежняя версия" in pending_text
    assert pending_buttons == [
        "⬇️ Word (.docx)",
        "⬇️ Текст (.md)",
        "◀ К Теме",
        "◀ К Плану",
    ]

    failed_text, failed_buttons = screen(_cell(state="failed"))
    assert "❌ Переписать не получилось — открыта прежняя версия" in failed_text
    assert "✏️ Попросить доработать" in failed_buttons
    assert "✅ Готово, публикую" not in failed_buttons


def test_reading_time_is_rounded_up_and_size_to_hundreds() -> None:
    def size_line(chars: int) -> str:
        topic = _topic_with(_cell(chars=chars))
        return render_hub_article_text(topic, topic.articles[0]).splitlines()[4]

    assert size_line(1300) == "≈ 1\u00a0300 знаков · 1 мин чтения"
    assert size_line(1349) == "≈ 1\u00a0300 знаков · 1 мин чтения"  # the shown number
    assert size_line(1350) == "≈ 1\u00a0400 знаков · 2 мин чтения"
    assert size_line(6250) == "≈ 6\u00a0300 знаков · 5 мин чтения"  # half rounds up
    assert size_line(30) == "≈ 100 знаков · 1 мин чтения"


def test_an_open_article_without_a_version_falls_back_to_its_topic() -> None:
    topic = _topic_with(_cell(state="pending", has_content=False))

    text, _ = render_hub_screen(_hub(topic, open_item_id="item-1", open_article_id="zen-1"))

    assert text.startswith("📂 Тема 1 из 1")


def test_every_hub_callback_fits_telegram_limit() -> None:
    long_id = "a" * 36
    topic = _topic_with(_cell(article_id=long_id), _cell(platform="vc", article_id=long_id))
    hub = _hub(topic, open_item_id="item-1", open_article_id=long_id)

    for keyboard in (
        build_hub_topic_keyboard(hub, topic),
        build_hub_article_keyboard(hub, topic, topic.articles[0]),
    ):
        for row in keyboard.inline_keyboard:
            for button in row:
                assert button.url or len(button.callback_data.encode()) <= 64


def test_rewriting_topic_says_the_result_comes_below_not_that_it_updates_itself() -> None:
    topic = _topic_with(_cell(state="pending"), _cell(platform="vc", state="pending"))

    text = render_hub_topic_text(_hub(topic), topic)

    assert "• Дзен — ⏳ переписывается" in text
    assert text.endswith("Что дальше: Статьи переписываются — пришлю, как будут готовы.")


def test_article_screen_without_a_reading_page_does_not_point_at_it() -> None:
    """The page couldn't be published: no «📖», and «Что дальше» doesn't name it."""
    topic = _topic_with(_cell(telegraph_path=None))

    text, keyboard = render_hub_screen(_hub(topic, open_item_id="item-1", open_article_id="zen-1"))

    assert "Читать полностью" not in text
    assert text.endswith(
        "Что дальше: скачайте Word и прочитайте → если всё хорошо, «Готово, публикую»."
    )
    assert all(b.url is None for r in keyboard.inline_keyboard for b in r)
