from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from structlog.contextvars import bind_contextvars, clear_contextvars
from structlog.testing import capture_logs

from agent_router.db import MAX_PERSISTED_BODY_BYTES, CallStore
from agent_router.recording import CallRecorder


@pytest.fixture
async def store():
    call_store = CallStore(":memory:")
    await call_store.init()
    try:
        yield call_store
    finally:
        await call_store.close()


class TestCallRecorder:
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"queue_size": 0}, "queue_size"),
            ({"shutdown_timeout": 0}, "shutdown_timeout"),
        ],
    )
    def test_rejects_non_positive_limits(self, kwargs, message):
        with pytest.raises(ValueError, match=message):
            CallRecorder(CallStore(":memory:"), **kwargs)

    async def test_writer_continues_after_store_failure(self, store, monkeypatch):
        recorded: list[str] = []

        async def flaky_record(**record: Any) -> str:
            model = str(record["virtual_model"])
            if model == "first":
                raise OSError("database unavailable")
            recorded.append(model)
            return model

        monkeypatch.setattr(store, "record", flaky_record)
        recorder = CallRecorder(store)
        await recorder.start()
        try:
            assert recorder.submit(virtual_model="first", status="success")
            assert recorder.submit(virtual_model="second", status="success")
            await recorder.wait_idle(timeout=1)
        finally:
            await recorder.close()

        assert recorded == ["second"]

    async def test_full_queue_drops_without_blocking(self, store, monkeypatch):
        started = asyncio.Event()
        release = asyncio.Event()
        recorded: list[str] = []

        async def blocking_record(**record: Any) -> str:
            model = str(record["virtual_model"])
            recorded.append(model)
            started.set()
            await release.wait()
            return model

        monkeypatch.setattr(store, "record", blocking_record)
        recorder = CallRecorder(store, queue_size=1)
        await recorder.start()
        try:
            assert recorder.submit(virtual_model="first", status="success")
            await asyncio.wait_for(started.wait(), timeout=1)
            assert recorder.submit(virtual_model="second", status="success")
            assert not recorder.submit(virtual_model="dropped", status="success")
            release.set()
            await recorder.wait_idle(timeout=1)
        finally:
            release.set()
            await recorder.close()

        assert recorded == ["first", "second"]

    async def test_close_drains_accepted_records(self, store, monkeypatch):
        recorded: list[str] = []

        async def record(**payload: Any) -> str:
            await asyncio.sleep(0)
            model = str(payload["virtual_model"])
            recorded.append(model)
            return model

        monkeypatch.setattr(store, "record", record)
        recorder = CallRecorder(store)
        await recorder.start()
        assert recorder.submit(virtual_model="queued", status="success")

        await recorder.close()

        assert recorded == ["queued"]
        assert not recorder.submit(virtual_model="late", status="success")

    async def test_close_timeout_cancels_blocked_writer(self, store, monkeypatch):
        started = asyncio.Event()

        async def blocked_record(**_record: Any) -> str:
            started.set()
            await asyncio.Event().wait()
            return "unreachable"

        monkeypatch.setattr(store, "record", blocked_record)
        recorder = CallRecorder(store, queue_size=3, shutdown_timeout=0.1)
        await recorder.start()
        bind_contextvars(request_id="req-shutdown")
        try:
            with capture_logs() as logs:
                assert recorder.submit(virtual_model="blocked", status="success")
                await asyncio.wait_for(started.wait(), timeout=1)
                assert recorder.submit(virtual_model="pending-1", status="success")
                assert recorder.submit(virtual_model="pending-2", status="success")

                await asyncio.wait_for(recorder.close(), timeout=1)
        finally:
            clear_contextvars()

        await recorder.wait_idle(timeout=1)
        assert not recorder.submit(virtual_model="late", status="success")
        timeout_log = next(
            entry for entry in logs if entry["event"] == "call_record.shutdown_timeout"
        )
        cancelled_log = next(
            entry for entry in logs if entry["event"] == "call_record.cancelled"
        )
        dropped_log = next(
            entry
            for entry in logs
            if entry["event"] == "call_record.dropped"
            and entry.get("reason") == "shutdown_timeout"
        )
        assert timeout_log["pending"] == 3
        assert cancelled_log["request_id"] == "req-shutdown"
        assert dropped_log["dropped"] == 2

    async def test_worker_failure_log_keeps_request_id(self, store, monkeypatch):
        async def fail_record(**_record: Any) -> str:
            raise OSError("database unavailable")

        monkeypatch.setattr(store, "record", fail_record)
        recorder = CallRecorder(store)
        await recorder.start()
        bind_contextvars(request_id="req-record-failure")
        try:
            with capture_logs() as logs:
                assert recorder.submit(virtual_model="failed", status="error")
                clear_contextvars()
                await recorder.wait_idle(timeout=1)
        finally:
            clear_contextvars()
            await recorder.close()

        failure_log = next(
            entry for entry in logs if entry["event"] == "call_record.failed"
        )
        assert failure_log["request_id"] == "req-record-failure"

    async def test_large_bodies_are_bounded_before_persistence(self, store):
        request_text = "输入" * MAX_PERSISTED_BODY_BYTES
        response_text = "output" * MAX_PERSISTED_BODY_BYTES
        recorder = CallRecorder(store)
        await recorder.start()
        try:
            assert recorder.submit(
                virtual_model="large-body",
                status="success",
                request_body={"messages": [{"role": "user", "content": request_text}]},
                response_body={"content": [{"type": "text", "text": response_text}]},
            )
            await recorder.wait_idle(timeout=1)
        finally:
            await recorder.close()

        summaries, total = await store.list_calls()
        assert total == 1
        detail = await store.get_call(summaries[0]["id"])
        assert detail is not None
        assert len(detail["request_body"].encode("utf-8")) <= (MAX_PERSISTED_BODY_BYTES)
        assert len(detail["response_body"].encode("utf-8")) <= (
            MAX_PERSISTED_BODY_BYTES
        )
        stored_request = json.loads(detail["request_body"])
        stored_response = json.loads(detail["response_body"])
        assert stored_request["_truncated"] is True
        assert stored_response["_truncated"] is True
        assert stored_request["_original_bytes"] > MAX_PERSISTED_BODY_BYTES
        assert stored_response["_original_bytes"] > MAX_PERSISTED_BODY_BYTES
        assert detail["request_tokens"] == len(request_text) // 3

    async def test_explicit_request_token_count_is_not_replaced(self, store):
        recorder = CallRecorder(store)
        await recorder.start()
        try:
            assert recorder.submit(
                virtual_model="explicit-tokens",
                status="success",
                request_body={"messages": [{"content": "estimated differently"}]},
                request_tokens=17,
            )
            await recorder.wait_idle(timeout=1)
        finally:
            await recorder.close()

        summaries, total = await store.list_calls()
        assert total == 1
        detail = await store.get_call(summaries[0]["id"])
        assert detail is not None
        assert detail["request_tokens"] == 17

    def test_unavailable_submit_tolerates_untyped_missing_fields(self):
        recorder = CallRecorder(CallStore(":memory:"))
        untyped_payload: dict[str, Any] = {}

        assert not recorder.submit(**untyped_payload)
