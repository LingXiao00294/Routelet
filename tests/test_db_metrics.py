"""Exercise exact token aggregation beyond SQLite's signed integer limit."""

from __future__ import annotations

import sqlite3
import math
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from agent_router.api.metrics import create_metrics_router
from agent_router.db import CallStore

_MAX_TOKEN_COUNT = 2**63 - 1
_AGGREGATES = [
    (
        "summary",
        "summary",
        (
            "total_input_tokens",
            "total_output_tokens",
            "total_cache_read",
            "total_cache_write",
        ),
    ),
    ("by_model", "by-model", ("total_input_tokens", "total_output_tokens")),
    ("by_real_model", "by-real-model", ("total_input_tokens", "total_output_tokens")),
    (
        "daily_trend",
        "daily",
        ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"),
    ),
]


@pytest.fixture
async def store() -> AsyncIterator[CallStore]:
    instance = CallStore(":memory:")
    await instance.init()
    try:
        yield instance
    finally:
        await instance.close()


async def _record_tokens(
    store: CallStore, count: int | None, *, model: str = "model", days_ago: int = 0
) -> None:
    call_id = await store.record(
        virtual_model=model,
        provider_name="provider",
        provider_model=model,
        status="success" if count is not None else "error",
        latency_ms=100 if count is not None else 500,
        input_tokens=count,
        output_tokens=count,
        cache_read_tokens=count,
        cache_write_tokens=count,
        cost_usd=0.125 if count is not None else None,
    )
    await store.conn.execute(
        "UPDATE calls SET timestamp = DATE('now', ?) WHERE id = ?",
        (f"-{days_ago} days", call_id),
    )
    await store.conn.commit()


@pytest.mark.parametrize(("method", "path", "fields"), _AGGREGATES)
async def test_token_overflow_remains_exact_in_store_and_api(
    store: CallStore, method: str, path: str, fields: tuple[str, ...]
) -> None:
    await _record_tokens(store, _MAX_TOKEN_COUNT)
    await _record_tokens(store, _MAX_TOKEN_COUNT)
    await _record_tokens(store, None)

    result = await getattr(store, method)()
    row = result if method == "summary" else result[0]
    for field in fields:
        assert type(row[field]) is int
        assert row[field] == 2 * _MAX_TOKEN_COUNT
    assert row["total_calls" if method == "summary" else "count"] == 3
    assert row["success_count"] == 2
    assert row["cost_usd" if method == "daily_trend" else "total_cost_usd"] == 0.25
    if method == "summary":
        assert row["error_count"] == 1
        assert row["success_rate"] == 66.67
        assert row["avg_latency_ms"] == 100

    app = FastAPI()
    app.include_router(create_metrics_router(store))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://metrics.test",
    ) as client:
        response = await client.get(f"/api/metrics/{path}")
    assert response.status_code == 200
    assert response.json() == result


@pytest.mark.parametrize(("method", "path", "fields"), _AGGREGATES)
async def test_overflow_retry_preserves_other_groups_and_all_null_values(
    store: CallStore, method: str, path: str, fields: tuple[str, ...]
) -> None:
    await _record_tokens(store, _MAX_TOKEN_COUNT)
    await _record_tokens(store, _MAX_TOKEN_COUNT)
    await _record_tokens(store, 7, model="small", days_ago=1)
    await _record_tokens(store, None, model="unknown", days_ago=2)

    result = await getattr(store, method)()
    if method == "summary":
        for field in fields:
            assert result[field] == 2 * _MAX_TOKEN_COUNT + 7
            assert type(result[field]) is int
        assert result["total_cost_usd"] == 0.375
        return

    assert len(result) == 3
    expected = {2 * _MAX_TOKEN_COUNT, 7, None}
    for field in fields:
        assert {row[field] for row in result} == expected
        assert all(row[field] is None or type(row[field]) is int for row in result)
    null_row = next(row for row in result if row[fields[0]] is None)
    cost_field = "cost_usd" if method == "daily_trend" else "total_cost_usd"
    assert null_row[cost_field] is None
    assert null_row["success_count"] == 0


