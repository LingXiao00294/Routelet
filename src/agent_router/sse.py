"""Incrementally decode bounded Server-Sent Events streams."""

from __future__ import annotations

import re
from dataclasses import dataclass

_LINE_END_RE = rb"(?:\r\n|\r(?!\n)|(?<!\r)\n)"
_EVENT_SEPARATOR_RE = re.compile(_LINE_END_RE + _LINE_END_RE)
_LINE_SPLIT_RE = re.compile(rb"\r\n|\r|\n")
_MAX_SEPARATOR_BYTES = 4
_DEFAULT_MAX_EVENT_BYTES = 1024 * 1024


class SSEDecodeError(ValueError):
    """Raised when an SSE event exceeds its size limit or ends incomplete."""


@dataclass(frozen=True, slots=True)
class SSEEvent:
    """Represent one decoded SSE event with its joined data payload."""

    event: str
    data: bytes


@dataclass(frozen=True, slots=True)
class SSEFrame:
    """Retain one complete wire frame and its optional dispatched event."""

    raw: bytes
    event: SSEEvent | None


class SSEDecoder:
    """Decode complete SSE events across arbitrary byte chunk boundaries.

    The decoder follows SSE field rules for comments, optional spaces after
    colons, arbitrary field order, and multiple ``data`` lines. Incomplete
    events remain buffered until a blank line arrives. The size limit bounds
    memory use when an upstream never terminates an event.

    Args:
        max_event_bytes: Maximum bytes allowed before an event separator.

    Raises:
        ValueError: If ``max_event_bytes`` is not positive.
    """

    def __init__(self, max_event_bytes: int = _DEFAULT_MAX_EVENT_BYTES) -> None:
        if max_event_bytes <= 0:
            raise ValueError("max_event_bytes must be positive")
        self._max_event_bytes = max_event_bytes
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        """Consume bytes and return every newly completed SSE event.

        Args:
            chunk: Next raw byte chunk from the upstream response.

        Returns:
            Complete events terminated by an SSE blank line. Events without
            any ``data`` field are ignored as required by the SSE dispatch
            rules.

        Raises:
            SSEDecodeError: If a complete or buffered event exceeds the size
                limit.
        """
        return [
            frame.event for frame in self.feed_frames(chunk) if frame.event is not None
        ]

    def feed_frames(self, chunk: bytes) -> list[SSEFrame]:
        """Return complete frames without exposing an unvalidated partial event.

        Unlike :meth:`feed`, this preserves wire bytes and data-less comment
        frames so a proxy can validate each event before forwarding it. Pending
        bytes stay subject to the same event size limit.
        """
        previous_length = len(self._buffer)
        self._buffer.extend(chunk)
        frames: list[SSEFrame] = []
        search_from = max(0, previous_length - (_MAX_SEPARATOR_BYTES - 1))
        separator = _EVENT_SEPARATOR_RE.search(self._buffer, search_from)
        while separator is not None:
            raw_event = bytes(self._buffer[: separator.start()])
            raw_frame = bytes(self._buffer[: separator.end()])
            del self._buffer[: separator.end()]
            self._check_size(raw_event)
            event = self._parse_event(raw_event)
            frames.append(SSEFrame(raw=raw_frame, event=event))
            separator = _EVENT_SEPARATOR_RE.search(self._buffer)
        self._check_size(self._buffer)
        return frames

    def finish(self) -> None:
        """Validate EOF without dispatching an unterminated data event.

        SSE requires a blank line before dispatch. A remaining data field at
        EOF therefore represents a truncated response, even when its JSON is
        syntactically complete. Trailing comments, control fields, and line
        endings cannot dispatch data and are discarded.

        Raises:
            SSEDecodeError: If EOF interrupts a frame containing data.
        """
        pending = bytes(self._buffer)
        self._buffer.clear()
        if self._parse_event(pending) is not None:
            raise SSEDecodeError("SSE stream ended before the event's blank line")

    def _check_size(self, raw_event: bytes | bytearray) -> None:
        if len(raw_event) > self._max_event_bytes:
            self._buffer.clear()
            raise SSEDecodeError(f"SSE event exceeds {self._max_event_bytes} bytes")

    @staticmethod
    def _parse_event(raw_event: bytes) -> SSEEvent | None:
        event_type = "message"
        data_lines: list[bytes] = []
        for line in _LINE_SPLIT_RE.split(raw_event):
            if not line or line.startswith(b":"):
                continue
            field, separator, value = line.partition(b":")
            if separator and value.startswith(b" "):
                value = value[1:]
            if field == b"event":
                event_type = value.decode("utf-8", errors="replace") or "message"
            elif field == b"data":
                data_lines.append(value)
        if not data_lines:
            return None
        return SSEEvent(event=event_type, data=b"\n".join(data_lines))
