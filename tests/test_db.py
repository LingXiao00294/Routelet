from __future__ import annotations

import json
import sqlite3

import pytest

from agent_router.db import (
    CALL_SCHEMA_COLUMNS,
    CALL_SUMMARY_COLUMNS,
    MAX_PERSISTED_BODY_BYTES,
    SCHEMA,
    CallStore,
    IncompatibleDatabaseError,
    _estimate_request_tokens,
    _serialize_call_body,
)

PRICE_SNAPSHOT_COLUMNS = (
    "input_price_per_million",
    "output_price_per_million",
    "cache_read_price_per_million",
    "cache_write_price_per_million",
)


async def test_fresh_database_has_complete_calls_schema(tmp_path):
    db_path = tmp_path / "calls.db"
    store = CallStore(str(db_path))

    await store.init()
    try:
        rows = await store.conn.execute_fetchall("PRAGMA table_info(calls)")
    finally:
        await store.close()

    assert {str(row[1]) for row in rows} == CALL_SCHEMA_COLUMNS


async def test_record_preserves_null_and_zero_price_snapshots(tmp_path):
    store = CallStore(str(tmp_path / "calls.db"))
    await store.init()
    try:
        zero_id = await store.record(
            virtual_model="router",
            status="success",
            provider_name="provider",
            provider_model="model",
            input_price_per_million=0.0,
            output_price_per_million=0.0,
            cache_read_price_per_million=0.0,
            cache_write_price_per_million=0.0,
            cost_usd=0.0,
        )
        null_id = await store.record(
            virtual_model="router",
            status="error",
            error_type="upstream_error",
        )

        zero_call = await store.get_call(zero_id)
        null_call = await store.get_call(null_id)
    finally:
        await store.close()

    assert zero_call is not None
    assert null_call is not None
    for column in PRICE_SNAPSHOT_COLUMNS:
        assert zero_call[column] == 0.0
        assert null_call[column] is None


async def test_list_calls_returns_summaries_while_get_call_keeps_details(tmp_path):
    store = CallStore(str(tmp_path / "calls.db"))
    await store.init()
    try:
        call_id = await store.record(
            virtual_model="router",
            status="success",
            provider_name="provider",
            provider_type="anthropic",
            provider_model="model",
            provider_url="https://provider.test",
            request_body={"messages": [{"role": "user", "content": "private prompt"}]},
            response_body={"content": [{"type": "text", "text": "private reply"}]},
            failover_details=[
                {"provider": "first", "model": "model", "error": "failed"}
            ],
            input_price_per_million=1.0,
            cost_usd=0.001,
        )

        summaries, total = await store.list_calls()
        detail = await store.get_call(call_id)
    finally:
        await store.close()

    assert total == 1
    assert len(summaries) == 1
    assert set(summaries[0]) == set(CALL_SUMMARY_COLUMNS)
    assert set(summaries[0]).isdisjoint(
        {"request_body", "response_body", "failover_details"}
    )
    assert summaries[0]["id"] == call_id
    assert summaries[0]["cost_usd"] == 0.001

    assert detail is not None
    assert json.loads(detail["request_body"])["messages"][0]["content"] == (
        "private prompt"
    )
    assert json.loads(detail["response_body"])["content"][0]["text"] == (
        "private reply"
    )
    assert json.loads(detail["failover_details"])[0]["provider"] == "first"
    assert detail["provider_url"] == "https://provider.test"
    assert detail["input_price_per_million"] == 1.0


async def test_daily_trend_uses_exact_inclusive_calendar_window(tmp_path):
    store = CallStore(str(tmp_path / "calls.db"))
    await store.init()
    try:
        today_id = await store.record(virtual_model="router", status="success")
        yesterday_id = await store.record(virtual_model="router", status="success")
        await store.conn.execute(
            "UPDATE calls SET timestamp = DATE('now') WHERE id = ?", (today_id,)
        )
        await store.conn.execute(
            "UPDATE calls SET timestamp = DATE('now', '-1 day') WHERE id = ?",
            (yesterday_id,),
        )
        await store.conn.commit()

        one_day = await store.daily_trend(days=1)
        two_days = await store.daily_trend(days=2)
    finally:
        await store.close()

    assert [row["count"] for row in one_day] == [1]
    assert [row["count"] for row in two_days] == [1, 1]


async def test_daily_trend_rejects_non_positive_window(tmp_path):
    store = CallStore(str(tmp_path / "calls.db"))
    await store.init()
    try:
        with pytest.raises(ValueError, match="days"):
            await store.daily_trend(days=0)
    finally:
        await store.close()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"messages": [None, "bad", {"content": [{"type": "text", "text": 7}]}]}, None),
        ({"messages": [], "system": "system prompt"}, 4),
        (
            {
                "messages": [
                    {
                        "content": [
                            {"type": "text", "text": "hello"},
                            {"type": "image", "source": {"data": "x" * 12}},
                        ]
                    }
                ]
            },
            3,
        ),
    ],
)
def test_request_token_estimate_ignores_malformed_nested_content(body, expected):
    assert _estimate_request_tokens(body) == expected


@pytest.mark.parametrize(
    "body",
    [
        "\x00" * MAX_PERSISTED_BODY_BYTES,
        {"control": "\x00\x01\x02" * MAX_PERSISTED_BODY_BYTES},
        {"multibyte": "输入" * MAX_PERSISTED_BODY_BYTES},
    ],
    ids=["raw-control", "json-control", "multibyte"],
)
def test_serialize_call_body_is_valid_and_bounded_for_any_json_expansion(body):
    serialized = _serialize_call_body(body)

    assert serialized is not None
    assert len(serialized.encode("utf-8")) <= MAX_PERSISTED_BODY_BYTES
    envelope = json.loads(serialized)
    assert envelope["_truncated"] is True
    assert envelope["_original_bytes"] > MAX_PERSISTED_BODY_BYTES
    assert isinstance(envelope["_preview"], str)


async def test_incompatible_database_is_unchanged_after_validation_failure(tmp_path):
    db_path = tmp_path / "legacy-calls.db"
    legacy_schema = "\n".join(
        line
        for line in SCHEMA.splitlines()
        if not any(column in line for column in PRICE_SNAPSHOT_COLUMNS)
    )
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(legacy_schema)
        conn.execute(
            """INSERT INTO calls (id, timestamp, virtual_model, status)
            VALUES (?, ?, ?, ?)""",
            ("legacy-id", "2026-07-13T00:00:00+00:00", "router", "success"),
        )
        conn.commit()
    finally:
        conn.close()

    original_bytes = db_path.read_bytes()
    store = CallStore(str(db_path))

    with pytest.raises(IncompatibleDatabaseError) as exc_info:
        await store.init()

    message = str(exc_info.value)
    assert "schema 与当前版本不兼容" in message
    assert "手动删除或重命名" in message
    for column in PRICE_SNAPSHOT_COLUMNS:
        assert column in message
    with pytest.raises(RuntimeError, match="未初始化"):
        _ = store.conn
    assert db_path.read_bytes() == original_bytes

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, virtual_model, status FROM calls WHERE id = ?",
            ("legacy-id",),
        ).fetchone()
    finally:
        conn.close()
    assert row == ("legacy-id", "router", "success")
