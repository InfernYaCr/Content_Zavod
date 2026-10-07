"""Render the Инструкция's pictures (#114) into `src/content_zavod/assets/guide/`.

    uv run --with playwright==1.56.0 python scripts/render_guide_assets.py

Every slide of `telegram.guide.GUIDE_SLIDES` gets `<slide key>.png`: a mockup of the bot's
screen - its real message text and inline buttons, built by the same functions the bot uses
(`render_plan_text`, `SETTING_FIELDS`, the onboarding step screens, …) - under a header with
the slide's title. `BANNERS` get a flat illustration with a title. Rerun after changing a
text: the pictures follow the code. The bot itself never needs a browser - it sends the PNGs
committed to the repository.

A designer can replace any PNG by hand: keep the file name (the bot finds pictures by name)
and the size (slides 1280×960, banners 1280×640). The bot notices the new file by its hash and
uploads it again on the next send.

Next to the PNGs, `sources.json` keeps a hash of each picture's HTML - the texts, buttons and
styles it was drawn from. `tests/telegram/test_guide.py` recomputes them, so a text changed in
the code without re-rendering its picture fails the tests instead of drifting silently. (A
designer's hand-made PNG keeps passing; re-rendering would overwrite it, so after a text change
the designer's picture needs updating by hand too, then rerun with `--sources-only`.)

Options: `--only welcome,niche` renders some pictures only; `--html DIR` also saves each
picture's HTML, to tweak the design in a browser. Needs Playwright's Chromium: the
`PLAYWRIGHT_BROWSERS_PATH` one, or `playwright install chromium`. Fonts: Liberation Sans or
DejaVu Sans for Cyrillic, Noto Color Emoji for emoji - nothing is downloaded.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from aiogram.types import InlineKeyboardMarkup

from content_zavod.domain import HubArticleCell, HubTopic, PlanHubView
from content_zavod.scheduling import (
    DEFAULT_DAY_OF_WEEK,
    DEFAULT_HOUR,
    DEFAULT_MINUTE,
    ScheduleConfig,
)
from content_zavod.settings import SettingsService
from content_zavod.telegram.article_card import render_article_card_text
from content_zavod.telegram.asset_photos import ASSETS_DIR
from content_zavod.telegram.gateway import (
    build_article_keyboard,
    build_join_request_keyboard,
    build_plan_keyboard,
    build_request_access_keyboard,
    render_plan_text,
)
from content_zavod.telegram.guide import BANNERS, GUIDE_SLIDES, Slide, example_of
from content_zavod.telegram.input_prompt import _question_text, build_cancel_input_keyboard
from content_zavod.telegram.main_menu import build_main_menu_keyboard
from content_zavod.telegram.onboarding import (
    ONBOARDING_INPUT_KIND,
    ONBOARDING_STEPS,
    Onboarding,
    StepRef,
    onboarding_fields,
)
from content_zavod.telegram.plan_hub import render_hub_screen
from content_zavod.telegram.settings_screen import (
    BACK_TO_MENU,
    build_schedule_keyboard,
    render_schedule_text,
)
from content_zavod.telegram.texts import (
    BACK_BUTTON,
    MENU_TEXT,
    PLAN_GENERATE_BUTTON,
    PLAN_MISSING_TEXT,
    UNREGISTERED_TEXT,
    WELCOME_TEXT,
    format_week_range,
    schedule_text,
    steps_count_text,
)
from content_zavod.telegram.types import ArticleView, PlanItemView, PlanView

SLIDE_SIZE = (1280, 960)
BANNER_SIZE = (1280, 640)
SAMPLE_WEEK = "2026-W42"

# One palette for everything: deep ink, calm teal, warm sand, a coral and a mustard accent.
INK = "#22304F"
TEAL = "#2A9D8F"
SAND = "#F6F0E6"
CORAL = "#E76F51"
MUSTARD = "#E9C46A"
WALLPAPER = "#DCE9E6"


# --- what a picture shows ---


@dataclass(frozen=True)
class Bubble:
    text: str
    buttons: Sequence[Sequence[str]] = ()
    mine: bool = False  # the user's answer, on the right
    service: bool = False  # a centred grey note between messages, like Telegram's dates


@dataclass(frozen=True)
class Mockup:
    title: str
    note: str
    bubbles: Sequence[Bubble] = field(default_factory=tuple)


def _buttons(markup: InlineKeyboardMarkup | Sequence[Sequence[object]] | None) -> list[list[str]]:
    if markup is None:
        return []
    rows = markup.inline_keyboard if isinstance(markup, InlineKeyboardMarkup) else markup
    return [[button.text for button in row] for row in rows]  # type: ignore[attr-defined]


class _MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def set_if_changed(self, key: str, value: str) -> bool:
        changed = self.values.get(key) != value
        self.values[key] = value
        return changed


async def _step_bubble(key: str) -> Bubble:
    """The onboarding question for `key`, exactly as the private chat shows it."""
    store = _MemoryStore()
    onboarding = Onboarding(
        None,  # type: ignore[arg-type]  # the step screen reads Настройки only
        SettingsService(store),
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )
    text, rows = await onboarding._step_screen(StepRef(key))
    keyboard = build_cancel_input_keyboard(ONBOARDING_INPUT_KIND, rows)
    return Bubble(_question_text(text, True), _buttons(keyboard))


def _answer_for(key: str) -> str | None:
    step = ONBOARDING_STEPS.get(key)
    return example_of(step.question) if step is not None and step.question else None


async def _slide_mockup(slide: Slide) -> Mockup:
    note = "Для Владельца" if slide.owner_only else "Для всей команды"
    if slide.optional:
        note += " · необязательно"
    if slide.key == "about":
        bubbles = [
            Bubble("Вы нажали /start", service=True),
            Bubble(WELCOME_TEXT),
            Bubble(MENU_TEXT, _buttons(build_main_menu_keyboard("content_manager"))),
        ]
    elif slide.key == "week":
        plan = PlanView(
            id="plan",
            week_label=SAMPLE_WEEK,
            items=[
                PlanItemView(id="1", title="Как посчитать окупаемость рекламы", status="approved"),
                PlanItemView(
                    id="2",
                    title="5 ошибок в воронке продаж малого бизнеса",
                    status="pending_review",
                ),
                PlanItemView(
                    id="3", title="CRM за выходные: с чего начать", status="pending_review"
                ),
            ],
        )
        hub_text, hub_keyboard = render_hub_screen(_sample_hub())
        bubbles = [
            Bubble("По Расписанию в чат команды приходит План", service=True),
            Bubble(render_plan_text(plan), _buttons(build_plan_keyboard(plan))),
            Bubble("После «✅ Утвердить всё» это же сообщение становится Хабом", service=True),
            Bubble(hub_text, _buttons(hub_keyboard)),
        ]
    elif slide.key in {setting.key for setting in onboarding_fields()}:
        bubbles = [
            Bubble("Так я спрашиваю при первой настройке", service=True),
            await _step_bubble(slide.key),
        ]
        answer = _answer_for(slide.key)
        if answer:
            bubbles.append(Bubble(answer, mine=True))
    elif slide.key == "schedule":
        config = ScheduleConfig(DEFAULT_DAY_OF_WEEK, DEFAULT_HOUR, DEFAULT_MINUTE)
        bubbles = [
            Bubble("🏠 Меню → 🕘 Расписание", service=True),
            Bubble(
                render_schedule_text(config),
                _buttons(build_schedule_keyboard(config, BACK_TO_MENU)),
            ),
        ]
    elif slide.key == "article":
        article = ArticleView(
            id="a",
            plan_item_id="1",
            title="Как посчитать окупаемость рекламы",
            platform="zen",
            content=(
                "# Как посчитать окупаемость рекламы\n\n"
                "Реклама окупается, когда каждый вложенный рубль возвращается с прибылью. "
                "Разберём на примере кофейни: сколько стоил гость, сколько он принёс и когда "
                "кампанию пора остановить."
            ).encode(),
            research_status="no_evidence",
        )
        keyboard = build_article_keyboard("a", "1", read_url="https://telegra.ph/example")
        bubbles = [
            Bubble("Готовая Статья приходит карточкой", service=True),
            Bubble(render_article_card_text(article), _buttons(keyboard)),
        ]
    elif slide.key == "roles":
        bubbles = [
            Bubble("Новый коллега открывает бота и нажимает /start", service=True),
            Bubble(UNREGISTERED_TEXT, _buttons(build_request_access_keyboard(1))),
            Bubble("Владельцу приходит заявка", service=True),
            Bubble(
                "Заявка на доступ от @anna_content (id 123456789).",
                _buttons(build_join_request_keyboard(1)),
            ),
            Bubble("Одобрили — коллега стал Контент-менеджером", service=True),
        ]
    elif slide.key == "faq":
        text = PLAN_MISSING_TEXT.format(
            week=format_week_range(SAMPLE_WEEK),
            schedule=schedule_text(DEFAULT_DAY_OF_WEEK, DEFAULT_HOUR, DEFAULT_MINUTE),
        )
        bubbles = [
            Bubble("Плана нет? 🏠 Меню → 📋 План недели", service=True),
            Bubble(text, [[PLAN_GENERATE_BUTTON], [BACK_BUTTON]]),
        ]
    else:
        bubbles = []
    return Mockup(slide.title, note, bubbles)


def _sample_hub() -> PlanHubView:
    def cells(zen: str, vc: str) -> list[HubArticleCell]:
        return [HubArticleCell("zen", zen, has_content=zen == "ready"), HubArticleCell("vc", vc)]

    return PlanHubView(
        id="plan",
        week_label=SAMPLE_WEEK,
        status="approved",
        topics=[
            HubTopic(
                "1", 1, "Как посчитать окупаемость рекламы", "ready", True, cells("ready", "ready")
            ),
            HubTopic(
                "2",
                2,
                "CRM за выходные: с чего начать",
                "pending",
                False,
                cells("ready", "pending"),
            ),
        ],
    )


# --- HTML ---

_FONTS = "'Liberation Sans', 'DejaVu Sans', 'Noto Color Emoji', sans-serif"

_BASE_CSS = f"""
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: 100%; height: 100%; }}
body {{ font-family: {_FONTS}; color: {INK}; overflow: hidden; }}
"""

_SLIDE_CSS = f"""
body {{
  background-color: {WALLPAPER};
  background-image:
    radial-gradient(circle at 20px 20px, rgba(42,157,143,.10) 3px, transparent 4px),
    radial-gradient(circle at 60px 60px, rgba(34,48,79,.06) 3px, transparent 4px);
  background-size: 80px 80px;
  display: flex; flex-direction: column;
}}
header {{
  background: {INK}; color: #fff; padding: 34px 56px 30px;
  display: flex; align-items: center; justify-content: space-between;
  border-bottom: 10px solid {TEAL};
}}
header h1 {{ font-size: 56px; font-weight: 700; letter-spacing: .5px; }}
header .note {{
  font-size: 22px; color: {INK}; background: {MUSTARD};
  padding: 10px 20px; border-radius: 999px; font-weight: 700; white-space: nowrap;
}}
main {{
  flex: 1; padding: 30px 56px 30px; display: flex; flex-direction: column;
  justify-content: flex-start; gap: .75em; font-size: var(--fs, 28px); min-height: 0;
}}
.msg {{ max-width: 88%; display: flex; flex-direction: column; gap: .3em; }}
.msg.bot {{ align-self: flex-start; }}
.msg.mine {{ align-self: flex-end; max-width: 72%; }}
.bubble {{
  background: #fff; border-radius: 1em 1em 1em .25em; padding: .7em .95em;
  line-height: 1.32; white-space: pre-wrap; box-shadow: 0 2px 0 rgba(34,48,79,.10);
  position: relative;
}}
.bot .bubble::before {{
  content: "Content Zavod"; display: block; font-weight: 700; color: {TEAL};
  font-size: .8em; margin-bottom: .2em;
}}
.mine .bubble {{ background: #E3F4D3; border-radius: 1em 1em .25em 1em; }}
.mine .bubble::after {{
  content: "✓✓"; color: {TEAL}; font-size: .65em; margin-left: .6em; letter-spacing: -.15em;
}}
.row {{ display: flex; gap: .3em; }}
.service {{
  align-self: center; background: rgba(34,48,79,.45); color: #fff; font-weight: 700;
  font-size: .78em; padding: .35em .9em; border-radius: 999px;
}}
.btn {{
  flex: 1; text-align: center; background: rgba(34,48,79,.58); color: #fff;
  border-radius: .55em; padding: .45em .5em; font-size: .86em; font-weight: 700;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
"""

_FIT_JS = """
() => {
  const main = document.querySelector('main');
  const limit = main.getBoundingClientRect().bottom - 28;
  const bottom = () => main.lastElementChild.getBoundingClientRect().bottom;
  let size = 30;
  main.style.setProperty('--fs', size + 'px');
  while (size > 14 && bottom() > limit) {
    size -= 0.5;
    main.style.setProperty('--fs', size + 'px');
  }
  main.style.justifyContent = 'center';  // measured top-aligned, shown centred
  return size;
}
"""


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def slide_html(mockup: Mockup) -> str:
    parts = []
    for bubble in mockup.bubbles:
        rows = "".join(
            '<div class="row">'
            + "".join(f'<div class="btn">{_esc(text)}</div>' for text in row)
            + "</div>"
            for row in bubble.buttons
        )
        if bubble.service:
            parts.append(f'<div class="service">{_esc(bubble.text)}</div>')
            continue
        side = "mine" if bubble.mine else "bot"
        parts.append(
            f'<div class="msg {side}"><div class="bubble">{_esc(bubble.text)}</div>{rows}</div>'
        )
    return (
        f"<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        f"<style>{_BASE_CSS}{_SLIDE_CSS}</style></head><body>"
        f"<header><h1>{_esc(mockup.title)}</h1><div class='note'>{_esc(mockup.note)}</div></header>"
        f"<main>{''.join(parts)}</main></body></html>"
    )


# --- banners ---


@dataclass(frozen=True)
class Banner:
    title: str
    subtitle: str
    art: str  # inline SVG
    chips: Sequence[str] = ()


def _doc_card(x: int, y: int, w: int, h: int, accent: str, rotate: int = 0) -> str:
    lines = "".join(
        f'<rect x="{x + 28}" y="{y + 70 + i * 34}" width="{w - 56 - (i % 2) * 60}" height="14"'
        f' rx="7" fill="{INK}" opacity=".18"/>'
        for i in range(max(0, (h - 100) // 34))
    )
    return (
        f'<g transform="rotate({rotate} {x + w / 2} {y + h / 2})">'
        f'<rect x="{x + 8}" y="{y + 10}" width="{w}" height="{h}" rx="22" fill="{INK}" opacity=".12"/>'
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="22" fill="#fff"/>'
        f'<rect x="{x + 28}" y="{y + 30}" width="{w * 0.5:.0f}" height="20" rx="10" fill="{accent}"/>'
        f"{lines}</g>"
    )


def _check(cx: int, cy: int, r: int, color: str = TEAL) -> str:
    return (
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}"/>'
        f'<path d="M{cx - r * 0.45:.0f} {cy + r * 0.02:.0f} l{r * 0.3:.0f} {r * 0.32:.0f}'
        f' l{r * 0.62:.0f} -{r * 0.66:.0f}" stroke="#fff" stroke-width="{r * 0.2:.0f}"'
        f' fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    )


def _svg(body: str) -> str:
    return f'<svg viewBox="0 0 560 560" width="560" height="560" xmlns="http://www.w3.org/2000/svg">{body}</svg>'


def _art_welcome() -> str:
    chart = (
        f'<rect x="300" y="300" width="200" height="170" rx="22" fill="{INK}"/>'
        + "".join(
            f'<rect x="{326 + i * 44}" y="{440 - hgt}" width="28" height="{hgt}" rx="6" fill="{c}"/>'
            for i, (hgt, c) in enumerate([(40, MUSTARD), (70, MUSTARD), (100, TEAL), (120, CORAL)])
        )
        + '<path d="M322 392 L372 360 L416 372 L476 318" stroke="#fff" stroke-width="7" fill="none"'
        ' stroke-linecap="round" stroke-linejoin="round"/>'
    )
    return _svg(
        f'<circle cx="290" cy="280" r="250" fill="{MUSTARD}" opacity=".25"/>'
        + _doc_card(70, 120, 230, 300, CORAL, -8)
        + _doc_card(170, 70, 240, 320, TEAL, 4)
        + chart
        + _check(150, 110, 34, CORAL)
    )


def _art_onboarding(labels: Sequence[str]) -> str:
    body = f'<circle cx="280" cy="280" r="250" fill="{TEAL}" opacity=".15"/>'
    top = 70
    for index, label in enumerate(labels):
        y = top + index * 86
        body += (
            f'<rect x="96" y="{y}" width="380" height="64" rx="32" fill="#fff"/>'
            f'<circle cx="128" cy="{y + 32}" r="22" fill="{TEAL if index < 2 else INK}"/>'
            f'<text x="128" y="{y + 41}" text-anchor="middle" font-size="24" font-weight="700"'
            f' fill="#fff" font-family="Liberation Sans, DejaVu Sans">{index + 1}</text>'
            f'<text x="168" y="{y + 42}" font-size="28" font-weight="700" fill="{INK}"'
            f' font-family="Liberation Sans, DejaVu Sans">{_esc(label)}</text>'
        )
        if index < 2:
            body += _check(440, y + 32, 18)
    return _svg(body)


def _art_team() -> str:
    people = "".join(
        f'<circle cx="{cx}" cy="150" r="46" fill="{c}"/>'
        f'<circle cx="{cx}" cy="138" r="18" fill="#fff" opacity=".9"/>'
        f'<path d="M{cx - 28} 182 q28 -34 56 0" fill="#fff" opacity=".9"/>'
        for cx, c in ((170, CORAL), (280, TEAL), (390, MUSTARD))
    )
    return _svg(
        f'<circle cx="280" cy="300" r="250" fill="{CORAL}" opacity=".14"/>'
        + people
        + _doc_card(130, 230, 300, 260, TEAL)
        + _check(400, 250, 34)
    )


def _art_note() -> str:
    return _svg(
        f'<circle cx="280" cy="290" r="250" fill="{TEAL}" opacity=".15"/>'
        f'<rect x="118" y="118" width="330" height="360" rx="26" fill="{INK}" opacity=".12"'
        f' transform="rotate(-4 283 298)"/>'
        f'<rect x="110" y="104" width="330" height="360" rx="26" fill="{SAND}" stroke="{MUSTARD}"'
        f' stroke-width="6" transform="rotate(-4 275 284)"/>'
        + "".join(
            f'<g transform="rotate(-4 275 284)">'
            f'<circle cx="160" cy="{200 + i * 70}" r="16" fill="{TEAL if i < 3 else CORAL}"/>'
            f'<rect x="190" y="{192 + i * 70}" width="{200 - (i % 2) * 50}" height="16" rx="8"'
            f' fill="{INK}" opacity=".25"/></g>'
            for i in range(4)
        )
        + f'<circle cx="275" cy="104" r="30" fill="{CORAL}"/>'
        f'<circle cx="266" cy="95" r="9" fill="#fff" opacity=".6"/>'
        f'<rect x="270" y="128" width="10" height="40" rx="5" fill="{INK}" opacity=".5"/>'
    )


def banners() -> dict[str, Banner]:
    labels = [setting.label for setting in onboarding_fields()]
    return {
        "welcome": Banner(
            "Контент-завод",
            "План Тем и Статьи для Дзена и VC.ru — каждую неделю, для всей команды",
            _art_welcome(),
            ("📈 Wordstat", "📋 План", "✍️ Статьи"),
        ),
        "onboarding": Banner(
            "Настроим бота за пару минут",
            f"{steps_count_text(len(labels))} — и я составлю первый План",
            _art_onboarding(labels),
        ),
        "cm_welcome": Banner(
            "Добро пожаловать в команду!",
            "План живёт в чате команды, Статьи — в один тап",
            _art_team(),
            ("🔄 заменить", "✅ утвердить", "📖 читать"),
        ),
        "team_note": Banner(
            "Как мы работаем",
            "План → согласование → Статьи → готово",
            _art_note(),
            ("📋 План", "✅ Хаб", "📄 Статьи"),
        ),
    }


_BANNER_CSS = f"""
body {{
  background: {SAND}; display: flex; align-items: center; padding: 0 40px 0 80px;
  position: relative;
}}
body::before {{
  content: ""; position: absolute; left: -120px; bottom: -160px; width: 420px; height: 420px;
  border-radius: 50%; background: {TEAL}; opacity: .10;
}}
body::after {{
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 18px; background: {TEAL};
}}
.text {{ flex: 1; display: flex; flex-direction: column; gap: 26px; position: relative; }}
.brand {{
  font-size: 22px; font-weight: 700; letter-spacing: 3px; text-transform: uppercase;
  color: {TEAL};
}}
h1 {{ font-size: 58px; line-height: 1.08; font-weight: 700; color: {INK}; }}
p {{ font-size: 30px; line-height: 1.3; color: {INK}; opacity: .78; max-width: 600px; }}
.chips {{ display: flex; gap: 12px; flex-wrap: wrap; }}
.chip {{
  font-size: 24px; font-weight: 700; background: #fff; color: {INK};
  border-radius: 999px; padding: 10px 20px; box-shadow: 0 2px 0 rgba(34,48,79,.12);
}}
.art {{ width: 560px; height: 560px; flex: none; }}
"""


def _typograph(text: str) -> str:
    """No dangling «в», «за» at a line's end, no last word alone on a line: Russian short
    words and the final word are glued with non-breaking spaces."""
    text = re.sub(r"(?<!\S)(\w{1,2}) ", "\\1\u00a0", text)
    return re.sub(r" (\S{1,6})$", "\u00a0\\1", text)


def banner_html(banner: Banner) -> str:
    chips = "".join(f"<div class='chip'>{_esc(chip)}</div>" for chip in banner.chips)
    return (
        f"<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        f"<style>{_BASE_CSS}{_BANNER_CSS}</style></head><body>"
        f"<div class='text'><div class='brand'>Content Zavod</div>"
        f"<h1>{_esc(_typograph(banner.title))}</h1><p>{_esc(_typograph(banner.subtitle))}</p>"
        f"{f'<div class=chips>{chips}</div>' if chips else ''}</div>"
        f"<div class='art'>{banner.art}</div></body></html>"
    )


# --- rendering ---


@dataclass(frozen=True)
class Picture:
    name: str
    html: str
    size: tuple[int, int]
    fit: bool  # shrink the chat's font until the mockup fits


async def pictures(only: set[str] | None) -> list[Picture]:
    result = [
        Picture(slide.key, slide_html(await _slide_mockup(slide)), SLIDE_SIZE, True)
        for slide in GUIDE_SLIDES
    ]
    all_banners = banners()
    result += [
        Picture(name, banner_html(all_banners[name]), BANNER_SIZE, False) for name in BANNERS
    ]
    return [picture for picture in result if not only or picture.name in only]


SOURCES_FILE = "sources.json"


def source_digest(picture: Picture) -> str:
    return hashlib.sha256(
        f"{picture.size[0]}x{picture.size[1]}\n{picture.html}".encode()
    ).hexdigest()


def write_sources(items: Sequence[Picture], directory: Path, *, partial: bool) -> None:
    """Record what `items` were drawn from; a `partial` run keeps the other pictures' entries."""
    path = directory / SOURCES_FILE
    sources = {}
    if partial and path.is_file():
        sources = json.loads(path.read_text(encoding="utf-8"))
    sources.update({picture.name: source_digest(picture) for picture in items})
    path.write_text(json.dumps(sources, indent=2, sort_keys=True) + "\n", encoding="utf-8")


async def screenshot(items: Sequence[Picture], paths: Sequence[Path]) -> None:
    from playwright.async_api import async_playwright  # a dev-time tool, not a bot dependency

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            for picture, path in zip(items, paths, strict=True):
                width, height = picture.size
                page = await browser.new_page(viewport={"width": width, "height": height})
                await page.set_content(picture.html)
                await page.evaluate("document.fonts.ready")
                if picture.fit:
                    await page.evaluate(_FIT_JS)
                await page.screenshot(path=str(path))
                await page.close()
        finally:
            await browser.close()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--only", help="comma-separated picture names, e.g. welcome,niche")
    parser.add_argument("--out", type=Path, default=ASSETS_DIR, help="output directory")
    parser.add_argument("--html", type=Path, help="also save each picture's HTML here")
    parser.add_argument(
        "--sources-only",
        action="store_true",
        help=f"only refresh {SOURCES_FILE}, keeping the PNGs (hand-made ones, say)",
    )
    args = parser.parse_args(argv)
    only = set(args.only.split(",")) if args.only else None

    items = asyncio.run(pictures(only))
    args.out.mkdir(parents=True, exist_ok=True)
    if args.html is not None:
        args.html.mkdir(parents=True, exist_ok=True)
        for picture in items:
            (args.html / f"{picture.name}.html").write_text(picture.html, encoding="utf-8")
    if not args.sources_only:
        paths = [args.out / f"{picture.name}.png" for picture in items]
        asyncio.run(screenshot(items, paths))
        for path in paths:
            print(f"{path} ({path.stat().st_size // 1024} KB)")
    write_sources(items, args.out, partial=only is not None)


if __name__ == "__main__":
    main()
