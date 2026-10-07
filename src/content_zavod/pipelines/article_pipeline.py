"""generate_article / regenerate_article Job Handlers (#94):

    research -> outline      once per Тема (`TopicResearcher`, cached per Тема)
    draft -> rewrite         per Площадка, only from the Тема's evidence

Research finds pages via the search provider, has the model extract facts strictly from
each page's text and keeps only facts whose verbatim quote is really on the page; the
shared outline is built from that evidence. Both are reused by the other Площадка and by
every Перегенерация, so the Статьи of one Тема can't disagree on facts. The draft cites
evidence with `[E1]` markers; this module turns them into numbered `[1]` references and
appends «Источники» listing only URLs from the evidence bundle - the model never writes a
source list, and any other URL it put in the text is dropped (the Проект's link excepted).
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

With a Проект set (#98), draft/rewrite are asked for one closing CTA to it, but the link
itself is the code's job: `_ensure_project_link` keeps exactly one exact copy of the URL in
the body (appending a CTA line when the model dropped or mangled it), and the foreign-URL
filter never removes links into the Проект.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Protocol

from ..domain import ArticleId, ArticleView, PlanItemId, ResearchBundle
from ..job_queue import JobHandler
from ..personas import platform_profile
from ..settings import Project, SettingsReader
from ..yandex import DEFAULT_TEMPERATURE, Completion, Message, TextGenerator
from .article_prompts import draft_messages, rewrite_messages
from .plan_pipeline import PlanItemReader
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
    "research_extract": "research-extract-v1",
    "outline": "outline-v4",
    "draft": "draft-v5",
    "rewrite": "rewrite-v5",
}

SOURCES_HEADING = "Источники:"


class ArticleReader(Protocol):
    async def get(self, article_id: ArticleId) -> ArticleView: ...


def make_generate_article_handler(
    text_generator: TextGenerator,
    researcher: TopicResearcher,
    settings: SettingsReader,
) -> JobHandler:
    async def handle(payload: dict[str, Any]) -> dict[str, Any]:
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
            article_id=ArticleId(payload["article_id"]),
            # Jobs enqueued before #94 carry no plan_item_id: researched, just not cached.
            plan_item_id=PlanItemId(plan_item_id) if plan_item_id else None,
            brief=brief,
            title=payload["title"],
            platform=payload["platform"],
        )

    return handle


def make_regenerate_article_handler(
    article_reader: ArticleReader,
    item_reader: PlanItemReader,
    text_generator: TextGenerator,
    researcher: TopicResearcher,
    settings: SettingsReader,
) -> JobHandler:
    async def handle(payload: dict[str, Any]) -> dict[str, Any]:
        article_id = ArticleId(payload["article_id"])
        view = await article_reader.get(article_id)
        item = await item_reader.get_item(view.plan_item_id)
        brief = TopicBrief(title=item.title, summary=item.summary, keywords=tuple(item.keywords))
        return await _run_pipeline(
            text_generator,
            researcher,
            settings,
            article_id=article_id,
            plan_item_id=view.plan_item_id,
            brief=brief,
            title=view.title,
            platform=view.platform,
            comment=payload.get("comment"),
            previous_content=strip_generated_parts(view.content.decode("utf-8")),
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
    article_id: ArticleId,
    plan_item_id: PlanItemId | None,
    brief: TopicBrief,
    title: str,
    platform: str,
    comment: str | None = None,
    previous_content: str | None = None,
) -> dict[str, Any]:
    steps = _Steps(text_generator)
    try:
        owner_settings = await settings.read()
        persona, custom_persona = owner_settings.persona, owner_settings.custom_persona
        project = owner_settings.project
        profile = platform_profile(platform)
        research = await researcher.prepare(brief, plan_item_id=plan_item_id, steps=steps)
        draft = await steps.llm(
            "draft",
            draft_messages(
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
            ),
        )
        rewrite = await steps.llm(
            "rewrite",
            rewrite_messages(
                draft=draft,
                comment=comment,
                persona=persona,
                custom_persona=custom_persona,
                profile=profile,
                project=project,
            ),
        )
        content = assemble_content(rewrite, research.bundle, project)
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


def _is_money_or_legal(title: str, keywords: Sequence[str]) -> bool:
    haystack = " ".join([title, *keywords]).lower()
    return any(word in haystack for word in _SENSITIVE_KEYWORDS)


def assemble_content(body: str, bundle: ResearchBundle, project: Project | None) -> str:
    """The model's text -> the Статья: foreign URLs out, `[E1]` -> `[1]`, the Проект link
    exactly once, «Источники» from the evidence bundle only. Nothing about missing evidence
    goes into the text: that is `research_status` metadata, shown on the Article card."""
    body = _drop_foreign_urls(body, {source.url for source in bundle.sources}, project)
    body, cited = _number_citations(body, bundle)
    body = _ensure_project_link(body, project)
    if bundle.has_evidence:
        if cited:
            lines = [
                f"{number}. {_source_line(bundle, url)}" for number, url in enumerate(cited, 1)
            ]
        else:
            # Written from the evidence but the model dropped every marker: the pages are
            # still what the text stands on, so list them without numbering.
            lines = [f"- {_source_line(bundle, source.url)}" for source in bundle.sources]
        body = f"{body.rstrip()}\n\n{SOURCES_HEADING}\n" + "\n".join(lines)
    return body


def _source_line(bundle: ResearchBundle, url: str) -> str:
    source = bundle.source_for(url)
    title = source.title.strip() if source is not None else ""
    return f"{title} — {url}" if title else url


_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]*)\]\((https?://[^)\s]+)\)")
_BARE_URL_RE = re.compile(r"[ \t]*https?://[^\s<>()\[\]]+")
_TRAILING_PUNCT = ".,;:!?»\"'"


def _normalized_link(url: str) -> str:
    url = re.sub(r"^https?://", "", url.strip().lower())
    return url.removeprefix("www.").rstrip("/")


def _drop_foreign_urls(body: str, allowed: set[str], project: Project | None) -> str:
    """No URL outside the evidence bundle survives (docs/plans/...-saas.md, E2) - except
    links into the Проект, mangled ones included, which `_ensure_project_link` handles."""
    project_link = _normalized_link(project.url) if project is not None else None

    def keep(url: str) -> bool:
        if url in allowed:
            return True
        return project_link is not None and _normalized_link(url).startswith(project_link)

    def replace_markdown(match: re.Match[str]) -> str:
        return match.group(0) if keep(match.group(2)) else match.group(1)

    def replace_bare(match: re.Match[str]) -> str:
        text = match.group(0)
        stripped = text.rstrip(_TRAILING_PUNCT)
        trailing = text[len(stripped) :]
        return text if keep(stripped.strip()) else trailing

    body = _MARKDOWN_LINK_RE.sub(replace_markdown, body)
    return _BARE_URL_RE.sub(replace_bare, body)


# `[E1]`, `[E1, E3]`, `[E1; E3]`, `[E1–E3]`, also with a Cyrillic «Е» (YandexGPT writes
# Russian and slips into it) or in parentheses - whatever the spelling, no raw marker may
# reach the published text.
_E = "[EeЕе]"
_MARKER_RE = re.compile(rf"[ \t]*[\[(]\s*({_E}\s*\d+(?:\s*[,;–—-]\s*{_E}?\s*\d+)*)\s*[\])]")
_HEADING_LINE_RE = re.compile(r"^[ \t]*#{1,6}[ \t].*$", re.MULTILINE)
_RANGE_RE = re.compile(r"(\d+)\s*[–—-]\s*" + _E + r"?\s*(\d+)")


def _marker_ids(inner: str) -> list[str]:
    numbers: list[int] = []
    for part in re.split(r"\s*[,;]\s*", inner):
        span = _RANGE_RE.search(part)
        if span is not None:
            first, last = int(span.group(1)), int(span.group(2))
            if first <= last <= first + 20:
                numbers.extend(range(first, last + 1))
                continue
        numbers.extend(int(n) for n in re.findall(r"\d+", part))
    return [f"E{n}" for n in numbers]


def _number_citations(body: str, bundle: ResearchBundle) -> tuple[str, list[str]]:
    """`[E1]`/`[E1, E3]` -> `[1]`/`[1, 2]`, numbered per source URL by first appearance;
    markers for unknown ids, and every marker in a heading, are dropped. Returns the body
    and the cited URLs in order."""
    url_by_id = {item.id: item.url for item in bundle.evidence}
    cited: list[str] = []

    def replace(match: re.Match[str]) -> str:
        numbers: list[int] = []
        for evidence_id in _marker_ids(match.group(1)):
            url = url_by_id.get(evidence_id)
            if url is None:
                continue
            if url not in cited:
                cited.append(url)
            number = cited.index(url) + 1
            if number not in numbers:
                numbers.append(number)
        if not numbers:
            return ""
        return " [" + ", ".join(str(n) for n in numbers) + "]"

    body = _HEADING_LINE_RE.sub(lambda line: _MARKER_RE.sub("", line.group(0)), body)
    return _MARKER_RE.sub(replace, body), cited


def strip_generated_parts(content: str) -> str:
    """The previous Версия as the model should see it on Перегенерация: without the
    «Источники» appendix and the numbered references - both of which the code adds itself
    and which would only confuse the `[E1]` markers."""
    heading = f"\n\n{SOURCES_HEADING}\n"
    if heading in content:
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
    a missing or truly mangled URL (a typo) gets a plain CTA line appended."""
    if project is None:
        return body
    url = re.escape(project.url)
    occurrence = re.compile(rf"\[([^\]\n]*)\]\({url}\)|[ \t]*{url}{_URL_END}")
    matches = list(occurrence.finditer(body))
    if not matches:
        variant = _last_project_link_variant(body, project.url)
        if variant is not None:
            return body[: variant.start()] + project.url + body[variant.end() :]
        return f"{body.rstrip()}\n\n{project.description.rstrip(' .!?…')}: {project.url}"
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