@pytest.mark.parametrize("count", [None, 0, 17])
async def test_ordinary_counts_keep_single_query_fast_path(
    store: CallStore, count: int | None
) -> None:
    await _record_tokens(store, count)
    await _record_tokens(store, None)
    queries: list[str] = []
    await store.conn.set_trace_callback(queries.append)

    for method, _path, fields in _AGGREGATES:
        queries.clear()
        result = await getattr(store, method)()
        row = result if method == "summary" else result[0]
        expected = (count or 0) if method == "summary" else count
        assert all(row[field] == expected for field in fields)
        assert len(queries) == 1


async def test_non_overflow_database_errors_are_not_retried(
    store: CallStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    failing_query = AsyncMock(
        side_effect=sqlite3.OperationalError("database is locked")
    )
    monkeypatch.setattr(store.conn, "execute_fetchall", failing_query)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        await store.summary()

    assert failing_query.await_count == 1


@pytest.mark.parametrize(("method", "path", "fields"), _AGGREGATES)
async def test_non_finite_cost_aggregate_is_unknown_and_api_remains_available(
    store: CallStore, method: str, path: str, fields: tuple[str, ...]
) -> None:
    for _ in range(2):
        await store.record(
            virtual_model="model",
            provider_name="provider",
            provider_model="actual",
            status="success",
            input_tokens=1,
            cost_usd=1e308,
        )

    result = await getattr(store, method)()
    row = result if method == "summary" else result[0]
    cost_field = "cost_usd" if method == "daily_trend" else "total_cost_usd"
    assert row[cost_field] is None
    assert row[fields[0]] == 2
    app = FastAPI()
    app.include_router(create_metrics_router(store))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://metrics.test",
    ) as client:
        response = await client.get(f"/api/metrics/{path}")
    assert response.status_code == 200
    assert response.json() == result


@pytest.mark.parametrize(
    ("costs", "expected"),
    [
        ([], 0),
        ([None], 0),
        ([0.0], 0),
        ([0.125, None], 0.125),
        ([float("inf"), float("-inf")], None),
    ],
    ids=["empty", "unknown", "zero", "finite", "indeterminate-sum"],
)
async def test_summary_distinguishes_empty_costs_from_null_aggregate(
    store: CallStore, costs: list[float | None], expected: float | None
) -> None:
    # Opposite infinities yield SQL NULL on every supported SQLite version.
    # Some SQLite versions also produce NULL when finite inputs overflow SUM.
    for cost in costs:
        await store.record(virtual_model="model", status="success", cost_usd=cost)

    result = await store.summary()
    assert result["total_cost_usd"] == expected
    assert result["total_calls"] == len(costs)
    app = FastAPI()
    app.include_router(create_metrics_router(store))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://metrics.test",
    ) as client:
        response = await client.get("/api/metrics/summary")
    assert response.status_code == 200
    assert response.json() == result


async def test_non_finite_stored_cost_is_sanitized_only_in_public_output(
    store: CallStore,
) -> None:
    call_id = await store.record(
        virtual_model="model", status="success", cost_usd=float("inf")
    )

    detail = await store.get_call(call_id)
    summaries, total = await store.list_calls()
    assert detail is not None
    assert detail["cost_usd"] is None
    assert total == 1
    assert summaries[0]["cost_usd"] is None
    stored = list(
        await store.conn.execute_fetchall(
            "SELECT cost_usd FROM calls WHERE id = ?", (call_id,)
        )
    )
    assert math.isinf(stored[0][0])

    app = FastAPI()
    app.include_router(create_metrics_router(store))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://metrics.test",
    ) as client:
        detail_response = await client.get(f"/api/calls/{call_id}")
        list_response = await client.get("/api/calls")
    assert detail_response.status_code == list_response.status_code == 200
    assert detail_response.json()["cost_usd"] is None
    assert list_response.json()["data"][0]["cost_usd"] is None
