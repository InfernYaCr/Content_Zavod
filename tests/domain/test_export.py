from docx import Document
from io import BytesIO

from content_zavod.domain import ArticleId, ArticleView, PlanItemId, build_export_document, build_export_filename

_ARTICLE = ArticleView(
    id=ArticleId("article-1"),
    plan_item_id=PlanItemId("item-1"),
    title="Как выбрать нишу!",
    platform="zen",
    content="Первый абзац.\n\nВторой абзац.".encode("utf-8"),
)


def test_build_export_filename_slugifies_title_and_appends_format() -> None:
    assert build_export_filename("Best Niche Guide", "zen", "docx") == "best-niche-guide-zen.docx"
    assert build_export_filename("Best Niche Guide", "zen", "md") == "best-niche-guide-zen.md"


def test_build_export_filename_falls_back_to_article_for_an_empty_slug() -> None:
    assert build_export_filename("!!!", "zen", "docx") == "article-zen.docx"


def test_build_export_document_md_is_the_plain_text_content() -> None:
    assert build_export_document(_ARTICLE, "md") == _ARTICLE.content


def test_build_export_document_docx_contains_the_content_as_paragraphs() -> None:
    document_bytes = build_export_document(_ARTICLE, "docx")

    document = Document(BytesIO(document_bytes))
    paragraphs = [p.text for p in document.paragraphs]

    assert paragraphs == ["Первый абзац.", "", "Второй абзац."]
