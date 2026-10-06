from typing import get_args

import pytest

from content_zavod.telegram.callback_codec import (
    _ACTION_CODES,
    ACTION_ROLE,
    CALLBACK_DATA_LIMIT,
    Action,
    ExportArticle,
    HistoryVersion,
    HistoryVersions,
    HistoryWeek,
    Page,
    SimpleAction,
    decode_callback_data,
    encode_callback_data,
)


def test_page_round_trips() -> None:
    payload = Page(plan_id="plan-1", page=2)
    assert decode_callback_data(encode_callback_data(payload)) == payload


def test_history_week_round_trips() -> None:
    payload = HistoryWeek(plan_id="plan-1", page=3)
    assert decode_callback_data(encode_callback_data(payload)) == payload


def test_history_versions_round_trips() -> None:
    payload = HistoryVersions(article_id="article-1", back_page=1)
    assert decode_callback_data(encode_callback_data(payload)) == payload


def test_history_version_round_trips() -> None:
    payload = HistoryVersion(article_id="article-1", version_id=42, back_page=1)
    assert decode_callback_data(encode_callback_data(payload)) == payload


def test_export_article_round_trips() -> None:
    payload = ExportArticle(article_id="article-1", article_format="docx")
    assert decode_callback_data(encode_callback_data(payload)) == payload


def test_simple_action_round_trips() -> None:
    payload = SimpleAction(action="regenerate", id_="item-0")
    assert decode_callback_data(encode_callback_data(payload)) == payload


def test_encode_rejects_oversized_payload() -> None:
    payload = SimpleAction(action="regenerate", id_="x" * CALLBACK_DATA_LIMIT)
    with pytest.raises(ValueError, match="exceeds"):
        encode_callback_data(payload)


def test_decode_rejects_unrecognized_string() -> None:
    with pytest.raises(ValueError, match="unrecognized"):
        decode_callback_data("z:abc")


def test_decode_rejects_string_without_separator() -> None:
    with pytest.raises(ValueError, match="unrecognized"):
        decode_callback_data("noseparator")


def test_decode_rejects_export_article_with_bad_format() -> None:
    with pytest.raises(ValueError, match="unrecognized"):
        decode_callback_data("ex:article-1:pdf")


def test_page_stays_under_limit_for_max_length_plan_id() -> None:
    uuid_hex_plan_id = "a" * 32
    data = encode_callback_data(Page(plan_id=uuid_hex_plan_id, page=999))
    assert len(data.encode("utf-8")) <= CALLBACK_DATA_LIMIT


def test_history_week_stays_under_limit_for_max_length_plan_id() -> None:
    uuid_hex_plan_id = "a" * 32
    data = encode_callback_data(HistoryWeek(plan_id=uuid_hex_plan_id, page=999))
    assert len(data.encode("utf-8")) <= CALLBACK_DATA_LIMIT


def test_history_versions_stays_under_limit_for_max_length_article_id() -> None:
    uuid_hex_article_id = "a" * 32
    data = encode_callback_data(HistoryVersions(article_id=uuid_hex_article_id, back_page=999))
    assert len(data.encode("utf-8")) <= CALLBACK_DATA_LIMIT


def test_history_version_stays_under_limit_for_max_length_article_id() -> None:
    uuid_hex_article_id = "a" * 32
    data = encode_callback_data(
        HistoryVersion(article_id=uuid_hex_article_id, version_id=999999, back_page=999)
    )
    assert len(data.encode("utf-8")) <= CALLBACK_DATA_LIMIT


def test_page_encoding_is_stable_across_calls() -> None:
    """Guards the wire format byte-for-byte (ADR-0011): buttons already sent to Telegram
    before a redeploy must keep decoding the same way, so this literal string must never
    change without a deliberate migration."""
    assert encode_callback_data(Page(plan_id="plan-1", page=2)) == "pg:plan-1:2"


def test_history_version_encoding_is_stable_across_calls() -> None:
    payload = HistoryVersion(article_id="article-1", version_id=7, back_page=3)
    assert encode_callback_data(payload) == "hd:article-1:7:3"


def test_export_article_encoding_is_stable_across_calls() -> None:
    payload = ExportArticle(article_id="article-1", article_format="md")
    assert encode_callback_data(payload) == "ex:article-1:md"


def test_simple_action_encoding_is_stable_across_calls() -> None:
    payload = SimpleAction(action="retry", id_="42")
    assert encode_callback_data(payload) == "rt:42"


def test_action_codes_are_unique_and_cover_every_action() -> None:
    assert set(_ACTION_CODES) == set(get_args(Action))
    assert len(set(_ACTION_CODES.values())) == len(_ACTION_CODES)


def test_every_action_but_request_access_has_a_role() -> None:
    assert set(ACTION_ROLE) == set(get_args(Action)) - {"request_access"}


@pytest.mark.parametrize(
    ("payload", "data"),
    [
        (SimpleAction("menu", ""), "mn:"),
        (SimpleAction("settings", ""), "st:"),
        (SimpleAction("edit_setting", "niche"), "se:niche"),
        (SimpleAction("schedule_day", "mon:s"), "sd:mon:s"),
        (SimpleAction("cancel_input", "setting_input"), "ci:setting_input"),
    ],
)
def test_menu_and_settings_actions_round_trip(payload: SimpleAction, data: str) -> None:
    assert encode_callback_data(payload) == data
    assert decode_callback_data(data) == payload
