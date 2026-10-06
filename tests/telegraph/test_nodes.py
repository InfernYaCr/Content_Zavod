from content_zavod.telegraph.nodes import (
    FooterLink,
    content_size,
    fit_content,
    footer_nodes,
    markdown_to_nodes,
    parse_inline,
)


def _link(url: str, *children: object) -> dict[str, object]:
    return {"tag": "a", "attrs": {"href": url}, "children": list(children)}


def test_headings_map_onto_telegraph_h3_h4() -> None:
    nodes = markdown_to_nodes("# Большой\n## Раздел\n### Подраздел\n#### Мелкий")

    assert nodes == [
        {"tag": "h3", "children": ["Большой"]},
        {"tag": "h3", "children": ["Раздел"]},
        {"tag": "h4", "children": ["Подраздел"]},
        {"tag": "h4", "children": ["Мелкий"]},
    ]


def test_leading_heading_repeating_the_title_is_dropped() -> None:
    nodes = markdown_to_nodes("# Как выбрать CRM?\n\nТекст.", title="Как выбрать CRM")

    assert nodes == [{"tag": "p", "children": ["Текст."]}]


def test_a_later_heading_equal_to_the_title_is_kept() -> None:
    nodes = markdown_to_nodes("Вступление.\n# Тема", title="Тема")

    assert nodes[1] == {"tag": "h3", "children": ["Тема"]}


def test_consecutive_list_items_group_into_one_list() -> None:
    nodes = markdown_to_nodes("- один\n* два\n\n1. первый\n2) второй\nабзац")

    assert nodes == [
        {
            "tag": "ul",
            "children": [{"tag": "li", "children": ["один"]}, {"tag": "li", "children": ["два"]}],
        },
        {
            "tag": "ol",
            "children": [
                {"tag": "li", "children": ["первый"]},
                {"tag": "li", "children": ["второй"]},
            ],
        },
        {"tag": "p", "children": ["абзац"]},
    ]


def test_every_non_empty_line_is_its_own_paragraph_and_rules_quotes_are_kept() -> None:
    nodes = markdown_to_nodes("Первый.\nВторой.\n\n---\n> Цитата")

    assert nodes == [
        {"tag": "p", "children": ["Первый."]},
        {"tag": "p", "children": ["Второй."]},
        {"tag": "hr"},
        {"tag": "blockquote", "children": ["Цитата"]},
    ]


def test_inline_bold_italic_code_and_links() -> None:
    children = parse_inline(
        "Это **важно**, *очень* и __тоже__, _курсив_, `код` и [сайт](https://example.com/a)."
    )

    assert children == [
        "Это ",
        {"tag": "strong", "children": ["важно"]},
        ", ",
        {"tag": "em", "children": ["очень"]},
        " и ",
        {"tag": "strong", "children": ["тоже"]},
        ", ",
        {"tag": "em", "children": ["курсив"]},
        ", ",
        {"tag": "code", "children": ["код"]},
        " и ",
        _link("https://example.com/a", "сайт"),
        ".",
    ]


def test_nested_bold_link_and_bare_urls() -> None:
    children = parse_inline("**[Отчёт](https://a.ru/r)** и https://b.ru/x.")

    assert children == [
        {"tag": "strong", "children": [_link("https://a.ru/r", "Отчёт")]},
        " и ",
        _link("https://b.ru/x", "https://b.ru/x"),
        ".",
    ]


def test_snake_case_and_arithmetic_are_not_emphasis() -> None:
    assert parse_inline("utm_source_name и 2 * 3 * 4") == ["utm_source_name и 2 * 3 * 4"]


def test_fit_content_keeps_small_content_and_appends_the_footer() -> None:
    nodes = markdown_to_nodes("Текст.")
    footer = footer_nodes(FooterLink("Проект", "https://example.com"))

    assert fit_content(nodes, tail=footer) == nodes + footer
    assert footer == [
        {"tag": "hr"},
        {"tag": "p", "children": [_link("https://example.com", "Проект")]},
    ]


def test_fit_content_truncates_oversized_content_but_keeps_the_footer() -> None:
    nodes = markdown_to_nodes("\n".join(f"Абзац номер {i} " + "х" * 200 for i in range(100)))
    footer = footer_nodes(FooterLink("Проект", "https://example.com"))

    fitted = fit_content(nodes, tail=footer, limit=5_000)

    assert content_size(fitted) <= 5_000
    assert fitted[-2:] == footer
    assert "Текст сокращён" in str(fitted[-3])
    assert fitted[0] == nodes[0]
    assert len(fitted) < len(nodes)
