from __future__ import annotations

from content_zavod.domain import PLATFORMS
from content_zavod.domain.types import ArticleStatus, PlanStatus
from content_zavod.telegram.texts import (
    ARTICLE_STATUSES,
    PLAN_STATUSES,
    article_status,
    job_failure_text,
    parse_weekday,
    plan_status,
    platform_name,
    weekday_name,
)


def test_every_domain_key_has_a_russian_label() -> None:
    """A status or Площадка added to the domain without a label here would leak its raw
    key into the chat (#89)."""
    assert set(ARTICLE_STATUSES) == set(ArticleStatus.__args__)
    assert set(PLAN_STATUSES) == set(PlanStatus.__args__)
    assert [platform_name(p) for p in PLATFORMS] == ["Дзен", "VC.ru"]


def test_unknown_key_falls_back_to_itself() -> None:
    assert article_status("brand_new") == "brand_new"
    assert plan_status("brand_new") == "brand_new"
    assert platform_name("tj") == "tj"
    assert weekday_name("xyz") == "xyz"


def test_parse_weekday_accepts_code_short_and_full_names() -> None:
    assert parse_weekday("mon") == "mon"
    assert parse_weekday("Пн") == "mon"
    assert parse_weekday("воскресенье") == "sun"
    assert parse_weekday("funday") is None


def test_job_failure_text_names_the_tema_and_platform_when_known() -> None:
    assert (
        job_failure_text("generate_article", title="Тренды", platform="zen")
        == "Не удалось написать Статью для Дзена: «Тренды»"
    )
    assert job_failure_text("generate_article") == "Не удалось написать Статью."
    assert job_failure_text("generate_plan", title="Тренды") == "Не удалось составить План."


def test_bot_descriptions_fit_telegrams_limits() -> None:
    from content_zavod.telegram.texts import BOT_DESCRIPTION, BOT_SHORT_DESCRIPTION

    assert 0 < len(BOT_SHORT_DESCRIPTION) <= 120
    assert 0 < len(BOT_DESCRIPTION) <= 512


def test_steps_count_text_agrees_with_the_number() -> None:
    from content_zavod.telegram.texts import steps_count_text

    assert steps_count_text(1) == "1 короткий шаг"
    assert steps_count_text(4) == "4 коротких шага"
    assert steps_count_text(5) == "5 коротких шагов"
    assert steps_count_text(11) == "11 коротких шагов"
    assert steps_count_text(22) == "22 коротких шага"
