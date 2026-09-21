"""Keep non-stream responses and call records independent of malformed usage."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from agent_router.app import create_app
from agent_router.config import parse_config_data
from agent_router.db import CallStore
from agent_router.recording import CallRecorder

_VALID_USAGE = {
    "input_tokens": 100,
    "output_tokens": 25,
    "cache_read_input_tokens": 200,
    "cache_creation_input_tokens": 50,
}
_TOKEN_COLUMNS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_input_tokens": "cache_read_tokens",
    "cache_creation_input_tokens": "cache_write_tokens",
}


@dataclass
class _Case:
    client: httpx.AsyncClient
    store: CallStore
    recorder: CallRecorder
    response_body: dict[str, Any]


@pytest.fixture
async def case() -> AsyncIterator[_Case]:
    config = parse_config_data(
        {
            "router": {"mode": "failover"},
            "providers": {
                "provider": {
                    "type": "anthropic",
                    "api_key": "test-key",
                    "base_url": "https://provider.test",
                    "models": {
                        "actual": {
                            "input_price_per_million": 2.0,
                            "output_price_per_million": 8.0,
                            "cache_read_price_per_million": 0.2,
                            "cache_write_price_per_million": 3.0,
                        }
                    },
                }
            },
            "models": {
                "virtual": {"models": [{"provider": "provider", "model": "actual"}]}
            },
        }
    )
    response_body: dict[str, Any] = {
        "id": "msg_success",
        "type": "message",
        "model": "actual",
        "content": [{"type": "text", "text": "Successful upstream response"}],
        "usage": {},
    }
    store = CallStore(":memory:")
    await store.init()
    recorder = CallRecorder(store)
    await recorder.start()
    try:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=response_body)
        )
        async with httpx.AsyncClient(transport=transport) as upstream:
            app = create_app(config, store, call_recorder=recorder)
            await app.state.router_engine.http.aclose()
            app.state.router_engine.http = upstream
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://router.test"
            ) as client:
                yield _Case(client, store, recorder, response_body)
    finally:
        await recorder.close()
        await store.close()


async def _request_and_record(case: _Case, usage: Any) -> dict:
    """Verify pass-through delivery and retrieve the sole persisted success."""
    case.response_body["usage"] = usage
    response = await case.client.post(
        "/v1/messages",
        json={
            "model": "virtual",
            "stream": False,
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == case.response_body
    await case.recorder.wait_idle(timeout=1)
    summaries, total = await case.store.list_calls()
    assert total == 1
    call = await case.store.get_call(summaries[0]["id"])
    assert call is not None
    assert call["status"] == "success"
    assert call["provider_name"] == "provider"
    assert call["attempt"] == 1
    return call


@pytest.mark.parametrize("usage", [None, "unexpected", [], [{"input_tokens": 10}]])
async def test_non_mapping_usage_preserves_response_and_record(
    case: _Case, usage: Any
) -> None:
    call = await _request_and_record(case, usage)

    assert all(call[column] is None for column in _TOKEN_COLUMNS.values())
    assert call["cost_usd"] == 0.0


@pytest.mark.parametrize(
    ("field", "expected_cost"),
    [
        ("input_tokens", 0.00039),
        ("output_tokens", 0.00039),
        ("cache_read_input_tokens", 0.00055),
        ("cache_creation_input_tokens", 0.00044),
    ],
)
@pytest.mark.parametrize(
    "invalid_value", ["oops", "12", {}, [], -1, True, 1.5, 2**63, None]
)
async def test_invalid_count_keeps_other_counts_and_does_not_drop_record(
    case: _Case, field: str, expected_cost: float, invalid_value: Any
) -> None:
    usage: dict[str, Any] = {**_VALID_USAGE, field: invalid_value}
    call = await _request_and_record(case, usage)

    for token_field, column in _TOKEN_COLUMNS.items():
        expected = None if token_field == field else _VALID_USAGE[token_field]
        assert call[column] == expected
    assert call["cost_usd"] == pytest.approx(expected_cost)


async def test_valid_usage_preserves_precise_cost_and_price_snapshots(
    case: _Case,
) -> None:
    call = await _request_and_record(case, dict(_VALID_USAGE))

    for field, column in _TOKEN_COLUMNS.items():
        assert call[column] == _VALID_USAGE[field]
    assert call["cost_usd"] == pytest.approx(0.00059)
    assert call["input_price_per_million"] == 2.0
    assert call["output_price_per_million"] == 8.0
    assert call["cache_read_price_per_million"] == 0.2
    assert call["cache_write_price_per_million"] == 3.0


async def test_zero_counts_remain_explicit_zero(case: _Case) -> None:
    call = await _request_and_record(case, dict.fromkeys(_VALID_USAGE, 0))

    assert all(call[column] == 0 for column in _TOKEN_COLUMNS.values())
    assert call["cost_usd"] == 0.0
