from __future__ import annotations

import asyncio
import gzip
import json
from types import SimpleNamespace
from typing import cast
from urllib.parse import quote

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import ClientDisconnect

from routelet import app as app_module
from routelet.app import (
    _calculate_cost_usd,
    _close_prefetched_stream,
    _prefetch_first_chunk,
    _stream_wrapper,
    create_app,
)
from routelet.config import (
    AppConfig,
    ModelRef,
    ProviderConfig,
    RouterConfig,
    ServerConfig,
    VirtualModelConfig,
    parse_config_data,
)
from routelet.db import CALL_SUMMARY_COLUMNS, CallStore
from routelet.recording import CallRecorder
from routelet.responses import ManagedStreamingResponse
from routelet.routing import NoProviderAvailableError, Router


def _passthrough_config() -> AppConfig:
    return AppConfig(
        router=RouterConfig(mode="sticky"),
        models={
            "m": VirtualModelConfig(
                pinned_model=ModelRef(provider="primary", model="real-model"),
                providers=[
                    ProviderConfig(
                        type="anthropic",
                        name=name,
                        model="real-model",
                        api_key="test-key",
                        base_url=f"https://{name}.test",
                        priority=index,
                    )
                    for index, name in enumerate(["backup", "primary"], 1)
                ],
            )
        },
    )


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503, 529])
async def test_sticky_http_errors_are_returned_unchanged(
    store, recorder, stream, status
):
    payload = b'{ "custom_error": "' + b"x" * 1024 + b'", "code": 123 }\n'
    attempted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempted.append(request.url.host)
        return httpx.Response(
            status,
            content=payload,
            headers={
                "Content-Type": "application/problem+json; charset=utf-8",
                "Retry-After": "Thu, 01 Jan 2037 00:00:00 GMT",
                "WWW-Authenticate": 'Bearer realm="upstream"',
                "X-Request-ID": "upstream-id",
                "Anthropic-Ratelimit-Requests-Remaining": "0",
                "Connection": "keep-alive, x-ratelimit-private",
                "X-Ratelimit-Private": "connection-only",
                "Set-Cookie": "upstream=session",
            },
        )

    config = _passthrough_config()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(config, store, call_recorder=recorder)
        engine = Router(config, upstream)
        app.state.router_engine = engine
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/messages",
                json={"model": "m", "stream": stream, "max_tokens": 10, "messages": []},
            )
    assert response.status_code == status
    assert response.content == payload
    assert response.headers["content-type"] == "application/problem+json; charset=utf-8"
    assert response.headers["content-length"] == str(len(payload))
    assert response.headers["retry-after"] == "Thu, 01 Jan 2037 00:00:00 GMT"
    assert response.headers["www-authenticate"] == 'Bearer realm="upstream"'
    assert response.headers["x-request-id"] == "upstream-id"
    assert response.headers["anthropic-ratelimit-requests-remaining"] == "0"
    assert "connection" not in response.headers
    assert "x-ratelimit-private" not in response.headers
    assert "set-cookie" not in response.headers
    assert attempted == ["primary.test"]
    if status in {429, 529}:
        assert engine.provider_gate.is_in_cooldown("primary")
    await recorder.wait_idle(timeout=1)
    call = await _only_call_detail(store)
    assert call["status"] == "error"
    assert call["attempt"] == 1
    assert len(json.loads(call["failover_details"])) == 1


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "payload", [b"", b"\xff\x00upstream failure", b"<html>Unavailable</html>"]
)
async def test_sticky_preserves_non_json_and_compressed_errors(
    store, recorder, stream, payload
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            content=gzip.compress(payload),
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Encoding": "gzip",
            },
        )

    config = _passthrough_config()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(config, store, call_recorder=recorder)
        app.state.router_engine = Router(config, upstream)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/messages", json={"model": "m", "stream": stream, "messages": []}
            )
    assert response.status_code == 503
    assert response.content == payload
    assert "content-encoding" not in response.headers
    assert response.headers["content-length"] == str(len(payload))


async def test_sticky_waits_for_slow_http_error_before_sending_headers(
    store, recorder, monkeypatch
):
    monkeypatch.setattr(app_module, "_STREAM_FIRST_BYTE_PREFETCH_TIMEOUT", 0.001)

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.03)
        return httpx.Response(529, content=b"overloaded", headers={"Retry-After": "7"})

    config = _passthrough_config()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(config, store, call_recorder=recorder)
        app.state.router_engine = Router(config, upstream)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/messages", json={"model": "m", "stream": True, "messages": []}
            )
    assert response.status_code == 529
    assert response.content == b"overloaded"
    assert response.headers["retry-after"] == "7"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_sticky_preserves_upstream_redirect_location(
    store, recorder, stream, status
):
    location = "https://primary.test/canonical/v1/messages?version=2"
    attempted = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempted.append(request.url.host)
        return httpx.Response(status, content=b"moved", headers={"Location": location})

    config = _passthrough_config()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(config, store, call_recorder=recorder)
        app.state.router_engine = Router(config, upstream)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/messages", json={"model": "m", "stream": stream, "messages": []}
            )
    assert response.status_code == status
    assert response.headers["location"] == location
    assert response.content == b"moved"
    assert attempted == ["primary.test"]


@pytest.mark.parametrize("provider", ["plain", "a/b", "a/b/reset", "中文/模型 %2F"])
async def test_circuit_reset_preserves_complete_provider_name(
    store, recorder, provider
):
    config = _passthrough_config()
    async with httpx.AsyncClient() as upstream:
        app = create_app(config, store, call_recorder=recorder)
        engine = Router(config, upstream)
        app.state.router_engine = engine
        await engine.circuit_breaker.record_failure(provider, immediate=True)
        await engine.circuit_breaker.record_failure("other", immediate=True)
        engine.provider_gate.enter_cooldown(provider, 60)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/circuit-breaker/{quote(provider, safe='')}/reset"
            )
            states = (await client.get("/api/circuit-breaker")).json()
    assert response.status_code == 200
    assert response.json()["provider"] == provider
    assert provider not in states
    assert (await engine.circuit_breaker.state(provider)).value == "closed"
    assert not engine.provider_gate.is_in_cooldown(provider)
    assert states["other"] == "open"
    assert states["primary"] == "closed"
    assert states["backup"] == "closed"


