"""Cover response cleanup before body iteration and prefetch ownership transfer."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator

import httpx
import pytest
from fastapi.routing import APIRoute
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response
from starlette.types import Message, Scope
from structlog.contextvars import bound_contextvars, get_contextvars

from routelet import app as app_module
from routelet.app import _prefetch_first_chunk, create_app
from routelet.config import AppConfig
from routelet.db import CallStore
from routelet.recording import CallRecorder
from routelet.routing import Router

_START_EVENT = b'event: message_start\ndata: {"type":"message_start"}\n\n'


class _HTTPStream(httpx.AsyncByteStream):
    """Expose both a suspended network read and awaited connection cleanup."""

    def __init__(self, *, first_event_ready: bool) -> None:
        self.first_event_ready = first_event_ready
        self.read_started = asyncio.Event()
        self.read_cancelled = asyncio.Event()
        self.close_count = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self.first_event_ready:
            yield _START_EVENT
        self.read_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.read_cancelled.set()
            raise

    async def aclose(self) -> None:
        # A cancellation checkpoint proves cleanup is shielded, rather than
        # accidentally passing because the mock closes without awaiting.
        await asyncio.sleep(0)
        self.close_count += 1


def _scope(*, legacy: bool = False) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0" if legacy else "2.4"},
        "method": "POST",
        "scheme": "http",
        "path": "/v1/messages",
        "query_string": b"",
        "headers": [],
    }


async def _fail_response_headers(response: Response, *, legacy: bool) -> None:
    """Fail headers through ASGI send errors or legacy disconnect cancellation."""
    headers_started = asyncio.Event()

    async def receive() -> Message:
        await headers_started.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        assert message["type"] == "http.response.start"
        headers_started.set()
        if legacy:
            await asyncio.Event().wait()
        raise OSError("client disconnected before response headers")

    if legacy:
        await response(_scope(legacy=True), receive, send)
    else:
        with pytest.raises(ClientDisconnect):
            await response(_scope(), receive, send)


async def _messages_response(
    config: AppConfig,
    upstream: httpx.AsyncClient,
    store: CallStore,
    recorder: CallRecorder,
) -> tuple[Response, Router]:
    """Call the real endpoint while retaining control over ASGI response sending."""
    app = create_app(config, store, call_recorder=recorder)
    router = app.state.router_engine
    await router.http.aclose()
    router.http = upstream
    scope = _scope()
    scope["app"] = app

    async def receive() -> Message:
        return {
            "type": "http.request",
            "body": json.dumps({"model": "haiku-router", "stream": True}).encode(),
            "more_body": False,
        }

    endpoint = next(
        route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/v1/messages"
    )
    return await endpoint(Request(scope, receive)), router


@pytest.fixture
async def recording() -> AsyncIterator[tuple[CallStore, CallRecorder]]:
    store = CallStore(":memory:")
    await store.init()
    recorder = CallRecorder(store)
    await recorder.start()
    try:
        yield store, recorder
    finally:
        await recorder.close()
        await store.close()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("first_event_ready", [False, True])
async def test_router_header_failure_closes_prefetch_resources(
    sample_config: AppConfig,
    recording: tuple[CallStore, CallRecorder],
    monkeypatch: pytest.MonkeyPatch,
    legacy: bool,
    first_event_ready: bool,
) -> None:
    store, recorder = recording
    monkeypatch.setattr(app_module, "_STREAM_FIRST_BYTE_PREFETCH_TIMEOUT", 0.01)
    recorded_request_ids: list[object] = []
    original_submit = recorder.submit

    def capture_submit(**record):
        recorded_request_ids.append(get_contextvars().get("request_id"))
        return original_submit(**record)

    monkeypatch.setattr(recorder, "submit", capture_submit)
    upstream = _HTTPStream(first_event_ready=first_event_ready)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=upstream)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with bound_contextvars(request_id="header-disconnect"):
            response, router = await _messages_response(
                sample_config, client, store, recorder
            )
        assert router.provider_gate.snapshot()["anthropic"]["in_flight"] == 1

        with bound_contextvars(request_id="unrelated-context"):
            await _fail_response_headers(response, legacy=legacy)
            assert get_contextvars()["request_id"] == "unrelated-context"

        assert upstream.close_count == 1
        assert router.provider_gate.snapshot()["anthropic"]["in_flight"] == 0
        if not first_event_ready:
            assert upstream.read_cancelled.is_set()
        await recorder.wait_idle(timeout=1)
        summaries, total = await store.list_calls()
        assert total == 1
        call = await store.get_call(summaries[0]["id"])
        assert call is not None
        assert call["error_type"] == "client_cancelled"
        assert call["attempt"] == 1
        assert call["provider_name"] == "anthropic"
        assert recorded_request_ids == ["header-disconnect"]


async def test_body_disconnect_and_response_callback_close_and_record_once(
    sample_config: AppConfig, recording: tuple[CallStore, CallRecorder]
) -> None:
    store, recorder = recording
    upstream = _HTTPStream(first_event_ready=True)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=upstream)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response, router = await _messages_response(
            sample_config, client, store, recorder
        )

        async def receive() -> Message:
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            if message["type"] == "http.response.body":
                raise OSError("client disconnected during body send")

        with pytest.raises(ClientDisconnect):
            await response(_scope(), receive, send)

        assert upstream.close_count == 1
        assert router.provider_gate.snapshot()["anthropic"]["in_flight"] == 0
        await recorder.wait_idle(timeout=1)
        summaries, total = await store.list_calls()
        assert total == 1
        call = await store.get_call(summaries[0]["id"])
        assert call is not None
        assert call["error_type"] == "client_cancelled"


async def test_cancelled_prefetch_owner_closes_pending_read(
    sample_config: AppConfig,
) -> None:
    upstream = _HTTPStream(first_event_ready=False)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=upstream)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        router = Router(sample_config, client)
        stream = router.route_stream({"model": "haiku-router", "stream": True})
        owner = asyncio.create_task(_prefetch_first_chunk(stream, timeout=60))
        try:
            await asyncio.wait_for(upstream.read_started.wait(), timeout=1)

            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner

            assert upstream.read_cancelled.is_set()
            assert upstream.close_count == 1
            assert router.provider_gate.snapshot()["anthropic"]["in_flight"] == 0
        finally:
            if not owner.done():
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)
            await stream.aclose()


async def test_cancelled_prefetch_owner_closes_already_completed_first_read() -> None:
    source_closed = asyncio.Event()

    async def source() -> AsyncGenerator[bytes, None]:
        try:
            owner.cancel()
            yield _START_EVENT
        finally:
            source_closed.set()

    owner = asyncio.create_task(_prefetch_first_chunk(source(), timeout=60))

    with pytest.raises(asyncio.CancelledError):
        await owner

    assert source_closed.is_set()


async def test_cancelled_endpoint_during_prefetch_records_call(
    sample_config: AppConfig,
    recording: tuple[CallStore, CallRecorder],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retain the upstream attempt when cancellation precedes response creation."""
    store, recorder = recording
    monkeypatch.setattr(app_module, "_STREAM_FIRST_BYTE_PREFETCH_TIMEOUT", 60)
    upstream = _HTTPStream(first_event_ready=False)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=upstream)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        task = asyncio.create_task(
            _messages_response(sample_config, client, store, recorder)
        )
        try:
            await asyncio.wait_for(upstream.read_started.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        assert upstream.close_count == 1
        await recorder.wait_idle(timeout=1)
        summaries, total = await store.list_calls()
        assert total == 1
        call = await store.get_call(summaries[0]["id"])
        assert call is not None
        assert call["error_type"] == "client_cancelled"
        assert call["attempt"] == 1
        assert call["provider_name"] == "anthropic"


async def test_header_failure_keeps_usage_completed_after_prefetch_timeout(
    sample_config: AppConfig,
    recording: tuple[CallStore, CallRecorder],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Record known usage when a timed-out first read finishes before headers."""
    store, recorder = recording
    sample_config.models["haiku-router"].providers[0].input_price_per_million = 2.0
    monkeypatch.setattr(app_module, "_STREAM_FIRST_BYTE_PREFETCH_TIMEOUT", 0.01)
    allow_first_event = asyncio.Event()
    first_event_emitted = asyncio.Event()

    class DelayedHTTPStream(_HTTPStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            self.read_started.set()
            await allow_first_event.wait()
            first_event_emitted.set()
            yield (
                b"event: message_start\n"
                b'data: {"message":{"usage":{"input_tokens":7}}}\n\n'
            )
            await asyncio.Event().wait()

    upstream = DelayedHTTPStream(first_event_ready=False)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=upstream)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response, router = await _messages_response(
            sample_config, client, store, recorder
        )
        assert upstream.read_started.is_set()
        allow_first_event.set()
        await asyncio.wait_for(first_event_emitted.wait(), timeout=1)
        # Let the anext task return its completed frame before response sending.
        await asyncio.sleep(0)

        await _fail_response_headers(response, legacy=False)

        assert upstream.close_count == 1
        assert router.provider_gate.snapshot()["anthropic"]["in_flight"] == 0
        await recorder.wait_idle(timeout=1)
        summaries, total = await store.list_calls()
        assert total == 1
        call = await store.get_call(summaries[0]["id"])
        assert call is not None
        assert call["error_type"] == "client_cancelled"
        assert call["input_tokens"] == 7
        assert call["cost_usd"] == pytest.approx(0.000014)
