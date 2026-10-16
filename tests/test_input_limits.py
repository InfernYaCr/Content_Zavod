"""ADR-0015, «Безопасность»: every text an Owner puts into their own prompts has a length limit.
One table: the limit itself is accepted, one character more is refused and nothing is saved -
for each Настройка (checked in `SettingsService`), a proposed Тема and a regeneration comment."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from content_zavod.domain.errors import InvalidSettingValue
from content_zavod.settings import (
    AUDIENCE_MAX_LENGTH,
    NICHE_MAX_LENGTH,
    PERSONA_MAX_LENGTH,
    PROJECT_MAX_LENGTH,
    SettingsService,
)
from content_zavod.telegram import CommentGatedRegeneration, TelegramGateway, handle_topic_command
from content_zavod.telegram.comment_gated_regeneration import COMMENT_MAX_LENGTH
from content_zavod.telegram.settings_screen import SETTING_FIELDS
from content_zavod.telegram.topic_command import TOPIC_MAX_LENGTH
from tests.settings.test_service import InMemoryStore
from tests.telegram.fakes import FakePendingInputs
from tests.telegram.test_comment_gated_regeneration import FakePrompt
from tests.telegram.test_topic_command import WORKSPACE, FakeBot, FakePlan
from tests.tenancy import WS_A


def _padded(prefix: str, length: int) -> str:
    return prefix + "я" * (length - len(prefix))


type Setter = Callable[[SettingsService, str], Awaitable[object]]

SETTINGS: list[tuple[str, int, str, Setter]] = [
    ("niche", NICHE_MAX_LENGTH, "", lambda s, v: s.set_niche(WS_A, v)),
    ("audience", AUDIENCE_MAX_LENGTH, "", lambda s, v: s.set_audience(WS_A, v)),
    ("persona", PERSONA_MAX_LENGTH, "Роль: ", lambda s, v: s.set_persona(WS_A, v)),
    ("project", PROJECT_MAX_LENGTH, "example.ru ", lambda s, v: s.set_project(WS_A, v)),
]


def test_the_limits_are_those_of_adr_0015() -> None:
    assert (NICHE_MAX_LENGTH, PERSONA_MAX_LENGTH, PROJECT_MAX_LENGTH, AUDIENCE_MAX_LENGTH) == (
        200,
        1500,
        500,
        2000,
    )
    assert (TOPIC_MAX_LENGTH, COMMENT_MAX_LENGTH) == (300, 1000)


@pytest.mark.parametrize(("key", "limit", "prefix", "setter"), SETTINGS)
async def test_a_setting_at_its_limit_is_saved_and_one_more_is_refused(
    key: str, limit: int, prefix: str, setter: Setter
) -> None:
    store = InMemoryStore()
    settings = SettingsService(store)

    with pytest.raises(InvalidSettingValue) as excinfo:
        await setter(settings, _padded(prefix, limit + 1))
    assert excinfo.value.field == f"{key}_too_long"
    assert store._values == {}

    await setter(settings, _padded(prefix, limit))
    assert len(store._values) == 1


@pytest.mark.parametrize(("key", "limit", "prefix", "setter"), SETTINGS)
def test_the_refusal_names_the_limit(key: str, limit: int, prefix: str, setter: Setter) -> None:
    (setting,) = (field for field in SETTING_FIELDS if field.key == key)
    assert str(limit) in setting.invalid_by_field[f"{key}_too_long"]


async def test_the_limit_counts_characters_after_trimming_not_bytes() -> None:
    """Emoji are one character each, and the spaces around the text don't count."""
    store = InMemoryStore()

    assert await SettingsService(store).set_niche(WS_A, f"  {'🍰' * NICHE_MAX_LENGTH}\n") == (
        "🍰" * NICHE_MAX_LENGTH
    )


async def test_a_topic_at_its_limit_is_added_and_one_more_is_refused() -> None:
    plan = FakePlan()
    bot = FakeBot()
    gateway = TelegramGateway(bot)

    await handle_topic_command(plan, gateway, WORKSPACE, 1, "я" * (TOPIC_MAX_LENGTH + 1))
    assert plan.added == []
    assert str(TOPIC_MAX_LENGTH) in bot.sent_messages[-1][1]

    await handle_topic_command(plan, gateway, WORKSPACE, 1, "я" * TOPIC_MAX_LENGTH)
    assert [len(topic.title) for _week, (topic,) in plan.added] == [TOPIC_MAX_LENGTH]


async def test_a_comment_at_its_limit_regenerates_and_one_more_is_refused() -> None:
    regenerated: list[str | None] = []

    async def regenerate(workspace_id: int, id_: str, comment: str | None) -> None:
        regenerated.append(comment)

    prompt = FakePrompt()
    flow = CommentGatedRegeneration[str](
        regenerate, prompt, FakePendingInputs(), kind="test_comment"
    )
    await flow.request(WS_A, 1, 10, "item-1")

    assert await flow.handle_comment_reply(WS_A, 1, 10, "я" * (COMMENT_MAX_LENGTH + 1))
    assert regenerated == [] and prompt.rejected == [1]

    assert await flow.handle_comment_reply(WS_A, 1, 10, "я" * COMMENT_MAX_LENGTH)
    assert regenerated == ["я" * COMMENT_MAX_LENGTH]