@pytest.mark.parametrize(
    "reload_point", ["lazy_start", "candidate_discovery", "active_attempt"]
)
async def test_stream_prefetch_uses_actual_routing_generation(
    store, recorder, monkeypatch, reload_point
):
    monkeypatch.setattr(app_module, "_STREAM_FIRST_BYTE_PREFETCH_TIMEOUT", 0.001)
    config = _passthrough_config()
    if reload_point != "active_attempt":
        config.router.mode = "failover"
    next_config = _passthrough_config()
    if reload_point == "active_attempt":
        next_config.router.mode = "failover"
    attempted = []

    async def handler(request: httpx.Request) -> httpx.Response:
        attempted.append(request.url.host)
        if reload_point == "active_attempt":
            await engine.reload_config(next_config)
        await asyncio.sleep(0.03)
        return httpx.Response(
            529, content=b"original slow error", headers={"Retry-After": "7"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(config, store, call_recorder=recorder)
        engine = Router(config, upstream)
        app.state.router_engine = engine
        if reload_point == "lazy_start":
            original_prefetch = app_module._prefetch_first_chunk

            async def reload_before_iteration(stream_agen, **kwargs):
                await engine.reload_config(next_config)
                return await original_prefetch(stream_agen, **kwargs)

            monkeypatch.setattr(
                app_module, "_prefetch_first_chunk", reload_before_iteration
            )
        elif reload_point == "candidate_discovery":
            original_discovery = engine._get_providers
            reloaded = False

            async def reload_during_discovery(model):
                nonlocal reloaded
                providers = await original_discovery(model)
                if not reloaded:
                    reloaded = True
                    await engine.reload_config(next_config)
                return providers

            monkeypatch.setattr(engine, "_get_providers", reload_during_discovery)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/messages", json={"model": "m", "stream": True, "messages": []}
            )
    assert response.status_code == 529
    assert response.content == b"original slow error"
    assert response.headers["retry-after"] == "7"
    assert attempted == ["primary.test"]


async def test_stream_cannot_switch_to_sticky_after_early_headers(store, recorder):
    config = _passthrough_config()
    config.router.mode = "failover"
    release = asyncio.Event()
    started = asyncio.Event()
    attempted = []

    async def handler(request: httpx.Request) -> httpx.Response:
        attempted.append(request.url.host)
        started.set()
        await release.wait()
        return httpx.Response(503, content=b"retry")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        engine = Router(config, upstream)
        outcome: dict = {}
        stream = engine.route_stream(
            {"model": "m", "stream": True, "messages": []}, outcome
        )
        prefetch = asyncio.create_task(
            _prefetch_first_chunk(stream, timeout=0.02, routing_outcome=outcome)
        )
        await started.wait()
        first, pending = await prefetch
        assert first is None and pending is not None
        assert outcome["_stream_response_mode"] == "failover"
        await engine.reload_config(_passthrough_config())
        release.set()
        try:
            with pytest.raises(RuntimeError, match="routing mode changed"):
                await pending
        finally:
            await _close_prefetched_stream(stream, pending)
    assert attempted == ["backup.test"]


@pytest.mark.parametrize("started", [False, True])
@pytest.mark.parametrize(
    "error_data", [b'{"error":{"type":"rate_limit_error","extra":42}}', b"not-json"]
)
async def test_sticky_sse_error_frame_is_forwarded_once(
    store, recorder, started, error_data
):
    prefix = (
        b'event: message_start\ndata: {"type":"message_start"}\n\n' if started else b""
    )
    frame = (
        b": upstream detail\r\nid: original-id\r\nevent: error\r\ndata: "
        + error_data
        + b"\r\n\r\n"
    )
    attempted: list[str] = []

    async def chunks():
        payload = prefix + frame
        for index in range(0, len(payload), 7):
            yield payload[index : index + 7]

    def handler(request: httpx.Request) -> httpx.Response:
        attempted.append(request.url.host)
        return httpx.Response(
            200, content=chunks(), headers={"Content-Type": "text/event-stream"}
        )

    config = _passthrough_config()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(config, store, call_recorder=recorder)
        app.state.router_engine = Router(config, upstream)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/messages", json={"model": "m", "stream": True, "messages": []}
            )
    assert response.status_code == 200
    assert response.content == prefix + frame
    assert attempted == ["primary.test"]
    await recorder.wait_idle(timeout=1)
    call = await _only_call_detail(store)
    assert call["status"] == "error"
    assert call["attempt"] == 1


@pytest.mark.parametrize("stream", [False, True])
async def test_sticky_transport_failure_uses_gateway_error(store, recorder, stream):
    attempted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempted.append(request.url.host)
        raise httpx.ReadTimeout("upstream timed out", request=request)

    config = _passthrough_config()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(config, store, call_recorder=recorder)
        app.state.router_engine = Router(config, upstream)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/messages",
                json={"model": "m", "stream": stream, "messages": []},
            )
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "api_error"
    assert attempted == ["primary.test"]
    await recorder.wait_idle(timeout=1)
    call = await _only_call_detail(store)
    assert call["status"] == "error"
    assert call["attempt"] == 1


async def _only_call_detail(store: CallStore) -> dict:
    """Return the complete detail for the only persisted call in a store."""
    summaries, total = await store.list_calls()
    assert total == 1
    detail = await store.get_call(summaries[0]["id"])
    assert detail is not None
    return detail


