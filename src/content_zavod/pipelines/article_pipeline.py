"""generate_article / regenerate_article Job Handlers: outline -> draft ->
rewrite -> sources, as four separate TextGenerator calls rather than one long
prompt (long single-shot generations were observed to degrade in quality).

Both job types converge on one shared pipeline core (`_run_pipeline`):
`regenerate_article` is a refinement of the prior result, not a different
pipeline, so it sources its facts from the Article's current Версия (via
`ArticleReader.get`) instead of re-fetching plan_items. Money/legal Темы
don't get a separate step or job_type - the sources step just uses a
stricter prompt for them, selected by a keyword/title heuristic.

Only the outline and draft steps mention the Голос (the rewrite/sources
steps don't reference an author persona); the free-text Voice description
is Owner-editable (#37), read fresh from `OwnerSettingsStore` on every call
so a `/set_voice` takes effect without a restart, shared across all
Площадки - platform tone is layered on top of it, not instead of it.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, Sequence

from ..domain import ArticleId, ArticleView
from ..job_queue import JobHandler
from ..yandex import Completion, Message, TextGenerator
from .url_reachability import UrlReachabilityChecker

VOICE_KEY = "voice"
DEFAULT_VOICE = "маркетолог-практик"

_URL_RE = re.compile(r"https?://\S+")

_SENSITIVE_KEYWORDS = frozenset(
    {
        "кредит",
        "займ",
        "ипотека",
        "налог",
        "право",
        "закон",
        "юрист",
        "страхование",
        "инвестиции",
        "банкрот",
        "штраф",
        "суд",
    }
)

# MVP: real per-token Yandex billing isn't wired up yet (no confirmed pricing
# figures) - cost stays 0.0 until that lands as a follow-up.
_COST_PER_TOKEN = 0.0


class ArticleReader(Protocol):
    async def get(self, article_id: ArticleId) -> ArticleView: ...


class OwnerSettingsOperations(Protocol):
    async def get(self, key: str) -> str | None: ...


async def _current_voice(owner_settings: OwnerSettingsOperations) -> str:
    value = await owner_settings.get(VOICE_KEY)
    return value if value else DEFAULT_VOICE


def make_generate_article_handler(
    text_generator: TextGenerator,
    url_checker: UrlReachabilityChecker,
    owner_settings: OwnerSettingsOperations,
) -> JobHandler:
    async def handle(payload: dict[str, Any]) -> dict[str, Any]:
        return await _run_pipeline(
            text_generator,
            url_checker,
            owner_settings,
            article_id=ArticleId(payload["article_id"]),
            title=payload["title"],
            platform=payload["platform"],
            summary=payload.get("summary", ""),
            keywords=payload.get("keywords", []),
        )

    return handle


def make_regenerate_article_handler(
    article_reader: ArticleReader,
    text_generator: TextGenerator,
    url_checker: UrlReachabilityChecker,
    owner_settings: OwnerSettingsOperations,
) -> JobHandler:
    async def handle(payload: dict[str, Any]) -> dict[str, Any]:
        article_id = ArticleId(payload["article_id"])
        view = await article_reader.get(article_id)
        return await _run_pipeline(
            text_generator,
            url_checker,
            owner_settings,
            article_id=article_id,
            title=view.title,
            platform=view.platform,
            comment=payload.get("comment"),
            previous_content=view.content.decode("utf-8"),
        )

    return handle


async def _run_pipeline(
    text_generator: TextGenerator,
    url_checker: UrlReachabilityChecker,
    owner_settings: OwnerSettingsOperations,
    *,
    article_id: ArticleId,
    title: str,
    platform: str,
    summary: str = "",
    keywords: Sequence[str] = (),
    comment: str | None = None,
    previous_content: str | None = None,
) -> dict[str, Any]:
    completions: list[Completion] = []
    prompts: list[str] = []

    async def run_step(messages: list[Message]) -> str:
        completion = await text_generator.complete_with_usage(messages)
        completions.append(completion)
        prompts.append("\n".join(f"[{m.role}] {m.text}" for m in messages))
        return completion.text

    voice = await _current_voice(owner_settings)
    outline = await run_step(
        _outline_messages(title, summary, keywords, platform, previous_content, comment, voice)
    )
    draft = await run_step(_draft_messages(title, platform, outline, voice))
    rewrite = await run_step(_rewrite_messages(platform, draft))
    sensitive = _is_money_or_legal(title, keywords)
    sources_text = await run_step(_sources_messages(rewrite, sensitive=sensitive))

    urls = _extract_urls(sources_text)
    reachable_urls = [url for url in urls if await url_checker.is_reachable(url)]
    content = _assemble_content(rewrite, reachable_urls)

    return {
        "article_id": article_id,
        "content": content,
        "prompt": "\n\n---\n\n".join(prompts),
        "model": completions[-1].model,
        "tokens": sum(c.tokens for c in completions),
        "cost": sum(c.tokens * _COST_PER_TOKEN for c in completions),
    }


def _outline_messages(
    title: str,
    summary: str,
    keywords: Sequence[str],
    platform: str,
    previous_content: str | None,
    comment: str | None,
    voice: str,
) -> list[Message]:
    system = (
        f"Ты - {voice}, пишущий Статью для площадки «{platform}». Составь аутлайн "
        "в Markdown: разделы — заголовками (##), подпункты каждого раздела — списком (-)."
    )
    if previous_content is not None:
        user = (
            f"Перегенерация статьи «{title}» по комментарию: {comment or '(без комментария)'}.\n\n"
            f"Текущая версия:\n{previous_content}"
        )
    else:
        user = (
            f"Тема: {title}\nОписание: {summary}\nКлючевые слова: {', '.join(keywords)}\n"
            "Составь аутлайн статьи по этой Теме."
        )
    return [Message(role="system", text=system), Message(role="user", text=user)]


def _draft_messages(title: str, platform: str, outline: str, voice: str) -> list[Message]:
    system = (
        f"Ты - {voice}. Напиши черновик статьи «{title}» для «{platform}» по аутлайну. "
        "Форматируй текст в Markdown: заголовки разделов — ##/###, перечисления — списком (-), "
        "ключевые термины и акценты — **жирным**."
    )
    return [Message(role="system", text=system), Message(role="user", text=outline)]


def _rewrite_messages(platform: str, draft: str) -> list[Message]:
    system = (
        f"Отредактируй черновик под тон и формат площадки «{platform}», сохранив факты. "
        "Сохрани и, где уместно, доработай Markdown-разметку: заголовки (##/###), списки (-), "
        "**выделения** — не превращай текст в плейн-текст."
    )
    return [Message(role="system", text=system), Message(role="user", text=draft)]


def _sources_messages(rewrite: str, *, sensitive: bool) -> list[Message]:
    if sensitive:
        system = (
            "Тема касается денег или права. Составь строгий Markdown-список источников (по одной "
            "ссылке на строку, начиная с «- ») для каждого утверждения с цифрой или юридическим "
            "фактом в тексте ниже."
        )
    else:
        system = (
            "Составь Markdown-список источников (по одной ссылке на строку, начиная с «- »), "
            "подтверждающих факты и цифры в тексте ниже."
        )
    return [Message(role="system", text=system), Message(role="user", text=rewrite)]


def _is_money_or_legal(title: str, keywords: Sequence[str]) -> bool:
    haystack = " ".join([title, *keywords]).lower()
    return any(word in haystack for word in _SENSITIVE_KEYWORDS)


def _extract_urls(text: str) -> list[str]:
    return [url.rstrip(".,;)") for url in _URL_RE.findall(text)]


def _assemble_content(body: str, urls: list[str]) -> str:
    if not urls:
        return body
    sources = "\n".join(f"- {url}" for url in urls)
    return f"{body}\n\nИсточники:\n{sources}"
