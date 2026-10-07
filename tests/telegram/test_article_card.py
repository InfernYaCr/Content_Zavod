from content_zavod.telegram import ArticleId, ArticleView, PlanItemId
from content_zavod.telegram.article_card import (
    PREVIEW_LIMIT,
    plain_text_preview,
    render_article_card_text,
)


def _article(
    content: str,
    *,
    platform: str = "vc",
    title: str = "Как выбрать CRM",
    research_status: str | None = None,
) -> ArticleView:
    return ArticleView(
        id=ArticleId("a1"),
        plan_item_id=PlanItemId("i1"),
        title=title,
        platform=platform,
        content=content.encode("utf-8"),
        research_status=research_status,
    )


def test_preview_strips_markdown_and_skips_the_title_heading() -> None:
    markdown = (
        "# Как выбрать CRM\n\n"
        "Вступление с **жирным** и *курсивом* и [ссылкой](https://example.com).\n\n"
        "## Шаг первый\n"
        "- пункт один\n"
        "---\n"
        "> цитата"
    )

    preview = plain_text_preview(markdown, title="Как выбрать CRM")

    assert preview == (
        "Вступление с жирным и курсивом и ссылкой.\nШаг первый\n• пункт один\nцитата"
    )


def test_preview_is_cut_on_a_word_boundary_with_an_ellipsis() -> None:
    markdown = " ".join(["слово"] * 400)

    preview = plain_text_preview(markdown)

    assert len(preview) <= PREVIEW_LIMIT + 1
    assert preview.endswith("слово…")


def test_card_text_has_title_platform_and_preview() -> None:
    text = render_article_card_text(_article("## Раздел\nТекст статьи."))

    assert text == "📄 Как выбрать CRM\nПлощадка: VC.ru\n\nРаздел\nТекст статьи."


def test_card_text_without_content_has_no_trailing_blank_line() -> None:
    assert render_article_card_text(_article("")) == "📄 Как выбрать CRM\nПлощадка: VC.ru"


def test_card_warns_the_editor_when_the_version_has_no_evidence() -> None:
    text = render_article_card_text(_article("Текст статьи.", research_status="no_evidence"))

    assert text == (
        "📄 Как выбрать CRM\nПлощадка: VC.ru\n"
        "⚠️ Источники не найдены — проверьте факты перед публикацией\n\nТекст статьи."
    )


def test_card_warns_differently_when_search_was_unavailable() -> None:
    text = render_article_card_text(_article("Текст статьи.", research_status="search_unavailable"))

    assert "⚠️ Поиск источников был недоступен" in text


def test_card_has_no_warning_for_researched_or_legacy_versions() -> None:
    for status in ("ok", None):
        text = render_article_card_text(_article("Текст.", research_status=status))
        assert "⚠️" not in text
