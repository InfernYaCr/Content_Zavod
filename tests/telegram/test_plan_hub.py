"""The Хаб (#91): the approved Plan message as a live checklist, then a table of contents
into each Тема's result card - all in the one canonical Plan message."""

from __future__ import annotations

from content_zavod.domain import HubArticleCell, HubCellState, HubTopic, PlanHubView
from content_zavod.telegram import (
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
    build_hub_keyboard,
    build_hub_topic_keyboard,
    render_hub_screen,
    render_hub_text,
    render_hub_topic_text,
)


def _topic(
    number: int,
    *,
    cover: HubCellState = "ready",
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


def _hub(*topics: HubTopic, open_item_id: str | None = None, status="approved") -> PlanHubView:
    return PlanHubView(
        id=PlanId("plan-1"),
        week_label="2026-W41",
        status=status,
        topics=list(topics),
        open_item_id=PlanItemId(open_item_id) if open_item_id else None,
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


def test_finished_topic_gets_a_button_while_others_are_still_generating() -> None:
    hub = _hub(_topic(1), _topic(2, zen="pending"))

    assert "Готовые Темы уже можно открыть" in render_hub_text(hub)
    assert _buttons(build_hub_keyboard(hub)) == [
        ("📂 1. Тема 1", SimpleAction("hub_topic", "item-1"))
    ]


def test_all_done_hub_is_a_table_of_contents() -> None:
    hub = _hub(_topic(1), _topic(2))

    assert "✅ Всё готово" in render_hub_text(hub)
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


def test_topic_card_lists_cells_and_offers_cover_articles_retry_and_back() -> None:
    topic = _topic(1, vc="failed")
    hub = _hub(topic, _topic(2), open_item_id="item-1")

    text = render_hub_topic_text(hub, topic)

    assert text.startswith("📂 Тема 1 из 2\n«Тема 1»")
    assert "🖼 Обложка — ✅ готова" in text
    assert "📄 Дзен — ✅ готова" in text
    assert "📄 VC.ru — ❌ не получилась" in text
    assert _buttons(build_hub_topic_keyboard(hub, topic)) == [
        ("🖼 Обложка", SimpleAction("hub_cover", "item-1")),
        ("📄 Дзен", SimpleAction("hub_article", "zen-1")),
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
    assert "📄 Дзен — ⏳ пишется" in text
    assert "📄 VC.ru — ❌ не получилась, открыта прежняя версия" in text
    assert ("📄 VC.ru", SimpleAction("hub_article", "vc-1")) in _buttons(
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

    assert "📄 Дзен — ✅ готова\n⚠️ Источники не найдены" in text
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

    async def get_hub(self, plan_id):
        return self.hub

    async def get_message_ref(self, plan_id):
        return self.ref

    async def record_message_ref(self, plan_id, chat_id, message_id) -> None:
        self.recorded.append((plan_id, chat_id, message_id))


async def test_deliver_plan_hub_edits_the_canonical_plan_message() -> None:
    bot = FakeBot()
    plan = FakePlan(_hub(_topic(1)), PlanMessageRef(chat_id=-100, message_id=5))

    await deliver_plan_hub(plan, TelegramGateway(bot), 42, PlanId("plan-1"))
    await deliver_plan_hub(plan, TelegramGateway(bot), 42, PlanId("plan-1"))  # redelivery

    assert bot.sent == []
    assert [(c, m) for c, m, _ in bot.edited] == [(-100, 5), (-100, 5)]
    assert bot.edited[0][2] == bot.edited[1][2]


async def test_deliver_plan_hub_sends_and_records_when_no_message_exists_yet() -> None:
    bot = FakeBot()
    plan = FakePlan(_hub(_topic(1)), None)

    await deliver_plan_hub(plan, TelegramGateway(bot), 42, PlanId("plan-1"))

    assert len(bot.sent) == 1
    assert plan.recorded == [("plan-1", 42, 77)]


async def test_deliver_plan_hub_leaves_an_archived_plan_alone() -> None:
    bot = FakeBot()
    plan = FakePlan(_hub(_topic(1), status="archived"), PlanMessageRef(chat_id=1, message_id=2))

    await deliver_plan_hub(plan, TelegramGateway(bot), 42, PlanId("plan-1"))

    assert bot.sent == [] and bot.edited == []
