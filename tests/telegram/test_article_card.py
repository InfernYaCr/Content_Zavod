from content_zavod.telegram.article_card import (
    OPENING_LIMIT,
    PREVIEW_LIMIT,
    opening_text,
    plain_text_preview,
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


def test_opening_is_the_first_two_sentences_of_the_first_paragraph() -> None:
    """#143: the Статья screen's «Начало» - no title, headings or markup, no third sentence."""
    markdown = (
        "# Как выбрать CRM\n\n"
        "## Введение\n"
        "Как выбрать **CRM**? Начните с [задач](https://example.com). Третье предложение.\n\n"
        "Второй абзац."
    )

    assert opening_text(markdown, title="Как выбрать CRM") == ("Как выбрать CRM? Начните с задач.")


def test_opening_skips_a_plain_line_repeating_the_title() -> None:
    assert opening_text("Как выбрать CRM\n\nТекст.", title="Как выбрать CRM") == "Текст."


def test_opening_keeps_only_the_first_sentence_when_two_do_not_fit() -> None:
    first = "Короткое начало."
    second = "Очень " + "длинное " * 40 + "продолжение."

    assert opening_text(f"{first} {second}") == first


def test_a_long_first_sentence_is_cut_on_a_word_boundary() -> None:
    opening = opening_text(" ".join(["слово"] * 100) + ".")

    assert len(opening) <= OPENING_LIMIT + 1
    assert opening.endswith("слово…")


def test_opening_of_a_list_only_text_falls_back_to_its_items() -> None:
    assert opening_text("- пункт один\n- пункт два") == "• пункт один • пункт два"


def test_opening_of_an_empty_text_is_empty() -> None:
    assert opening_text("") == ""


def test_opening_does_not_split_on_abbreviations() -> None:
    assert opening_text("Это CRM, т. е. база клиентов. Второе. Третье.") == (
        "Это CRM, т. е. база клиентов. Второе."
    )
    assert opening_text("В 2024 г. Рынок вырос. Дальше. Ещё.") == "В 2024 г. Рынок вырос. Дальше."
    assert opening_text("Бюджет 5 тыс. руб. в месяц хватит. Проверим. Ещё.") == (
        "Бюджет 5 тыс. руб. в месяц хватит. Проверим."
    )
    assert opening_text("Пишет А. С. Пушкин. Второе. Третье.") == "Пишет А. С. Пушкин. Второе."


def test_a_sentence_ending_on_a_short_word_still_ends() -> None:
    assert opening_text("Купите это. Второе. Третье.") == "Купите это. Второе."
