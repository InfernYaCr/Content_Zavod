"""generate_article / regenerate_article Job Handlers (#94):

    research -> outline      once per Тема (`TopicResearcher`, cached per Тема)
    draft -> rewrite         per Площадка, only from the Тема's evidence

Research finds pages via the search provider, has the model extract facts strictly from
each page's text and keeps only facts whose verbatim quote is really on the page; the
shared outline is built from that evidence. Both are reused by the other Площадка and by
every Перегенерация, so the Статьи of one Тема can't disagree on facts. The draft cites
evidence with `[E1]` markers to stay on the facts; this module strips them, and every URL
the model put in the text is dropped (the Проект's link excepted). Sources stay in the
Тема's Исследование (and the Версия's prompt) and are not shown in the Статья (#144).
Without evidence (search unavailable or nothing verifiable found) the draft is told not to
state concrete facts/numbers; the Статья text itself stays clean (it is exported and
published as is) - the Job reports `research_status`, the Версия stores it, and the
Telegram Article card warns the editor.

Both job types converge on one shared pipeline core (`_run_pipeline`):
`regenerate_article` is a refinement of the prior result, not a different pipeline - it
reuses the Тема's cached research/outline and passes the current Версия (without its
generated appendix) to the draft as `previous_content`, plus the editor's comment to draft
and rewrite (#85). Money/legal Темы get a stricter evidence rule in the draft, selected by
a keyword/title heuristic.

Only draft and rewrite mention the Персона and Площадка (the outline is shared across
Площадки on purpose); Персона is Owner-editable (#37, renamed from Голос in #50), read
fresh from `SettingsReader` on every call so a `/set_persona` takes effect without a
restart - platform tone is layered on top of it, not instead of it.

With a Проект set (#98), draft/rewrite are asked for one closing invitation to it, but the
link itself is the code's job: `_ensure_project_link` keeps exactly one exact copy of the URL
in the body (appending a neutral line when the model dropped or mangled it), and the
foreign-URL filter keeps only spellings of that very link (#133).

The Аудитория (#100) is read fresh like the Персона and goes to draft/rewrite only, as
INPUT_DATA: the outline is cached per Тема and stays reader-neutral (see `article_prompts`).

Every step gets today's date in the Пространство's time zone (#145). The Статья's text is never
checked by regex for years - a true fact about the past stays; only a past-year tail on a `#`/`##`
heading («…: что использовать в 2024 году») is cut, as the Тема title it came from would be.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any, Protocol

from ..domain import ArticleId, ArticleView, PlanItemId
from ..job_queue import JobHandler
from ..personas import platform_profile
from ..settings import Project, SettingsReader
from ..workspaces import WorkspaceId
from ..yandex import DEFAULT_TEMPERATURE, Completion, Message, TextGenerator
from .article_prompts import draft_messages, rewrite_messages, strip_past_year_tail
from .plan_pipeline import PlanItemReader, WorkspaceTimezone, local_today, utc_timezone
from .provenance import StepRecord, StepRecorder
from .topic_research import TopicBrief, TopicResearcher

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

# Bumped whenever a step's prompt-building function changes shape, so a stored Версия's
# provenance stays explainable without reading logs (#74). The LLM `sources` step is gone
# (#94): «Источники» come from the evidence bundle.
_PROMPT_VERSIONS = {
    "research_extract": "research-extract-v2",
    "outline": "outline-v5",
    "draft": "draft-v9",
    "rewrite": "rewrite-v9",
}

SOURCES_HEADING = "Источники:"


class ArticleReader(Protocol):
    async def get(self, workspace_id: WorkspaceId, article_id: ArticleId) -> ArticleView: ...


def make_generate_article_handler(
    text_generator: TextGenerator,
    researcher: TopicResearcher,
    settings: SettingsReader,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    workspace_timezone: WorkspaceTimezone = utc_timezone,
) -> JobHandler:
    async def handle(workspace_id: WorkspaceId, payload: dict[str, Any]) -> dict[str, Any]:
        plan_item_id = payload.get("plan_item_id")
        brief = TopicBrief(
            title=payload["title"],
            summary=payload.get("summary", ""),
            keywords=tuple(payload.get("keywords", [])),
        )
        return await _run_pipeline(
            text_generator,
            researcher,
            settings,
            workspace_id=workspace_id,
            article_id=ArticleId(payload["article_id"]),
            # Jobs enqueued before #94 carry no plan_item_id: researched, just not cached.
            plan_item_id=PlanItemId(plan_item_id) if plan_item_id else None,
            brief=brief,
            title=payload["title"],
            platform=payload["platform"],
            today=await local_today(now, workspace_timezone, workspace_id),
        )

    return handle


def make_regenerate_article_handler(
    article_reader: ArticleReader,
    item_reader: PlanItemReader,
    text_generator: TextGenerator,
    researcher: TopicResearcher,
    settings: SettingsReader,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    workspace_timezone: WorkspaceTimezone = utc_timezone,
) -> JobHandler:
    async def handle(workspace_id: WorkspaceId, payload: dict[str, Any]) -> dict[str, Any]:
        article_id = ArticleId(payload["article_id"])
        # The Job's Workspace: a payload naming another's Статья finds nothing (ADR-0015).
        view = await article_reader.get(workspace_id, article_id)
        item = await item_reader.get_item(workspace_id, view.plan_item_id)
        brief = TopicBrief(title=item.title, summary=item.summary, keywords=tuple(item.keywords))
        return await _run_pipeline(
            text_generator,
            researcher,
            settings,
            workspace_id=workspace_id,
            article_id=article_id,
            plan_item_id=view.plan_item_id,
            brief=brief,
            title=view.title,
            platform=view.platform,
            comment=payload.get("comment"),
            previous_content=strip_generated_parts(view.content.decode("utf-8")),
            today=await local_today(now, workspace_timezone, workspace_id),
        )

    return handle


class _Steps:
    """The `StepRunner` lent to `TopicResearcher`, also used for draft/rewrite: every call
    is recorded for the Версия's prompt/model/tokens and the Job's provenance (#74)."""

    def __init__(self, text_generator: TextGenerator) -> None:
        self._text_generator = text_generator
        self.completions: list[Completion] = []
        self.prompts: list[str] = []
        self.recorder = StepRecorder()

    async def llm(self, step_name: str, messages: list[Message]) -> str:
        prompt_text = "\n".join(f"[{m.role}] {m.text}" for m in messages)
        completion = await self._text_generator.complete_with_usage(messages)
        self.completions.append(completion)
        self.prompts.append(prompt_text)
        self.recorder.add(
            StepRecord.from_completion(
                completion,
                step_name=step_name,
                prompt_template_version=_PROMPT_VERSIONS[step_name],
                prompt_text=prompt_text,
                params={"temperature": DEFAULT_TEMPERATURE},
            )
        )
        return completion.text

    def record(self, step: StepRecord) -> None:
        self.recorder.add(step)


async def _run_pipeline(
    text_generator: TextGenerator,
    researcher: TopicResearcher,
    settings: SettingsReader,
    *,
    workspace_id: WorkspaceId,
    article_id: ArticleId,
    plan_item_id: PlanItemId | None,
    brief: TopicBrief,
    title: str,
    platform: str,
    today: date,
    comment: str | None = None,
    previous_content: str | None = None,
) -> dict[str, Any]:
    steps = _Steps(text_generator)
    try:
        owner_settings = await settings.read(workspace_id)
        persona, custom_persona = owner_settings.persona, owner_settings.custom_persona
        project = owner_settings.project
        audience = owner_settings.audience
        profile = platform_profile(platform)
        research = await researcher.prepare(
            brief, workspace_id=workspace_id, plan_item_id=plan_item_id, steps=steps, today=today
        )
        draft = await steps.llm(
            "draft",
            draft_messages(
                today=today,
                title=title,
                summary=brief.summary,
                outline=research.outline,
                bundle=research.bundle,
                previous_content=previous_content,
                comment=comment,
                persona=persona,
                custom_persona=custom_persona,
                profile=profile,
                project=project,
                sensitive=_is_money_or_legal(title, brief.keywords),
                audience=audience,
                style_profile=owner_settings.style_profile,
            ),
        )
        rewrite = await steps.llm(
            "rewrite",
            rewrite_messages(
                today=today,
                draft=draft,
                comment=comment,
                persona=persona,
                custom_persona=custom_persona,
                profile=profile,
                project=project,
                audience=audience,
                style_profile=owner_settings.style_profile,
            ),
        )
        content = assemble_content(_fresh_headings(rewrite, today), project)
    except Exception as exc:
        raise steps.recorder.fail(exc, article_id=article_id) from exc

    # A total is only as good as its worst component - one step with unreported usage or
    # unconfigured pricing (an LLM call or the web search) makes the whole Версия's
    # tokens/cost unknown, not a partial sum quietly presented as final (#74).
    completions = steps.completions
    recorded = steps.recorder.steps
    usage_complete = not any(c.usage_missing for c in completions)
    cost_complete = not any(step.cost is None for step in recorded)
    return {
        "article_id": article_id,
        "content": content,
        "prompt": "\n\n---\n\n".join(steps.prompts),
        "model": completions[-1].model,
        "tokens": sum(c.tokens for c in completions) if usage_complete else None,
        "cost": sum(step.cost or 0.0 for step in recorded) if cost_complete else None,
        "research_status": research.bundle.status,
        "steps": steps.recorder.as_output(),
    }


_TOP_HEADING_RE = re.compile(r"^([ \t]*#{1,2}[ \t]+)(.+)$", re.MULTILINE)


def _fresh_headings(body: str, today: date) -> str:
    """`#`/`##` headings without a past-year tail (#145); the text itself is left alone."""
    return _TOP_HEADING_RE.sub(lambda m: m.group(1) + strip_past_year_tail(m.group(2), today), body)


def _is_money_or_legal(title: str, keywords: Sequence[str]) -> bool:
    haystack = " ".join([title, *keywords]).lower()
    return any(word in haystack for word in _SENSITIVE_KEYWORDS)


def assemble_content(body: str, project: Project | None) -> str:
    """The model's text -> the Статья: every URL but the Проект link out (research pages
    included), `[E1]` markers out, the Проект link exactly once. Sources stay in the Тема's
    Исследование and are not shown in the Статья (#144); nothing about missing evidence goes
    into the text either: that is `research_status` metadata, shown on the Article card."""
    body = _drop_foreign_urls(body, project)
    body = _MARKER_RE.sub(_marker_gap, body)
    # A list the model wrote around dropped URLs leaves empty items and maybe a bare heading.
    body = _EMPTY_SOURCES_RE.sub(_empty_sources_gap, body)
    body = _EMPTY_ITEM_RE.sub("", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return _ensure_project_link(body, project)


def _marker_gap(match: re.Match[str]) -> str:
    """`продажи [E1].` -> `продажи.`, `[E1] Начало` -> `Начало`, `([E1])` -> nothing, but
    `продажи[E1]и` -> `продажи и`: a space only where the markers stood between two words."""
    before = match.string[match.start() - 1 : match.start()]
    after = match.string[match.end() : match.end() + 1]
    if not before or before in "\n([«" or not after or after in "\n" + _CLOSING:
        return ""
    return " " if match.group("tail") or after.isalnum() or after == "«" else ""


def _empty_sources_gap(match: re.Match[str]) -> str:
    """A «Источники» heading goes when its items were all links (now empty) or nothing
    follows it; a heading over real items stays."""
    rest = match.string[match.end() :]
    return "" if re.search(r"[-*•\d]", match.group(1)) or not rest.strip() else match.group(0)


# Schemes are case-insensitive: `HTTPS://evil.com` is just as foreign (#133).
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]*)\]\((https?://[^)\s]+)\)", re.IGNORECASE)
_BARE_URL_RE = re.compile(r"[ \t]*https?://[^\s<>()\[\]]+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?»\"'"
_CLOSING = _TRAILING_PUNCT + ")]…"


def _normalized_link(url: str) -> str:
    url = re.sub(r"^https?://", "", url.strip().lower())
    return url.removeprefix("www.").rstrip("/")


def _drop_foreign_urls(body: str, project: Project | None) -> str:
    """No URL survives but the Проект link (research pages included, #144) - also in another
    spelling (`http://`, `www.`, case, trailing `/`), which is rewritten into the exact
    `project.url` right here. Equality, not a prefix (#133): `https://t.me/name_spam` and
    `https://example.ru.evil.com` are foreign."""
    project_link = _normalized_link(project.url) if project is not None else None

    def kept(url: str) -> str | None:
        if project is not None and _normalized_link(url) == project_link:
            return project.url
        return None

    def replace_markdown(match: re.Match[str]) -> str:
        url = kept(match.group(2))
        return match.group(1) if url is None else f"[{match.group(1)}]({url})"

    def replace_bare(match: re.Match[str]) -> str:
        text = match.group(0)
        stripped = text.rstrip(_TRAILING_PUNCT)
        trailing = text[len(stripped) :]
        url = kept(stripped.strip())
        if url is None:
            return trailing
        return stripped[: len(stripped) - len(stripped.lstrip())] + url + trailing

    body = _MARKDOWN_LINK_RE.sub(replace_markdown, body)
    return _BARE_URL_RE.sub(replace_bare, body)


# `[E1]`, `[E1, E3]`, `[E1; E3]`, `[E1–E3]`, also with a Cyrillic «Е» (YandexGPT writes
# Russian and slips into it) or in parentheses - whatever the spelling, no raw marker may
# reach the published text.
_E = "[EeЕе]"
_IDS = rf"{_E}\s*\d+(?:\s*[,;–—-]\s*{_E}?\s*\d+)*"
# A run of markers (`[E1][E2]`, `([E1])`) is one match, so it leaves at most one gap.
_ONE_MARKER = rf"[ \t]*(?:[\[(]\s*{_IDS}\s*[\])]|\(\s*(?:\[\s*{_IDS}\s*\]\s*)+\))"
_MARKER_RE = re.compile(rf"(?:{_ONE_MARKER})+(?P<tail>[ \t]*)")
_LINE_GAP = r"\n[ \t]*(?:(?:[-*•]|\d+[.)])[ \t]*)?"
_EMPTY_ITEM_RE = re.compile(r"^[ \t]*(?:[-*•]|\d+[.)])[ \t]*(?:\n|\Z)", re.MULTILINE)
_EMPTY_SOURCES_RE = re.compile(
    rf"^[ \t]*(?:#{{1,6}}[ \t]+)?\**Источники:?\**[ \t]*((?:{_LINE_GAP})*)(?=\n[ \t]*\n|\s*\Z)",
    re.MULTILINE | re.IGNORECASE,
)


def strip_generated_parts(content: str) -> str:
    """The previous Версия as the model should see it on Перегенерация. Versions made
    before #144 end with a «Источники» appendix and carry numbered `[1]` references - both
    would only confuse the `[E1]` markers, so they go. Without the appendix the text is
    already clean, and a `[2024]` in it is the author's, not a reference."""
    heading = f"\n\n{SOURCES_HEADING}\n"
    if heading not in content:
        return content
    content = content[: content.rindex(heading)]
    return re.sub(r"[ \t]*\[\d+(?:,\s*\d+)*\]", "", content)


# The occurrence must end where the URL ends - `https://t.me/name_2` or `.../name/12` is a
# different link, not a copy of `https://t.me/name`.
_URL_END = r"(?![\w/?#=&%~+@-]|[.:]\w)"


def _ensure_project_link(body: str, project: Project | None) -> str:
    """Exactly one exact `project.url` in the body (#98). Repeats are cut down to the last
    occurrence - the closing CTA - keeping a Markdown link's text. Without an exact copy, the
    last recognizable spelling of the same link (`t.me/name`, `@name`, `http://...`) is
    rewritten into the exact URL in place, so the model's own CTA sentence stays the only CTA;
    a missing URL gets a neutral line appended."""
    if project is None:
        return body
    url = re.escape(project.url)
    occurrence = re.compile(rf"\[([^\]\n]*)\]\({url}\)|[ \t]*{url}{_URL_END}")
    matches = list(occurrence.finditer(body))
    if not matches:
        variant = _last_project_link_variant(body, project.url)
        if variant is not None:
            return body[: variant.start()] + project.url + body[variant.end() :]
        where = "в Telegram-канале автора" if project.kind == "telegram" else "на сайте автора"
        return f"{body.rstrip()}\n\nБольше по теме — {where}: {project.url}"
    for match in reversed(matches[:-1]):
        body = body[: match.start()] + (match.group(1) or "") + body[match.end() :]
    return body


def _last_project_link_variant(body: str, project_url: str) -> re.Match[str] | None:
    bare = project_url.removeprefix("https://")
    spellings = [rf"(?:https?://)?(?:www\.)?{re.escape(bare)}"]
    if bare.startswith("t.me/") and re.fullmatch(r"[A-Za-z]\w{4,31}", bare[5:]):
        spellings.append(rf"@{re.escape(bare[5:])}")
    variant = re.compile(rf"(?<![\w@/.])(?:{'|'.join(spellings)}){_URL_END}", re.IGNORECASE)
    matches = list(variant.finditer(body))
    return matches[-1] if matches else None
