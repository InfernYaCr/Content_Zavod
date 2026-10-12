from __future__ import annotations

from content_zavod.domain import PLATFORMS
from content_zavod.domain.types import ArticleStatus, PlanStatus
from content_zavod.telegram.texts import (
    ARTICLE_STATUSES,
    PLAN_STATUSES,
    approve_shortfall_text,
    article_status,
    error_alert_text,
    job_failure_text,
    parse_weekday,
    plan_status,
    platform_name,
    weekday_name,
)
from content_zavod.workspaces import (
    DailyFreeJobLimitReached,
    DailyJobLimitReached,
    DailyPlanLimitReached,
    TrialLimitExhausted,
    Usage,
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


def test_newcomer_texts_stay_neutral_until_the_niche_map_lands() -> None:
    """Until #128's pipeline, nothing promises a number of Темы or Wordstat growth, and
    nothing speaks of a team, Роли or access requests (ADR-0015)."""
    from content_zavod.telegram import guide_texts, texts

    newcomer = [texts.BOT_DESCRIPTION, texts.BOT_SHORT_DESCRIPTION, texts.ONBOARDING_INTRO]
    newcomer += [
        value
        for module in (texts, guide_texts)
        for name, value in vars(module).items()
        if name.isupper() and isinstance(value, str)
    ]
    for text in newcomer:
        for banned in (
            "до трёх Тем",
            "растут в Wordstat",
            "чат команды",
            "чате команды",
            "для команды",
            "вашей команды",
            "Контент-менеджер",
            "Направлени",
            "Запросить доступ",
        ):
            assert banned not in text, (banned, text)


def test_a_button_of_another_workspace_reads_like_a_stale_one() -> None:
    """ADR-0015: «not yours» and «gone» must not be told apart."""
    from content_zavod.domain.errors import (
        ArticleNotFound,
        ArticleVersionNotFound,
        PlanItemNotFound,
        PlanNotFound,
    )
    from content_zavod.job_queue import JobNotFound
    from content_zavod.telegram.texts import STALE_BUTTON, error_alert_text

    for exc in (
        PlanNotFound("p"),
        PlanItemNotFound("i"),
        ArticleNotFound("a"),
        ArticleVersionNotFound("a", 1),
        JobNotFound(1),
    ):
        assert error_alert_text(exc) == STALE_BUTTON


def _short(used: int, asked: int, contact: str | None = "@owner") -> TrialLimitExhausted:
    usage = Usage(plans_used=1, plan_limit=1, articles_used=used, article_limit=8)
    return TrialLimitExhausted(usage, plans=0, articles=asked, contact=contact)


def test_a_shortfall_counts_statyi_in_the_accusative_and_gives_the_contact() -> None:
    assert error_alert_text(_short(7, 3)) == (
        "Пробного лимита хватит ещё на 1 Статью, а нужно 3.\nЧтобы продолжить, напишите @owner."
    )
    assert "на 2 Статьи," in error_alert_text(_short(6, 3))
    assert "на 5 Статей," in error_alert_text(_short(3, 6, None))
    assert "напишите" not in error_alert_text(_short(3, 6, None))


def test_approving_too_many_temy_asks_to_remove_some_only_if_one_tema_fits() -> None:
    assert "🗑" in approve_shortfall_text(_short(4, 6), per_tema=2)
    nearly = approve_shortfall_text(_short(7, 6), per_tema=2)
    assert "почти исчерпан" in nearly and "@owner" in nearly and "🗑" not in nearly
    assert approve_shortfall_text(_short(8, 6), per_tema=2).startswith("Пробный лимит исчерпан")


def test_daily_caps_have_their_own_reasons() -> None:
    texts = {
        error_alert_text(exc)
        for exc in (DailyJobLimitReached(), DailyPlanLimitReached(), DailyFreeJobLimitReached())
    }
    assert len(texts) == 3 and all("завтра" in text for text in texts)
