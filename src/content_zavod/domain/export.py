"""Article export: turning an Article's raw content into a downloadable file for a
chosen format (ADR-0007). Single choke point (`build_export_filename`/`build_export_document`)
shared by the initial delivery-on-generation path and the history re-download path (#20).
"""

from __future__ import annotations

import re
from io import BytesIO

from docx import Document

from .types import ArticleFormat, ArticleView


def build_export_filename(title: str, platform: str, article_format: ArticleFormat) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "article"
    return f"{slug}-{platform}.{article_format}"


def build_export_document(article: ArticleView, article_format: ArticleFormat) -> bytes:
    text = article.content.decode("utf-8")
    if article_format == "md":
        return text.encode("utf-8")
    document = Document()
    for line in text.split("\n"):
        document.add_paragraph(line)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()
