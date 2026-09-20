"""Verify routed streams close real HTTP response resources deterministically."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress

import httpx
import pytest

from agent_router.config import AppConfig
from agent_router.routing import Router

_START_EVENT = b'event: message_start\ndata: {"type":"message_start"}\n\n'
_ERROR_EVENT = (
    b'event: error\ndata: {"type":"error","error":'
    b'{"type":"api_error","message":"upstream failure"}}\n\n'
)


class _ObservedHTTPStream(httpx.AsyncByteStream):
    """Observe response closure independently from iterator cancellation."""

    def __init__(self, chunks: list[bytes], *, block_read: bool = False) -> None:
        self.chunks = chunks
        self.block_read = block_read
        self.read_started = asyncio.Event()
        self.read_cancelled = asyncio.Event()
        self.close_count = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        if self.block_read:
            self.read_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.read_cancelled.set()
                raise

    async def aclose(self) -> None:
        self.close_count += 1


async def test_closing_router_stream_immediately_closes_upstream(
    sample_config: AppConfig,
) -> None:
    upstream = _ObservedHTTPStream([_START_EVENT], block_read=True)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=upstream)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        router = Router(sample_config, client)
        stream = router.route_stream({"model": "haiku-router", "stream": True})
        try:
            assert await anext(stream) == _START_EVENT
            assert upstream.close_count == 0

            await stream.aclose()

            assert upstream.close_count == 1
            assert router.provider_gate.snapshot()["anthropic"]["in_flight"] == 0
        finally:
            await stream.aclose()


async def test_initial_error_closes_failed_response_before_failover(
    sample_config: AppConfig,
) -> None:
    failed_upstream = _ObservedHTTPStream([_ERROR_EVENT], block_read=True)
    fallback_upstream = _ObservedHTTPStream([_START_EVENT])
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if len(calls) == 1:
            return httpx.Response(200, stream=failed_upstream)
        assert failed_upstream.close_count == 1
        return httpx.Response(200, stream=fallback_upstream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = Router(sample_config, client)
        chunks = [
            chunk
            async for chunk in router.route_stream(
                {"model": "haiku-router", "stream": True}
            )
        ]

        assert chunks == [_START_EVENT]
        assert calls == ["api.anthropic.com", "api.z.ai"]
        assert failed_upstream.close_count == fallback_upstream.close_count == 1
        assert all(
            state["in_flight"] == 0
            for state in router.provider_gate.snapshot().values()
        )


@pytest.mark.parametrize("after_first_event", [False, True])
async def test_cancelling_pending_read_closes_upstream_and_releases_slot(
    sample_config: AppConfig, after_first_event: bool
) -> None:
    upstream = _ObservedHTTPStream(
        [_START_EVENT] if after_first_event else [], block_read=True
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=upstream)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        router = Router(sample_config, client)
        stream = router.route_stream({"model": "haiku-router", "stream": True})
        if after_first_event:
            assert await anext(stream) == _START_EVENT
        pending = asyncio.create_task(anext(stream))
        try:
            await asyncio.wait_for(upstream.read_started.wait(), timeout=1)
            assert router.provider_gate.snapshot()["anthropic"]["in_flight"] == 1

            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending

            assert upstream.read_cancelled.is_set()
            assert upstream.close_count == 1
            assert router.provider_gate.snapshot()["anthropic"]["in_flight"] == 0
        finally:
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
            await stream.aclose()
