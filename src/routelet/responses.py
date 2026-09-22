"""Streaming response primitives with deterministic resource cleanup."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from inspect import isawaitable
from typing import Any

import structlog
from anyio import CancelScope
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from starlette.responses import ContentStream
from starlette.types import Receive, Scope, Send

logger = structlog.get_logger(__name__)


class ManagedStreamingResponse(StreamingResponse):
    """Close the asynchronous response body after completion or disconnect.

    Starlette's streaming loop does not close an async iterator when the ASGI
    ``send`` call fails after a yielded chunk. Closing it in ``finally`` makes
    upstream HTTP contexts and concurrency slots deterministic on disconnects.
    ``on_close`` also releases resources acquired before body iteration starts,
    since closing an unstarted async generator does not execute its ``finally``.
    """

    def __init__(
        self,
        content: ContentStream,
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
        background: BackgroundTask | None = None,
        *,
        on_close: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Register optional idempotent cleanup for pre-acquired resources."""
        super().__init__(content, status_code, headers, media_type, background)
        self._on_close = on_close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Send the response and always finalize its body iterator."""
        try:
            await super().__call__(scope, receive, send)
        finally:
            with CancelScope(shield=True):
                try:
                    close = getattr(self.body_iterator, "aclose", None)
                    if close is not None:
                        try:
                            result: Any = close()
                            if isawaitable(result):
                                await result
                        except Exception:
                            logger.warning(
                                "streaming_response.close_failed", exc_info=True
                            )
                finally:
                    if self._on_close is not None:
                        try:
                            await self._on_close()
                        except Exception:
                            logger.warning(
                                "streaming_response.cleanup_failed", exc_info=True
                            )
