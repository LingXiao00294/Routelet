"""Best-effort background persistence for API call records."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Unpack

import structlog
from structlog.contextvars import get_contextvars

from agent_router.db import (
    CallRecordPayload,
    CallStore,
    _estimate_request_tokens,
    _serialize_call_body,
)

logger = structlog.get_logger(__name__)

DEFAULT_QUEUE_SIZE = 1_000
DEFAULT_SHUTDOWN_TIMEOUT = 5.0


@dataclass(slots=True)
class _QueuedCallRecord:
    payload: CallRecordPayload
    request_id: str | None


class CallRecorder:
    """Persist call records in the background without blocking API responses.

    Records are best-effort observability data. A full queue or SQLite failure is
    logged and isolated from the request path rather than propagated to clients.
    """

    def __init__(
        self,
        store: CallStore,
        *,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
    ) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size must be greater than zero")
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be greater than zero")
        self._store = store
        self._queue: asyncio.Queue[_QueuedCallRecord] = asyncio.Queue(
            maxsize=queue_size
        )
        self._shutdown_timeout = shutdown_timeout
        self._worker: asyncio.Task[None] | None = None
        self._active_record: _QueuedCallRecord | None = None
        self._accepting = False

    async def start(self) -> None:
        """Start the background writer if it is not already running."""
        if self._worker is not None and not self._worker.done():
            return
        self._accepting = True
        self._worker = asyncio.create_task(
            self._run(), name="agent-router-call-recorder"
        )

    def submit(self, **record: Unpack[CallRecordPayload]) -> bool:
        """Enqueue a call record without waiting for SQLite.

        Args:
            **record: A complete call record accepted by ``CallStore.record``.

        Returns:
            ``True`` when the record was queued, or ``False`` when recording is
            unavailable or the bounded queue is full.
        """
        virtual_model = record.get("virtual_model")
        status = record.get("status")
        request_id = get_contextvars().get("request_id")
        if not self._accepting or self._worker is None or self._worker.done():
            logger.error(
                "call_record.unavailable",
                virtual_model=virtual_model,
                status=status,
            )
            return False
        if self._queue.full():
            logger.warning(
                "call_record.dropped",
                virtual_model=virtual_model,
                status=status,
                queue_size=self._queue.maxsize,
            )
            return False
        try:
            payload = _prepare_payload_for_queue(record)
        except Exception:
            logger.error(
                "call_record.serialization_failed",
                virtual_model=virtual_model,
                status=status,
                exc_info=True,
            )
            return False
        queued_record = _QueuedCallRecord(
            payload=payload,
            request_id=request_id if isinstance(request_id, str) else None,
        )
        try:
            self._queue.put_nowait(queued_record)
        except asyncio.QueueFull:
            logger.warning(
                "call_record.dropped",
                virtual_model=virtual_model,
                status=status,
                queue_size=self._queue.maxsize,
            )
            return False
        return True

    async def wait_idle(self, timeout: float | None = None) -> None:
        """Wait until all accepted records have finished processing.

        Args:
            timeout: Optional maximum wait in seconds.

        Raises:
            TimeoutError: If accepted records remain after ``timeout`` seconds.
        """
        if timeout is None:
            await self._queue.join()
            return
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except TimeoutError:
            raise TimeoutError("timed out waiting for call records to flush") from None

    async def close(self) -> None:
        """Stop accepting records, drain briefly, and stop the writer."""
        self._accepting = False
        worker = self._worker
        if worker is None:
            return

        timed_out = False
        try:
            await self.wait_idle(timeout=self._shutdown_timeout)
        except TimeoutError:
            timed_out = True
            logger.error(
                "call_record.shutdown_timeout",
                pending=self._queue.qsize() + int(self._active_record is not None),
                timeout_seconds=self._shutdown_timeout,
            )
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            self._worker = None
            if timed_out:
                self._discard_pending()

    async def _run(self) -> None:
        while True:
            queued_record = await self._queue.get()
            self._active_record = queued_record
            record = queued_record.payload
            try:
                await self._store.record(**record)
            except asyncio.CancelledError:
                logger.warning(
                    "call_record.cancelled",
                    virtual_model=record.get("virtual_model"),
                    status=record.get("status"),
                    request_id=queued_record.request_id,
                    may_have_persisted=True,
                )
                raise
            except Exception:
                logger.error(
                    "call_record.failed",
                    virtual_model=record.get("virtual_model"),
                    status=record.get("status"),
                    request_id=queued_record.request_id,
                    exc_info=True,
                )
            finally:
                self._active_record = None
                self._queue.task_done()

    def _discard_pending(self) -> None:
        """Discard queued records after a shutdown drain timeout."""
        dropped = 0
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._queue.task_done()
            dropped += 1
        if dropped:
            logger.warning(
                "call_record.dropped",
                reason="shutdown_timeout",
                dropped=dropped,
            )


def _prepare_payload_for_queue(record: CallRecordPayload) -> CallRecordPayload:
    """Serialize and bound large bodies before they enter the recorder queue.

    Request token estimation runs against the complete parsed body before it is
    replaced by a bounded JSON preview, preserving metrics without retaining a
    potentially 50 MiB object for the lifetime of a slow SQLite queue.
    """
    payload = record.copy()
    request_body = payload.get("request_body")
    if payload.get("request_tokens") is None and isinstance(request_body, dict):
        payload["request_tokens"] = _estimate_request_tokens(request_body)
    payload["request_body"] = _serialize_call_body(request_body)
    payload["response_body"] = _serialize_call_body(payload.get("response_body"))
    return payload
