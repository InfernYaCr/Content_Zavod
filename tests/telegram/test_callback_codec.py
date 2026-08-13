import pytest

from content_zavod.telegram.callback_codec import (
    CALLBACK_DATA_LIMIT,
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


def test_page_encoding_matches_todays_gateway_wire_format() -> None:
    from content_zavod.telegram.gateway import encode_page_callback

    assert encode_callback_data(Page(plan_id="plan-1", page=2)) == encode_page_callback("plan-1", 2)


def test_history_version_encoding_matches_todays_gateway_wire_format() -> None:
    from content_zavod.telegram.gateway import encode_history_version_callback

    payload = HistoryVersion(article_id="article-1", version_id=7, back_page=3)
    assert encode_callback_data(payload) == encode_history_version_callback("article-1", 7, 3)


def test_export_article_encoding_matches_todays_gateway_wire_format() -> None:
    from content_zavod.telegram.gateway import encode_export_callback

    payload = ExportArticle(article_id="article-1", article_format="md")
    assert encode_callback_data(payload) == encode_export_callback("article-1", "md")


def test_simple_action_encoding_matches_todays_gateway_wire_format() -> None:
    from content_zavod.telegram.gateway import encode_callback_data as gateway_encode

    payload = SimpleAction(action="retry", id_="42")
    assert encode_callback_data(payload) == gateway_encode("retry", "42")


def test_page_stays_under_limit_for_max_length_plan_id() -> None:
    uuid_hex_plan_id = "a" * 32
    data = encode_callback_data(Page(plan_id=uuid_hex_plan_id, page=999))
    assert len(data.encode("utf-8")) <= CALLBACK_DATA_LIMIT


def test_todays_gateway_string_decodes_to_the_new_typed_payload() -> None:
    from content_zavod.telegram.gateway import encode_callback_data as gateway_encode

    old_data = gateway_encode("delete", "item-0")
    assert decode_callback_data(old_data) == SimpleAction(action="delete", id_="item-0")


def test_action_codes_match_gateways_table_for_every_action() -> None:
    """Guards the byte-for-byte wire-format promise (ADR-0011) across all twenty
    Действия, not just the six exercised above - a future edit to either module's
    table that drifts from the other would fail here."""
    from content_zavod.telegram import callback_codec, gateway

    assert callback_codec._ACTION_CODES == gateway._ACTION_CODES