class TestCostCalculation:
    def test_calculates_all_token_categories(self):
        usage = {
            "input_tokens": 1_000_000,
            "output_tokens": 500_000,
            "cache_read_input_tokens": 2_000_000,
            "cache_creation_input_tokens": 250_000,
        }
        outcome = {
            "pricing": {
                "input": 1.0,
                "output": 4.0,
                "cache_read": 0.1,
                "cache_write": 1.2,
            }
        }

        assert _calculate_cost_usd(usage, outcome) == 3.5

    def test_missing_pricing_is_free(self):
        assert _calculate_cost_usd({"input_tokens": 1_000_000}, {}) == 0.0

    def test_per_million_scaling_avoids_intermediate_overflow(self):
        """Retain a representable cost even when unscaled products overflow."""
        cost = _calculate_cost_usd({"input_tokens": 100}, {"pricing": {"input": 1e308}})

        assert cost == pytest.approx(1e304)

    @pytest.mark.asyncio
    async def test_streaming_route_persists_calculated_cost(self, store, recorder):
        """Persist streamed usage cost with the selected provider prices."""
        sse = (
            b"event: message_start\n"
            b'data: {"message":{"usage":{"input_tokens":100,'
            b'"cache_read_input_tokens":200,"cache_creation_input_tokens":50}}}\n\n'
            b"event: message_delta\n"
            b'data: {"usage":{"output_tokens":25}}\n\n'
            b"event: message_stop\n"
            b"data: {}\n\n"
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=sse)
        )
        http_client = httpx.AsyncClient(transport=transport)
        config = parse_config_data(
            {
                "router": {"mode": "failover"},
                "providers": {
                    "provider": {
                        "type": "anthropic",
                        "api_key": "test-key",
                        "base_url": "https://provider.test",
                        "models": {
                            "real-model": {
                                "input_price_per_million": 2.0,
                                "output_price_per_million": 8.0,
                                "cache_read_price_per_million": 0.2,
                                "cache_write_price_per_million": 3.0,
                            }
                        },
                    }
                },
                "models": {
                    "priced": {
                        "models": [{"provider": "provider", "model": "real-model"}]
                    }
                },
            }
        )
        router = Router(config, http_client)
        outcome: dict = {}
        body = {
            "model": "priced",
            "stream": True,
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        }

        try:
            async for _ in _stream_wrapper(
                router.route_stream(body, outcome),
                outcome=outcome,
                recorder=recorder,
                virtual_model="priced",
                request_body=body,
                start_time=0.0,
                request_id="stream-cost-test",
            ):
                pass
        finally:
            await http_client.aclose()

        await recorder.wait_idle(timeout=1)
        call = await _only_call_detail(store)
        assert call["input_tokens"] == 100
        assert call["output_tokens"] == 25
        assert call["cache_read_tokens"] == 200
        assert call["cache_write_tokens"] == 50
        assert call["input_price_per_million"] == 2.0
        assert call["output_price_per_million"] == 8.0
        assert call["cache_read_price_per_million"] == 0.2
        assert call["cache_write_price_per_million"] == 3.0
        assert call["cost_usd"] == pytest.approx(0.00059)

    async def test_stream_usage_survives_long_and_multiline_sse_events(
        self, store, recorder
    ):
        """Record usage when large SSE events span chunks and data lines."""
        start_payload = json.dumps(
            {
                "message": {
                    "padding": "x" * 40_000,
                    "usage": {"input_tokens": 321, "cache_read_input_tokens": 45},
                }
            }
        ).encode()
        sse = (
            b"event: message_start\n"
            b"data: " + start_payload + b"\n\n"
            b"event: message_delta\n"
            b'data: {"usage":\n'
            b'data: {"output_tokens": 17}}\n\n'
        )

        async def source():
            yield sse[:20_000]
            yield sse[20_000:]

        async for _ in _stream_wrapper(
            source(),
            outcome={"attempt": 1},
            recorder=recorder,
            virtual_model="long-usage",
            request_body={"model": "long-usage", "stream": True},
            start_time=0.0,
            request_id="long-usage-test",
        ):
            pass

        await recorder.wait_idle(timeout=1)
        call = await _only_call_detail(store)
        assert call["input_tokens"] == 321
        assert call["output_tokens"] == 17
        assert call["cache_read_tokens"] == 45

    @pytest.mark.parametrize("configured_price", [None, 0.0])
    async def test_non_stream_preserves_missing_and_zero_prices(
        self, store, recorder, configured_price
    ):
        response = {
            "id": "msg_1",
            "type": "message",
            "model": "real-model",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 25,
                "cache_creation_input_tokens": 10,
            },
        }
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=response)
        )
        config = AppConfig(
            server=ServerConfig(),
            router=RouterConfig(mode="failover"),
            models={
                "priced": VirtualModelConfig(
                    providers=[
                        ProviderConfig(
                            type="anthropic",
                            name="provider",
                            model="real-model",
                            api_key="test-key",
                            base_url="https://provider.test",
                            priority=1,
                            input_price_per_million=configured_price,
                            output_price_per_million=configured_price,
                            cache_read_price_per_million=configured_price,
                            cache_write_price_per_million=configured_price,
                        )
                    ]
                )
            },
        )
        body = {
            "model": "priced",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        }

        async with httpx.AsyncClient(transport=transport) as upstream:
            app = create_app(config, store, call_recorder=recorder)
            app.state.router_engine = Router(config, upstream)
            asgi = ASGITransport(app=app)
            async with AsyncClient(transport=asgi, base_url="http://test") as client:
                api_response = await client.post("/v1/messages", json=body)

        assert api_response.status_code == 200
        await recorder.wait_idle(timeout=1)
        call = await _only_call_detail(store)
        for column in (
            "input_price_per_million",
            "output_price_per_million",
            "cache_read_price_per_million",
            "cache_write_price_per_million",
        ):
            assert call[column] == configured_price
        assert call["cost_usd"] == 0.0

    async def test_non_stream_failover_persists_final_provider_prices(
        self, store, recorder
    ):
        usage = {
            "input_tokens": 1_000_000,
            "output_tokens": 500_000,
            "cache_read_input_tokens": 2_000_000,
            "cache_creation_input_tokens": 250_000,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "first.test":
                return httpx.Response(500, text="first failed")
            return httpx.Response(
                200,
                json={"id": "msg_2", "model": "shared-model", "usage": usage},
            )

        transport = httpx.MockTransport(handler)
        config = AppConfig(
            server=ServerConfig(),
            router=RouterConfig(mode="failover"),
            models={
                "router": VirtualModelConfig(
                    providers=[
                        ProviderConfig(
                            type="anthropic",
                            name="first",
                            model="shared-model",
                            api_key="first-key",
                            base_url="https://first.test",
                            priority=1,
                            input_price_per_million=90.0,
                            output_price_per_million=90.0,
                            cache_read_price_per_million=90.0,
                            cache_write_price_per_million=90.0,
                        ),
                        ProviderConfig(
                            type="anthropic",
                            name="second",
                            model="shared-model",
                            api_key="second-key",
                            base_url="https://second.test",
                            priority=2,
                            input_price_per_million=1.0,
                            output_price_per_million=4.0,
                            cache_read_price_per_million=0.1,
                            cache_write_price_per_million=1.2,
                        ),
                    ]
                )
            },
        )
        body = {"model": "router", "max_tokens": 100, "messages": []}

        async with httpx.AsyncClient(transport=transport) as upstream:
            app = create_app(config, store, call_recorder=recorder)
            app.state.router_engine = Router(config, upstream)
            asgi = ASGITransport(app=app)
            async with AsyncClient(transport=asgi, base_url="http://test") as client:
                response = await client.post("/v1/messages", json=body)

        assert response.status_code == 200
        await recorder.wait_idle(timeout=1)
        call = await _only_call_detail(store)
        assert call["provider_name"] == "second"
        assert call["provider_model"] == "shared-model"
        assert call["attempt"] == 2
        assert call["input_price_per_million"] == 1.0
        assert call["output_price_per_million"] == 4.0
        assert call["cache_read_price_per_million"] == 0.1
        assert call["cache_write_price_per_million"] == 1.2
        assert call["cost_usd"] == 3.5

    async def test_stream_failover_persists_final_provider_prices(
        self, store, recorder
    ):
        first_error = (
            b'event: error\ndata: {"type":"error","error":'
            b'{"type":"api_error","message":"first failed"}}\n\n'
        )
        success = (
            b"event: message_start\n"
            b'data: {"message":{"usage":{"input_tokens":100,'
            b'"cache_read_input_tokens":200,"cache_creation_input_tokens":50}}}\n\n'
            b"event: message_delta\n"
            b'data: {"usage":{"output_tokens":25}}\n\n'
            b"event: message_stop\ndata: {}\n\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            content = first_error if request.url.host == "first.test" else success
            return httpx.Response(200, content=content)

        transport = httpx.MockTransport(handler)
        config = AppConfig(
            server=ServerConfig(),
            router=RouterConfig(mode="failover"),
            models={
                "router": VirtualModelConfig(
                    providers=[
                        ProviderConfig(
                            type="anthropic",
                            name="first",
                            model="shared-model",
                            api_key="first-key",
                            base_url="https://first.test",
                            priority=1,
                            input_price_per_million=90.0,
                            output_price_per_million=90.0,
                            cache_read_price_per_million=90.0,
                            cache_write_price_per_million=90.0,
                        ),
                        ProviderConfig(
                            type="anthropic",
                            name="second",
                            model="shared-model",
                            api_key="second-key",
                            base_url="https://second.test",
                            priority=2,
                            input_price_per_million=2.0,
                            output_price_per_million=8.0,
                            cache_read_price_per_million=0.2,
                            cache_write_price_per_million=3.0,
                        ),
                    ]
                )
            },
        )
        body = {
            "model": "router",
            "stream": True,
            "max_tokens": 100,
            "messages": [],
        }

        async with httpx.AsyncClient(transport=transport) as upstream:
            router = Router(config, upstream)
            outcome: dict = {}
            chunks = [
                chunk
                async for chunk in _stream_wrapper(
                    router.route_stream(body, outcome),
                    outcome=outcome,
                    recorder=recorder,
                    virtual_model="router",
                    request_body=body,
                    start_time=0.0,
                    request_id="stream-failover-price-test",
                )
            ]

        assert b"event: error" not in b"".join(chunks)
        await recorder.wait_idle(timeout=1)
        call = await _only_call_detail(store)
        assert call["provider_name"] == "second"
        assert call["provider_model"] == "shared-model"
        assert call["attempt"] == 2
        assert call["input_price_per_million"] == 2.0
        assert call["output_price_per_million"] == 8.0
        assert call["cache_read_price_per_million"] == 0.2
        assert call["cache_write_price_per_million"] == 3.0
        assert call["cost_usd"] == pytest.approx(0.00059)

    async def test_failed_call_has_null_price_snapshots(self, store, recorder):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(500, text="upstream failed")
        )
        config = AppConfig(
            server=ServerConfig(),
            router=RouterConfig(mode="failover"),
            models={
                "router": VirtualModelConfig(
                    providers=[
                        ProviderConfig(
                            type="anthropic",
                            name="first",
                            model="real-model",
                            api_key="test-key",
                            base_url="https://first.test",
                            priority=1,
                            input_price_per_million=1.0,
                            output_price_per_million=4.0,
                            cache_read_price_per_million=0.1,
                            cache_write_price_per_million=1.2,
                        ),
                        ProviderConfig(
                            type="anthropic",
                            name="second",
                            model="real-model",
                            api_key="test-key",
                            base_url="https://second.test",
                            priority=2,
                        ),
                    ]
                )
            },
        )
        body = {"model": "router", "max_tokens": 100, "messages": []}

        async with httpx.AsyncClient(transport=transport) as upstream:
            app = create_app(config, store, call_recorder=recorder)
            app.state.router_engine = Router(config, upstream)
            asgi = ASGITransport(app=app)
            async with AsyncClient(transport=asgi, base_url="http://test") as client:
                response = await client.post("/v1/messages", json=body)

        assert response.status_code == 502
        await recorder.wait_idle(timeout=1)
        call = await _only_call_detail(store)
        assert call["provider_name"] is None
        assert call["provider_model"] is None
        assert call["attempt"] == 2
        assert call["input_price_per_million"] is None
        assert call["output_price_per_million"] is None
        assert call["cache_read_price_per_million"] is None
        assert call["cache_write_price_per_million"] is None
        assert call["cost_usd"] is None
        failures = json.loads(call["failover_details"])
        assert len(failures) == 2
        assert all(isinstance(item["latency_ms"], int) for item in failures)


class TestPrefetchHelpers:
    async def test_prefetch_timeout_returns_pending_task(self):
        async def slow():
            await asyncio.sleep(1.0)
            yield b"data"

        agen = slow()
        first, pending = await _prefetch_first_chunk(agen, timeout=0.05)
        assert first is None
        assert pending is not None
        assert not pending.done()
        await _close_prefetched_stream(agen, pending)
        assert pending.done()

    async def test_prefetch_completes_within_timeout(self):
        async def fast():
            yield b"hello"

        agen = fast()
        first, pending = await _prefetch_first_chunk(agen, timeout=1.0)
        assert first == b"hello"
        assert pending is None
        await _close_prefetched_stream(agen, None)

    async def test_close_waits_for_cancel_before_aclose(self):
        """cancel 后必须等 task 结束再 aclose，避免 generator already running."""
        entered = asyncio.Event()

        async def blocked():
            entered.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(0)  # 让出一拍，模拟清理
                raise
            yield b"x"  # pragma: no cover

        agen = blocked()
        task = asyncio.create_task(anext(agen))
        await entered.wait()
        # 不应抛 RuntimeError
        await _close_prefetched_stream(agen, task)
        assert task.done()


@pytest.fixture
def app_config():
    return AppConfig(
        server=ServerConfig(host="127.0.0.1", port=9456),
        models={
            "test-router": VirtualModelConfig(
                providers=[
                    ProviderConfig(
                        type="anthropic",
                        name="anthropic",
                        model="claude-haiku-4-5-20251001",
                        api_key="sk-ant-test",
                        base_url="https://api.anthropic.com",
                        priority=1,
                    ),
                ]
            ),
        },
    )


@pytest.fixture
async def store():
    s = CallStore(":memory:")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
async def recorder(store):
    call_recorder = CallRecorder(store)
    await call_recorder.start()
    try:
        yield call_recorder
    finally:
        await call_recorder.close()


@pytest.fixture
async def client(app_config, store, recorder):
    app = create_app(app_config, store, call_recorder=recorder)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestHealth:
    @pytest.mark.asyncio
    async def test_health(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestModels:
    @pytest.mark.asyncio
    async def test_list_models(self, client):
        resp = await client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == "test-router"


class TestMessages:
    @pytest.mark.asyncio
    async def test_unknown_model(self, client):
        resp = await client.post(
            "/v1/messages",
            json={
                "model": "nonexistent",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    async def test_unrenderable_upstream_json_has_one_error_record(
        self, app_config, store, recorder
    ):
        """A failed response render must not enqueue a success first."""
        app_config.router.mode = "failover"
        app_config.models["test-router"].providers[0].input_price_per_million = 2.0
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b'{"usage":{"input_tokens":1},"metadata":NaN}',
                headers={"content-type": "application/json"},
            )
        )
        async with httpx.AsyncClient(transport=transport) as upstream:
            app = create_app(app_config, store, call_recorder=recorder)
            app.state.router_engine = Router(app_config, upstream)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as api_client:
                response = await api_client.post(
                    "/v1/messages", json={"model": "test-router", "messages": []}
                )

        assert response.status_code == 502
        await recorder.wait_idle(timeout=1)
        call = await _only_call_detail(store)
        assert call["status"] == "error"
        assert call["error_type"] == "ValueError"
        assert call["attempt"] == 1
        assert call["provider_name"] == "anthropic"
        assert call["provider_model"] == "claude-haiku-4-5-20251001"
        assert call["provider_url"] == "https://api.anthropic.com"
        assert call["input_tokens"] == 1
        assert call["input_price_per_million"] == 2.0
        assert call["cost_usd"] == pytest.approx(0.000002)

    @pytest.mark.parametrize(
        "upstream_body", [b"[]", b'"hello"', b"42", b"true", b"null"]
    )
    async def test_non_object_upstream_json_has_one_error_record(
        self, app_config, store, recorder, upstream_body
    ):
        app_config.router.mode = "failover"
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=upstream_body,
                headers={"content-type": "application/json"},
            )
        )
        async with httpx.AsyncClient(transport=transport) as upstream:
            app = create_app(app_config, store, call_recorder=recorder)
            app.state.router_engine = Router(app_config, upstream)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as api_client:
                response = await api_client.post(
                    "/v1/messages", json={"model": "test-router", "messages": []}
                )

        assert response.status_code == 502
        await recorder.wait_idle(timeout=1)
        call = await _only_call_detail(store)
        assert call["status"] == "error"
        assert call["error_type"] == "ValueError"
        assert call["attempt"] == 1
        assert call["provider_name"] == "anthropic"
        assert call["cost_usd"] == 0.0

    @pytest.mark.parametrize(
        ("content", "message"),
        [
            (b"", "请求体必须是有效的 JSON 对象"),
            (b'{"model":', "请求体必须是有效的 JSON 对象"),
            (b"[]", "请求体顶层必须是 JSON 对象"),
        ],
    )
    async def test_rejects_invalid_json_bodies(self, client, content, message):
        response = await client.post(
            "/v1/messages",
            content=content,
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 400
        assert response.json() == {
            "error": {"type": "invalid_request_error", "message": message}
        }

    @pytest.mark.parametrize("stream", [False, True])
    @pytest.mark.parametrize(
        "raw_value",
        [
            b"NaN",
            b"Infinity",
            b"1e999",
            b'"\\ud800"',
            b'{"\\udfff":"value"}',
        ],
        ids=[
            "nan",
            "infinity",
            "float-overflow",
            "surrogate-value",
            "surrogate-key",
        ],
    )
    async def test_rejects_unforwardable_json_before_routing(
        self, app_config, store, recorder, raw_value, stream
    ):
        """Unencodable client JSON is a 400, never an upstream failure."""
        app_config.router.mode = "failover"
        upstream_calls = 0

        def handler(request):
            nonlocal upstream_calls
            upstream_calls += 1
            raise AssertionError("invalid client JSON must not reach the upstream")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as upstream:
            app = create_app(app_config, store, call_recorder=recorder)
            await app.state.router_engine.http.aclose()
            app.state.router_engine.http = upstream
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as api_client:
                response = await api_client.post(
                    "/v1/messages",
                    content=(
                        b'{"model":"test-router","stream":'
                        + (b"true" if stream else b"false")
                        + b',"extra":'
                        + raw_value
                        + b"}"
                    ),
                    headers={"content-type": "application/json"},
                )

        assert response.status_code == 400
        assert response.json()["error"]["type"] == "invalid_request_error"
        assert upstream_calls == 0
        await recorder.wait_idle(timeout=1)
        _, total = await store.list_calls()
        assert total == 0

    @pytest.mark.parametrize("stream", [False, True])
    @pytest.mark.parametrize("operation", ["loads", "dumps"])
    async def test_json_recursion_errors_are_client_errors(
        self, client, store, recorder, monkeypatch, operation, stream
    ):
        # CPython versions have different JSON nesting limits. Exercise the
        # error path without assuming that a fixed nesting depth is invalid.
        codec = SimpleNamespace(loads=json.loads, dumps=json.dumps)

        def fail_recursion(*args, **kwargs):
            raise RecursionError("JSON nesting limit exceeded")

        monkeypatch.setattr(codec, operation, fail_recursion)
        monkeypatch.setattr(app_module, "json", codec)
        response = await client.post(
            "/v1/messages", json={"model": "test-router", "stream": stream}
        )

        assert response.status_code == 400
        assert response.json()["error"]["type"] == "invalid_request_error"
        await recorder.wait_idle(timeout=1)
        _, total = await store.list_calls()
        assert total == 0

    @pytest.mark.parametrize("stream", [False, True])
    @pytest.mark.parametrize("escaped_unicode", [False, True])
    async def test_forwards_valid_unicode_and_finite_json(
        self, app_config, store, recorder, stream, escaped_unicode
    ):
        app_config.router.mode = "failover"
        body = {
            "model": "test-router",
            "stream": stream,
            "messages": [{"role": "user", "content": "你好 🌏"}],
            "metadata": {"中文🔑": [None, True, 1e308, -0.0, 2**64]},
        }
        received = []
        sse = b'event: message_stop\ndata: {"type":"message_stop"}\n\n'

        def handler(request):
            received.append(json.loads(request.content))
            if stream:
                return httpx.Response(200, content=sse)
            return httpx.Response(200, json={"id": "msg_unicode", "usage": {}})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as upstream:
            app = create_app(app_config, store, call_recorder=recorder)
            await app.state.router_engine.http.aclose()
            app.state.router_engine.http = upstream
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as api_client:
                response = await api_client.post(
                    "/v1/messages",
                    content=json.dumps(body, ensure_ascii=escaped_unicode).encode(),
                    headers={"content-type": "application/json"},
                )

        assert response.status_code == 200
        assert received == [{**body, "model": "claude-haiku-4-5-20251001"}]
        if stream:
            assert response.content == sse
        await recorder.wait_idle(timeout=1)
        call = await _only_call_detail(store)
        assert call["status"] == "success"
        assert json.loads(call["request_body"]) == body

    @pytest.mark.parametrize("depth", [2000, 10000])
    async def test_forwards_deep_json_supported_by_httpx(
        self, app_config, store, recorder, depth
    ):
        app_config.router.mode = "failover"
        content = (
            b'{"model":"test-router","extra":'
            + b"[" * depth
            + b"0"
            + b"]" * depth
            + b"}"
        )
        try:
            expected = json.loads(content)
            expected["model"] = "claude-haiku-4-5-20251001"
            wire_body = httpx.Request(
                "POST", "https://provider.test", json=expected
            ).content
        except RecursionError:
            pytest.skip(f"This Python runtime cannot forward {depth} nested arrays")
        received = []

        def handler(request):
            received.append(request.content)
            return httpx.Response(200, json={"id": "msg_deep", "usage": {}})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as upstream:
            app = create_app(app_config, store, call_recorder=recorder)
            await app.state.router_engine.http.aclose()
            app.state.router_engine.http = upstream
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as api_client:
                response = await api_client.post(
                    "/v1/messages",
                    content=content,
                    headers={"content-type": "application/json"},
                )

        assert response.status_code == 200
        assert received == [wire_body]

    async def test_rejects_oversized_declared_body(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "_MAX_REQUEST_BODY_BYTES", 16)

        response = await client.post(
            "/v1/messages",
            content=b"{}",
            headers={"content-type": "application/json", "content-length": "17"},
        )

        assert response.status_code == 413
        assert response.json()["error"]["type"] == "invalid_request_error"

    async def test_rejects_oversized_streamed_body(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "_MAX_REQUEST_BODY_BYTES", 16)

        async def chunks():
            yield b'{"model":"test-'
            yield b'router","messages":[]}'

        response = await client.post(
            "/v1/messages",
            content=chunks(),
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 413
        assert response.json()["error"]["type"] == "invalid_request_error"

    @pytest.mark.parametrize(
        "body",
        [
            {"messages": []},
            {"model": "", "messages": []},
            {"model": "   ", "messages": []},
            {"model": {}, "messages": []},
            {"model": [], "messages": []},
        ],
    )
    async def test_rejects_missing_or_non_string_model(self, client, body):
        response = await client.post("/v1/messages", json=body)

        assert response.status_code == 400
        assert response.json() == {
            "error": {
                "type": "invalid_request_error",
                "message": "model 必须是非空字符串",
            }
        }

    @pytest.mark.parametrize("stream", [None, 0, 1, "false", [], {}])
    async def test_rejects_non_boolean_stream(self, client, stream):
        response = await client.post(
            "/v1/messages",
            json={"model": "test-router", "messages": [], "stream": stream},
        )

        assert response.status_code == 400
        assert response.json() == {
            "error": {
                "type": "invalid_request_error",
                "message": "stream 必须是布尔值",
            }
        }

    async def test_upstream_client_error_preserves_status(
        self, app_config, store, recorder
    ):
        app_config.router.mode = "failover"
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                400,
                json={
                    "error": {
                        "type": "invalid_request_error",
                        "message": "max_tokens is required",
                    }
                },
            )
        )

        async with httpx.AsyncClient(transport=transport) as upstream:
            app = create_app(app_config, store, call_recorder=recorder)
            app.state.router_engine = Router(app_config, upstream)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as api_client:
                response = await api_client.post(
                    "/v1/messages",
                    json={
                        "model": "test-router",
                        "max_tokens": 10,
                        "messages": [
                            None,
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": 42}],
                            },
                        ],
                    },
                )

        assert response.status_code == 400
        assert response.json()["error"]["type"] == "invalid_request_error"
        assert "max_tokens is required" in response.json()["error"]["message"]
        await recorder.wait_idle(timeout=1)
        call = await _only_call_detail(store)
        assert call["provider_name"] is None
        assert call["provider_model"] is None
        assert call["attempt"] == 1
        assert call["request_tokens"] is None
        failures = json.loads(call["failover_details"])
        assert len(failures) == 1
        assert failures[0]["provider"] == "anthropic"
        assert failures[0]["model"] == "claude-haiku-4-5-20251001"
        assert failures[0]["error"] == call["error_message"]
        assert isinstance(failures[0]["latency_ms"], int)

    async def test_forwards_anthropic_feature_headers(
        self, app_config, store, recorder
    ):
        app_config.router.mode = "failover"
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["headers"] = dict(request.headers)
            seen["body"] = request.read()
            return httpx.Response(
                200,
                json={
                    "id": "msg_headers",
                    "type": "message",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as upstream:
            app = create_app(app_config, store, call_recorder=recorder)
            app.state.router_engine = Router(app_config, upstream)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as api_client:
                response = await api_client.post(
                    "/v1/messages",
                    headers={
                        "anthropic-version": "2026-07-01",
                        "anthropic-beta": "context-1m-2025-08-07",
                    },
                    json={
                        "model": "test-router",
                        "max_tokens": 10,
                        "messages": [],
                    },
                )

        assert response.status_code == 200, response.text
        await recorder.wait_idle(timeout=1)
        headers = seen["headers"]
        upstream_body = seen["body"]
        assert isinstance(headers, dict)
        assert isinstance(upstream_body, bytes)
        typed_headers = cast(dict[str, str], headers)
        assert typed_headers["anthropic-version"] == "2026-07-01"
        assert typed_headers["anthropic-beta"] == "context-1m-2025-08-07"
        assert "_routelet_anthropic_headers" not in upstream_body.decode()
        call = await _only_call_detail(store)
        recorded_body = json.loads(call["request_body"])
        assert "_routelet_anthropic_headers" not in recorded_body

    async def test_disconnect_closes_stream_and_records_cancellation(
        self, store, recorder
    ):
        source_closed = asyncio.Event()

        async def source():
            try:
                yield (
                    b"event: message_start\n"
                    b'data: {"message":{"usage":{"input_tokens":7}}}\n\n'
                )
                await asyncio.Event().wait()
            finally:
                source_closed.set()

        wrapped = _stream_wrapper(
            source(),
            outcome={
                "provider_name": "provider",
                "provider_type": "anthropic",
                "provider_model": "model",
                "provider_url": "https://provider.test",
                "attempt": 1,
            },
            recorder=recorder,
            virtual_model="test-router",
            request_body={"model": "test-router", "stream": True},
            start_time=0,
            request_id="req-client-disconnect",
        )
        response = ManagedStreamingResponse(wrapped, media_type="text/event-stream")

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                raise OSError("client disconnected")

        with pytest.raises(ClientDisconnect):
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}},
                receive,
                send,
            )

        await recorder.wait_idle(timeout=1)
        assert source_closed.is_set()
        call = await _only_call_detail(store)
        assert call["status"] == "error"
        assert call["error_type"] == "client_cancelled"
        assert call["input_tokens"] == 7
        assert call["provider_name"] == "provider"

    @pytest.mark.asyncio
    async def test_non_stream_request(self, client):
        """非流式请求: 会尝试真实调用 Anthropic API, 预期鉴权失败 (401)."""
        resp = await client.post(
            "/v1/messages",
            json={
                "model": "test-router",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        # 预期 502 因为 api key 是假的, 或者 401 从 Provider 透传
        assert resp.status_code in (401, 502)

    async def test_non_stream_success_survives_record_failure(
        self, app_config, store, recorder, monkeypatch
    ):
        attempted_records: list[dict] = []

        async def fail_record(**record):
            attempted_records.append(record)
            raise OSError("database unavailable")

        monkeypatch.setattr(store, "record", fail_record)
        app_config.router.mode = "failover"
        upstream_response = {
            "id": "msg_success",
            "type": "message",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=upstream_response)
        )

        async with httpx.AsyncClient(transport=transport) as upstream:
            app = create_app(app_config, store, call_recorder=recorder)
            app.state.router_engine = Router(app_config, upstream)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as api_client:
                response = await api_client.post(
                    "/v1/messages",
                    json={
                        "model": "test-router",
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )

        await recorder.wait_idle(timeout=1)
        assert response.status_code == 200
        assert response.json() == upstream_response
        assert len(attempted_records) == 1
        assert attempted_records[0]["status"] == "success"

    async def test_stream_success_survives_record_failure(
        self, app_config, store, recorder, monkeypatch
    ):
        attempted_records: list[dict] = []

        async def fail_record(**record):
            attempted_records.append(record)
            raise OSError("database unavailable")

        monkeypatch.setattr(store, "record", fail_record)
        app_config.router.mode = "failover"
        sse = (
            b"event: message_start\n"
            b'data: {"message":{"usage":{"input_tokens":1}}}\n\n'
            b"event: message_delta\n"
            b'data: {"usage":{"output_tokens":1}}\n\n'
            b"event: message_stop\ndata: {}\n\n"
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=sse)
        )

        async with httpx.AsyncClient(transport=transport) as upstream:
            app = create_app(app_config, store, call_recorder=recorder)
            app.state.router_engine = Router(app_config, upstream)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as api_client:
                response = await api_client.post(
                    "/v1/messages",
                    json={
                        "model": "test-router",
                        "stream": True,
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )

        await recorder.wait_idle(timeout=1)
        assert response.status_code == 200
        assert b"event: error" not in response.content
        assert b"event: message_stop" in response.content
        assert len(attempted_records) == 1
        assert attempted_records[0]["status"] == "success"

    async def test_error_response_survives_record_failure(
        self, client, store, recorder, monkeypatch
    ):
        attempted_records: list[dict] = []

        async def fail_record(**record):
            attempted_records.append(record)
            raise OSError("database unavailable")

        monkeypatch.setattr(store, "record", fail_record)

        response = await client.post(
            "/v1/messages",
            json={"model": "missing", "max_tokens": 10, "messages": []},
        )

        await recorder.wait_idle(timeout=1)
        assert response.status_code == 400
        assert response.json()["error"]["type"] == "invalid_request_error"
        assert len(attempted_records) == 1
        assert attempted_records[0]["error_type"] == "unknown_model"
        assert attempted_records[0]["attempt"] == 0

    async def test_stream_rate_limit_uses_semantic_record_error_type(
        self, store, recorder
    ):
        async def rate_limited_stream():
            yield b"event: message_start\ndata: {}\n\n"
            raise NoProviderAvailableError(
                "test-router",
                [
                    {
                        "provider": "anthropic",
                        "model": "real-model",
                        "error": "rate limited",
                    }
                ],
                kind="rate_limit",
                retry_after=1,
            )

        chunks = [
            chunk
            async for chunk in _stream_wrapper(
                rate_limited_stream(),
                outcome={
                    "provider_name": "anthropic",
                    "provider_type": "anthropic",
                    "provider_model": "real-model",
                    "provider_url": "https://provider.test",
                    "attempt": 1,
                },
                recorder=recorder,
                virtual_model="test-router",
                request_body={"model": "test-router", "stream": True},
                start_time=0,
                request_id="req-stream-rate-limit",
            )
        ]
        await recorder.wait_idle(timeout=1)
        call = await _only_call_detail(store)
        assert call["error_type"] == "rate_limit_error"
        assert call["provider_name"] == "anthropic"
        assert call["provider_model"] == "real-model"
        assert b'"type": "rate_limit_error"' in b"".join(chunks)

    @pytest.mark.asyncio
    async def test_stream_rate_limit_returns_http_429(self, store, recorder):
        """流式在首字节前限流时返回 HTTP 429 + Retry-After，而非 SSE 内嵌错误."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="rl", headers={"Retry-After": "7"})

        transport = httpx.MockTransport(handler)
        http_client = httpx.AsyncClient(transport=transport)
        config = AppConfig(
            server=ServerConfig(),
            router=RouterConfig(mode="sticky"),
            models={
                "m": VirtualModelConfig(
                    pinned_model=ModelRef(provider="p1", model="m1"),
                    providers=[
                        ProviderConfig(
                            type="anthropic",
                            name="p1",
                            model="m1",
                            api_key="k",
                            base_url="https://p1.test",
                            priority=1,
                        )
                    ],
                )
            },
        )
        app = create_app(config, store, call_recorder=recorder)
        # 注入带 mock transport 的 router
        app.state.router_engine = Router(config, http_client)

        asgi = ASGITransport(app=app)
        async with AsyncClient(transport=asgi, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "stream": True,
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        await http_client.aclose()
        assert resp.status_code == 429
        assert resp.headers.get("retry-after") in {"7", "6"}  # ceil of remaining
        assert int(resp.headers.get("retry-after", "0")) >= 6
        assert resp.content == b"rl"

    @pytest.mark.asyncio
    async def test_stream_prefetch_timeout_still_returns_sse(
        self, store, recorder, monkeypatch
    ):
        """首字节预取超时后仍应先返回 SSE 头，再在响应体中交付内容."""
        import routelet.app as app_mod

        # 调用时读取模块常量，monkeypatch 可生效
        monkeypatch.setattr(app_mod, "_STREAM_FIRST_BYTE_PREFETCH_TIMEOUT", 0.05)

        async def slow_body():
            await asyncio.sleep(0.2)
            yield b'event: message_start\ndata: {"type":"message_start"}\n\n'

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=slow_body())

        transport = httpx.MockTransport(handler)
        http_client = httpx.AsyncClient(transport=transport)
        config = AppConfig(
            server=ServerConfig(),
            router=RouterConfig(mode="failover"),
            models={
                "m": VirtualModelConfig(
                    providers=[
                        ProviderConfig(
                            type="anthropic",
                            name="p1",
                            model="m1",
                            api_key="k",
                            base_url="https://p1.test",
                            priority=1,
                        )
                    ],
                )
            },
        )
        app = create_app(config, store, call_recorder=recorder)
        app.state.router_engine = Router(config, http_client)

        asgi = ASGITransport(app=app)
        async with AsyncClient(transport=asgi, base_url="http://test") as ac:
            resp = await ac.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "stream": True,
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        await http_client.aclose()
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        assert b"message_start" in resp.content


class TestRecordCall:
    @pytest.mark.asyncio
    async def test_record_and_retrieve(self, store):
        call_id = await store.record(
            virtual_model="test-router",
            status="success",
            provider_type="anthropic",
            provider_model="claude-test",
            latency_ms=500,
            input_tokens=100,
            output_tokens=50,
        )
        assert call_id is not None

        call = await store.get_call(call_id)
        assert call is not None
        assert call["virtual_model"] == "test-router"
        assert call["status"] == "success"
        assert call["input_tokens"] == 100

    @pytest.mark.asyncio
    async def test_summary(self, store):
        await store.record(
            virtual_model="test",
            status="success",
            input_tokens=100,
            output_tokens=50,
        )
        await store.record(
            virtual_model="test",
            status="error",
            error_type="timeout",
            error_message="timeout",
        )
        summary = await store.summary()
        assert summary["total_calls"] == 2
        assert summary["success_count"] == 1
        assert summary["error_count"] == 1
        assert summary["success_rate"] == 50.0
        assert summary["total_input_tokens"] == 100
        assert summary["total_output_tokens"] == 50

    @pytest.mark.asyncio
    async def test_daily_trend_includes_token_details_and_cost(self, store):
        await store.record(
            virtual_model="test",
            status="success",
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=300,
            cache_write_tokens=25,
            cost_usd=0.0125,
        )

        rows = await store.daily_trend(days=1)

        assert len(rows) == 1
        assert rows[0]["input_tokens"] == 100
        assert rows[0]["output_tokens"] == 50
        assert rows[0]["cache_read_tokens"] == 300
        assert rows[0]["cache_write_tokens"] == 25
        assert rows[0]["cost_usd"] == 0.0125

    @pytest.mark.asyncio
    async def test_list_calls_pagination(self, store):
        for i in range(5):
            await store.record(
                virtual_model="test",
                status="success",
            )
        calls, total = await store.list_calls(page=1, size=3)
        assert len(calls) == 3
        assert total == 5

    async def test_calls_api_separates_summaries_from_complete_details(
        self, store, client
    ):
        call_id = await store.record(
            virtual_model="test-router",
            status="success",
            provider_name="provider",
            provider_type="anthropic",
            provider_model="model",
            provider_url="https://provider.test",
            request_body={"messages": [{"content": "private prompt"}]},
            response_body={"content": [{"text": "private reply"}]},
            failover_details=[
                {"provider": "first", "model": "model", "error": "failed"}
            ],
            input_price_per_million=1.0,
        )

        list_response = await client.get("/api/calls")
        detail_response = await client.get(f"/api/calls/{call_id}")

        assert list_response.status_code == 200
        payload = list_response.json()
        assert payload["total"] == 1
        assert set(payload["data"][0]) == set(CALL_SUMMARY_COLUMNS)
        assert set(payload["data"][0]).isdisjoint(
            {"request_body", "response_body", "failover_details"}
        )
        assert payload["data"][0]["id"] == call_id

        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert "private prompt" in detail["request_body"]
        assert "private reply" in detail["response_body"]
        assert "first" in detail["failover_details"]
        assert detail["provider_url"] == "https://provider.test"
        assert detail["input_price_per_million"] == 1.0

    @pytest.mark.asyncio
    async def test_list_calls_status_filter(self, store):
        await store.record(virtual_model="m", status="success")
        await store.record(
            virtual_model="m",
            status="error",
            error_type="timeout",
            error_message="boom",
        )
        await store.record(virtual_model="m", status="success")

        calls, total = await store.list_calls(status="error")
        assert total == 1
        assert all(c["status"] == "error" for c in calls)

        calls, total = await store.list_calls(status="success")
        assert total == 2
        assert all(c["status"] == "success" for c in calls)

    @pytest.mark.asyncio
    async def test_list_calls_model_status_combo(self, store):
        await store.record(virtual_model="a", status="success")
        await store.record(
            virtual_model="a",
            status="error",
            error_type="x",
            error_message="y",
        )
        await store.record(
            virtual_model="b",
            status="error",
            error_type="x",
            error_message="y",
        )

        calls, total = await store.list_calls(model="a", status="error")
        assert total == 1
        assert all(c["virtual_model"] == "a" and c["status"] == "error" for c in calls)

        # 单独 model 过滤仍正常工作
        _, total = await store.list_calls(model="b")
        assert total == 1

    async def test_provider_metrics_group_by_name(self, store, client):
        await store.record(
            virtual_model="router",
            status="success",
            provider_name="provider-a",
            provider_type="anthropic",
        )
        await store.record(
            virtual_model="router",
            status="error",
            provider_name="provider-a",
            provider_type="anthropic",
        )
        await store.record(
            virtual_model="router",
            status="success",
            provider_name="provider-b",
            provider_type="anthropic",
        )
        await store.record(virtual_model="router", status="error")

        response = await client.get("/api/metrics/by-provider")

        assert response.status_code == 200
        assert {
            row["provider"]: (row["count"], row["success_count"])
            for row in response.json()
        } == {
            "provider-a": (2, 1),
            "provider-b": (1, 1),
            "unknown": (1, 0),
        }

    async def test_real_model_metrics_group_by_provider_and_model(self, store, client):
        await store.record(
            virtual_model="router-a",
            status="success",
            provider_name="provider-a",
            provider_model="shared-model",
            input_tokens=100,
        )
        await store.record(
            virtual_model="router-b",
            status="success",
            provider_name="provider-b",
            provider_model="shared-model",
            input_tokens=200,
        )

        response = await client.get("/api/metrics/by-real-model")

        assert response.status_code == 200
        rows = response.json()
        assert {
            (row["provider"], row["model"], row["total_input_tokens"]) for row in rows
        } == {
            ("provider-a", "shared-model", 100),
            ("provider-b", "shared-model", 200),
        }
        assert all("display_name" not in row for row in rows)

    async def test_list_calls_filters_by_structured_actual_model(self, store):
        await store.record(
            virtual_model="router",
            status="success",
            provider_name="provider-a",
            provider_model="shared-model",
        )
        await store.record(
            virtual_model="router",
            status="success",
            provider_name="provider-b",
            provider_model="shared-model",
        )

        calls, total = await store.list_calls(
            provider="provider-b", provider_model="shared-model"
        )

        assert total == 1
        assert calls[0]["provider_name"] == "provider-b"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("final_status", [200, 400, 500, "removed", "unresolved"])
async def test_hot_reload_preserves_total_attempts_in_call_records(
    app_config, store, recorder, stream, final_status
):
    """Keep real attempts and failure history across a configuration restart."""
    app_config.router.mode = "failover"
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            new_config = app_config.model_copy(deep=True)
            if final_status == "removed":
                new_config.models.clear()
            elif final_status == "unresolved":
                new_config.models["test-router"].providers[
                    0
                ].api_key = "${MISSING_REVIEW_TEST_KEY}"
            await router.reload_config(new_config)
            return httpx.Response(500, text="failure before reload")
        if final_status != 200:
            return httpx.Response(int(final_status), text="failure after reload")
        if stream:
            return httpx.Response(
                200,
                content=b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
            )
        return httpx.Response(200, json={"usage": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(app_config, store, call_recorder=recorder)
        original_router = app.state.router_engine
        router = Router(app_config, upstream)
        app.state.router_engine = router
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as api_client:
                response = await api_client.post(
                    "/v1/messages",
                    json={"model": "test-router", "stream": stream, "messages": []},
                )
        finally:
            await original_router.http.aclose()

    expected_status = 400 if final_status == "removed" else final_status
    if expected_status in (500, "unresolved"):
        expected_status = 502
    assert response.status_code == expected_status
    await recorder.wait_idle(timeout=1)
    call = await _only_call_detail(store)
    assert (
        call["attempt"]
        == calls
        == (1 if final_status in ("removed", "unresolved") else 2)
    )
    failures = json.loads(call["failover_details"])
    assert "failure before reload" in failures[0]["error"]
    assert len(failures) == (2 if final_status in (400, 500, "unresolved") else 1)
    if final_status == "unresolved":
        assert "MISSING_REVIEW_TEST_KEY" in failures[-1]["error"]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("blocked", ["capacity", "cooldown", "circuit", "key"])
async def test_skipped_providers_record_zero_real_attempts(
    app_config, store, recorder, stream, blocked
):
    """Do not report a provider skip as an upstream API call."""
    app_config.router.mode = "failover"
    provider = app_config.models["test-router"].providers[0]
    if blocked == "capacity":
        provider.max_concurrent = 1
    elif blocked == "key":
        provider.api_key = "${MISSING_REVIEW_TEST_KEY}"

    def unexpected(request):
        raise AssertionError("blocked provider must not be called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as upstream:
        app = create_app(app_config, store, call_recorder=recorder)
        original_router = app.state.router_engine
        router = Router(app_config, upstream)
        app.state.router_engine = router
        if blocked == "cooldown":
            router.provider_gate.enter_cooldown(provider.name, 60)
        elif blocked == "circuit":
            await router.circuit_breaker.record_failure(provider.name, immediate=True)

        async def send_request():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as api_client:
                return await api_client.post(
                    "/v1/messages",
                    json={"model": "test-router", "stream": stream, "messages": []},
                )

        try:
            if blocked == "capacity":
                async with router.provider_gate.slot(provider):
                    response = await send_request()
            else:
                response = await send_request()
        finally:
            await original_router.http.aclose()

    assert response.status_code in (429, 502, 503)
    await recorder.wait_idle(timeout=1)
    call = await _only_call_detail(store)
    assert call["attempt"] == 0
    assert call["provider_name"] is None


@pytest.mark.parametrize("stream", [False, True])
async def test_gate_skip_does_not_inflate_successful_attempts(
    sample_config, store, recorder, stream
):
    """Only count the fallback provider when the first candidate has no capacity."""
    first = sample_config.models["haiku-router"].providers[0]
    first.max_concurrent = 1
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.host)
        if stream:
            return httpx.Response(200, content=b"event: message_stop\ndata: {}\n\n")
        return httpx.Response(200, json={"usage": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(sample_config, store, call_recorder=recorder)
        original_router = app.state.router_engine
        router = Router(sample_config, upstream)
        app.state.router_engine = router
        try:
            async with router.provider_gate.slot(first):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as api_client:
                    response = await api_client.post(
                        "/v1/messages",
                        json={"model": "haiku-router", "stream": stream},
                    )
        finally:
            await original_router.http.aclose()

    assert response.status_code == 200
    await recorder.wait_idle(timeout=1)
    call = await _only_call_detail(store)
    assert call["attempt"] == len(calls) == 1
    assert call["provider_name"] == "zhipu"


@pytest.mark.parametrize("started", [False, True])
async def test_split_upstream_error_produces_valid_client_sse(
    sample_config, store, recorder, started
):
    """Never concatenate a partial upstream error with the router's error event."""
    calls = 0
    first_event = b'event: message_start\ndata: {"type":"message_start"}\n\n'
    error_event = (
        b'event: error\ndata: {"error":{"type":"api_error","message":"busy"}}\n\n'
    )

    async def body():
        if started:
            yield first_event
        yield error_event[:-1]
        yield error_event[-1:]

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=body() if calls == 1 else first_event)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(sample_config, store, call_recorder=recorder)
        original_router = app.state.router_engine
        app.state.router_engine = Router(sample_config, upstream)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as api_client:
                response = await api_client.post(
                    "/v1/messages", json={"model": "haiku-router", "stream": True}
                )
        finally:
            await original_router.http.aclose()

    assert response.status_code == 200
    events = app_module.SSEDecoder().feed(response.content)
    assert [json.loads(event.data)["type"] for event in events] == (
        ["message_start", "error"] if started else ["message_start"]
    )
    await recorder.wait_idle(timeout=1)
    call = await _only_call_detail(store)
    assert call["attempt"] == calls == (1 if started else 2)
    assert call["status"] == ("error" if started else "success")
    assert len(json.loads(call["failover_details"])) == 1


@pytest.mark.parametrize(
    ("content", "started"),
    [
        (b"", False),
        (b": keepalive\n\n", False),
        (b'event: error\ndata: {"error":{"type":"api_error"}}', False),
        (
            b'event: message_start\ndata: {"type":"message_start"}\n\n'
            b'event: content_block_delta\ndata: {"delta":',
            True,
        ),
    ],
    ids=["empty", "comments-only", "undispatched-error", "partial-after-start"],
)
@pytest.mark.parametrize("mode", ["sticky", "failover"])
async def test_incomplete_upstream_sse_never_records_success(
    sample_config, store, recorder, content, started, mode
):
    """Reject early EOF without silently discarding the failed response."""
    calls = 0
    sample_config.router = RouterConfig(mode=mode)
    vm = sample_config.models["haiku-router"]
    vm.pinned_model = ModelRef(
        provider=vm.providers[0].name, model=vm.providers[0].model
    )

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(sample_config, store, call_recorder=recorder)
        original_router = app.state.router_engine
        app.state.router_engine = Router(sample_config, upstream)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as api_client:
                response = await api_client.post(
                    "/v1/messages", json={"model": "haiku-router", "stream": True}
                )
        finally:
            await original_router.http.aclose()

    if started or (mode == "sticky" and content.startswith(b": keepalive")):
        assert response.status_code == 200
        events = app_module.SSEDecoder().feed(response.content)
        assert [json.loads(event.data)["type"] for event in events] == (
            ["message_start", "error"] if started else ["error"]
        )
    else:
        assert response.status_code == 502
        assert response.json()["error"]["type"] == "api_error"
    await recorder.wait_idle(timeout=1)
    call = await _only_call_detail(store)
    assert call["status"] == "error"
    assert call["attempt"] == calls == 1
    assert len(json.loads(call["failover_details"])) == 1
