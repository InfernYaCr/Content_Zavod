import pytest

from content_zavod.domain import ArticleId, ArticleView
from content_zavod.pipelines.article_pipeline import (
    make_generate_article_handler,
    make_regenerate_article_handler,
)
from content_zavod.yandex import Completion, Message


class ScriptedTextGenerator:
    """Returns each queued completion in order, one per `complete_with_usage` call."""

    def __init__(self, completions: list[Completion]) -> None:
        self._completions = list(completions)
        self.calls: list[list[Message]] = []

    async def complete_with_usage(self, messages: list[Message], *, temperature: float = 0.7) -> Completion:
        self.calls.append(messages)
        return self._completions.pop(0)


class FakeUrlReachabilityChecker:
    def __init__(self, reachable: set[str]) -> None:
        self._reachable = reachable
        self.checked: list[str] = []

    async def is_reachable(self, url: str) -> bool:
        self.checked.append(url)
        return url in self._reachable


class FakeArticleReader:
    def __init__(self, view: ArticleView) -> None:
        self._view = view
        self.requested: list[ArticleId] = []

    async def get(self, article_id: ArticleId) -> ArticleView:
        self.requested.append(article_id)
        return self._view


def _completion(text: str, *, tokens: int = 10, model: str = "yandexgpt/latest") -> Completion:
    return Completion(text=text, model=model, tokens=tokens)


@pytest.mark.asyncio
async def test_generate_article_runs_four_steps_and_assembles_reachable_sources() -> None:
    text_generator = ScriptedTextGenerator(
        [
            _completion("outline", tokens=5),
            _completion("draft", tokens=7),
            _completion("Final article body.", tokens=9),
            _completion("See https://good.example/a and https://bad.example/b", tokens=3),
        ]
    )
    url_checker = FakeUrlReachabilityChecker({"https://good.example/a"})
    handler = make_generate_article_handler(text_generator, url_checker)

    output = await handler(
        {
            "article_id": "article-1",
            "plan_item_id": "item-1",
            "title": "Как выбрать CRM",
            "summary": "обзор CRM",
            "keywords": ["crm"],
            "platform": "zen",
        }
    )

    assert output["article_id"] == "article-1"
    assert "Final article body." in output["content"]
    assert "https://good.example/a" in output["content"]
    assert "https://bad.example/b" not in output["content"]
    assert output["tokens"] == 5 + 7 + 9 + 3
    assert output["model"] == "yandexgpt/latest"
    assert len(text_generator.calls) == 4


@pytest.mark.asyncio
async def test_generate_article_omits_sources_section_when_nothing_is_reachable() -> None:
    text_generator = ScriptedTextGenerator(
        [
            _completion("outline"),
            _completion("draft"),
            _completion("Body only."),
            _completion("no urls here"),
        ]
    )
    url_checker = FakeUrlReachabilityChecker(set())
    handler = make_generate_article_handler(text_generator, url_checker)

    output = await handler(
        {"article_id": "a", "title": "T", "summary": "", "keywords": [], "platform": "vc"}
    )

    assert output["content"] == "Body only."


@pytest.mark.asyncio
async def test_generate_article_uses_a_stricter_sources_prompt_for_money_or_legal_topics() -> None:
    text_generator = ScriptedTextGenerator(
        [_completion("outline"), _completion("draft"), _completion("rewrite"), _completion("")]
    )
    url_checker = FakeUrlReachabilityChecker(set())
    handler = make_generate_article_handler(text_generator, url_checker)

    await handler(
        {
            "article_id": "a",
            "title": "Как получить ипотека без первоначального взноса",
            "summary": "",
            "keywords": ["кредит"],
            "platform": "vc",
        }
    )

    sources_step_system_prompt = text_generator.calls[3][0].text
    assert "денег" in sources_step_system_prompt.lower() or "права" in sources_step_system_prompt.lower()


@pytest.mark.asyncio
async def test_regenerate_article_sources_facts_from_the_current_version_not_a_fresh_payload() -> None:
    view = ArticleView(
        id="article-1",
        plan_item_id="item-1",
        title="Topic A",
        platform="zen",
        content=b"old content",
    )
    article_reader = FakeArticleReader(view)
    text_generator = ScriptedTextGenerator(
        [_completion("outline"), _completion("draft"), _completion("new body"), _completion("")]
    )
    url_checker = FakeUrlReachabilityChecker(set())
    handler = make_regenerate_article_handler(article_reader, text_generator, url_checker)

    output = await handler({"article_id": "article-1", "comment": "shorter please"})

    assert article_reader.requested == ["article-1"]
    assert output["content"] == "new body"
    outline_user_prompt = text_generator.calls[0][1].text
    assert "old content" in outline_user_prompt
    assert "shorter please" in outline_user_prompt
