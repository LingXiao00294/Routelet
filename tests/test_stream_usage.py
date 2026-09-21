"""Regression coverage for malformed streamed accounting metadata."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from structlog.contextvars import get_contextvars

from agent_router.app import _stream_wrapper
from agent_router.db import CallStore
from agent_router.providers.base import RetryableError
from agent_router.recording import CallRecorder


class _Stream:
    """Expose deterministic iteration and explicit source cleanup to tests."""

    def __init__(self, chunks: list[bytes], error: Exception | None = None) -> None:
        self.chunks = iter(chunks)
        self.error = error
        self.closed = False

    def __aiter__(self) -> _Stream:
        return self

    async def __anext__(self) -> bytes:
        chunk = next(self.chunks, None)
        if chunk is not None:
            return chunk
        if self.error is not None:
            raise self.error
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed = True


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


def _usage_events(value: Any) -> bytes:
    return (
        b"event: message_start\n"
        b'data: {"message":{"usage":{"input_tokens":7}}}\n\n'
        b"event: message_delta\ndata: "
        + json.dumps(
            {"usage": {"output_tokens": value, "input_tokens": value}}
        ).encode()
        + b"\n\n"
    )


def _wrap(stream: _Stream, recorder: object) -> AsyncIterator[bytes]:
    return _stream_wrapper(
        stream,
        outcome={"attempt": 1, "pricing": {"input": 1.0, "output": 2.0}},
        recorder=recorder,
        virtual_model="test",
        request_body={"model": "test", "stream": True},
        start_time=time.time(),
        request_id="stream-usage-test",
    )


async def _recorded_call(store: CallStore, recorder: CallRecorder) -> dict:
    await recorder.wait_idle(timeout=1)
    summaries, total = await store.list_calls()
    assert total == 1
    detail = await store.get_call(summaries[0]["id"])
    assert detail is not None
    return detail


@pytest.mark.parametrize(
    "invalid_count",
    ["oops", {}, [], True, -1, 1.5, None, float("inf"), float("nan"), 2**63],
)
async def test_malformed_usage_preserves_response_and_call_record(
    recording: tuple[CallStore, CallRecorder], invalid_count: Any
) -> None:
    store, recorder = recording
    chunk = _usage_events(invalid_count)
    stream = _Stream([chunk])

    delivered = [part async for part in _wrap(stream, recorder)]

    assert delivered == [chunk]
    assert stream.closed
    call = await _recorded_call(store, recorder)
    assert call["status"] == "success"
    assert call["input_tokens"] == 7
    assert call["output_tokens"] is None
    assert call["cost_usd"] == pytest.approx(0.000007)


@pytest.mark.parametrize("cancel", [False, True])
async def test_malformed_usage_preserves_error_record_and_cleanup(
    recording: tuple[CallStore, CallRecorder], cancel: bool
) -> None:
    store, recorder = recording
    chunk = _usage_events("oops")
    stream = _Stream([chunk], error=RetryableError("upstream disconnected"))
    wrapped = _stream_wrapper(
        stream,
        outcome={"attempt": 1, "pricing": {"input": 1.0, "output": 2.0}},
        recorder=recorder,
        virtual_model="test",
        request_body={"model": "test", "stream": True},
        start_time=time.time(),
        request_id="stream-error-usage-test",
    )

    assert await anext(wrapped) == chunk
    if cancel:
        await wrapped.aclose()
    else:
        remaining = [part async for part in wrapped]
        assert len(remaining) == 1
        assert b"event: error" in remaining[0]

    assert stream.closed
    call = await _recorded_call(store, recorder)
    assert call["status"] == "error"
    assert call["error_type"] == ("client_cancelled" if cancel else "RetryableError")
    assert call["input_tokens"] == 7
    assert call["output_tokens"] is None
    assert call["cost_usd"] == pytest.approx(0.000007)


async def test_valid_usage_after_malformed_event_is_recorded(
    recording: tuple[CallStore, CallRecorder],
) -> None:
    store, recorder = recording
    chunks = [
        _usage_events("oops"),
        b'event: message_delta\ndata: {"usage":{"output_tokens":0}}\n\n',
    ]
    stream = _Stream(chunks)

    assert [part async for part in _wrap(stream, recorder)] == chunks

    call = await _recorded_call(store, recorder)
    assert call["status"] == "success"
    assert call["output_tokens"] == 0
    assert stream.closed


@pytest.mark.parametrize(
    "raw_count",
    [b"1" * 5000, b"[" * 10000 + b"0" + b"]" * 10000],
    ids=["integer-digit-limit", "nesting-limit"],
)
async def test_usage_json_limits_do_not_interrupt_delivery(
    recording: tuple[CallStore, CallRecorder], raw_count: bytes
) -> None:
    """Treat parser resource limits like other unusable accounting metadata."""
    store, recorder = recording
    chunks = [
        b'event: message_delta\ndata: {"usage":{"output_tokens":'
        + raw_count
        + b"}}\n\n",
        b'event: message_delta\ndata: {"usage":{"output_tokens":3}}\n\n',
    ]
    stream = _Stream(chunks)

    assert [part async for part in _wrap(stream, recorder)] == chunks

    call = await _recorded_call(store, recorder)
    assert call["status"] == "success"
    assert call["output_tokens"] == 3
    assert call["cost_usd"] == pytest.approx(0.000006)
    assert stream.closed


async def test_recording_failure_cannot_skip_source_cleanup() -> None:
    class FailingRecorder:
        def submit(self, **record: Any) -> bool:
            raise RuntimeError("recorder failure")

    stream = _Stream([_usage_events(1)])
    wrapped = _stream_wrapper(
        stream,
        outcome={"attempt": 1},
        recorder=FailingRecorder(),
        virtual_model="test",
        request_body={},
        start_time=time.time(),
        request_id="recording-failure-test",
    )
    await anext(wrapped)

    with pytest.raises(RuntimeError, match="recorder failure"):
        await wrapped.aclose()

    assert stream.closed
    assert "request_id" not in get_contextvars()
