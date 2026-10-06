from content_zavod.telegram import ArticleId, ArticleView, PlanItemId
from content_zavod.telegram.article_card import (
    PREVIEW_LIMIT,
    plain_text_preview,
    platform_display_name,
    render_article_card_text,
)


def _article(content: str, *, platform: str = "vc", title: str = "Как выбрать CRM") -> ArticleView:
    return ArticleView(
        id=ArticleId("a1"),
        plan_item_id=PlanItemId("i1"),
        title=title,
        platform=platform,
        content=content.encode("utf-8"),
    )


def test_platform_display_name_is_human_with_raw_fallback() -> None:
    assert platform_display_name("zen") == "Дзен"
    assert platform_display_name("vc") == "VC.ru"
    assert platform_display_name("tj") == "tj"


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
