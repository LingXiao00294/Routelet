from __future__ import annotations

import pytest

from agent_router.sse import SSEDecodeError, SSEDecoder, SSEEvent


def test_decoder_joins_data_lines_across_chunks() -> None:
    decoder = SSEDecoder()
    raw = (
        b': keepalive\r\nevent: message_delta\r\ndata: {"usage":\r\n'
        b'data: {"output_tokens": 7}}\r\n\r\n'
    )
    events: list[SSEEvent] = []

    for byte in raw:
        events.extend(decoder.feed(bytes((byte,))))

    assert events == [
        SSEEvent(
            event="message_delta",
            data=b'{"usage":\n{"output_tokens": 7}}',
        )
    ]


def test_decoder_returns_multiple_events_and_ignores_data_less_blocks() -> None:
    decoder = SSEDecoder()

    events = decoder.feed(b"id: 1\n\ndata: first\n\ndata: second\ndata: line\n\n")

    assert events == [
        SSEEvent(event="message", data=b"first"),
        SSEEvent(event="message", data=b"second\nline"),
    ]


def test_decoder_rejects_an_unterminated_oversized_event() -> None:
    decoder = SSEDecoder(max_event_bytes=8)

    with pytest.raises(SSEDecodeError, match="exceeds 8 bytes"):
        decoder.feed(b"data: 123")


def test_frames_preserve_comments_and_buffer_incomplete_events() -> None:
    """Expose wire bytes only after a complete frame is available."""
    decoder = SSEDecoder()
    prefix = b": keepalive\n\n"
    event = b"event: message_delta\ndata: first\ndata: second\n\n"

    frames = decoder.feed_frames(prefix + event[:-1])
    assert [frame.raw for frame in frames] == [prefix]
    assert frames[0].event is None
    frames = decoder.feed_frames(event[-1:])
    assert [frame.raw for frame in frames] == [event]
    assert frames[0].event == SSEEvent("message_delta", b"first\nsecond")


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r", b"\r\n"])
def test_finish_rejects_undispatched_data(ending: bytes) -> None:
    """A full JSON value still needs the terminating blank line to be valid SSE."""
    decoder = SSEDecoder()
    assert decoder.feed(b"event: message_stop\ndata: {}" + ending) == []

    with pytest.raises(SSEDecodeError, match="blank line"):
        decoder.finish()


@pytest.mark.parametrize("trailing", [b"", b"\n", b": final keepalive", b"id: 1"])
def test_finish_allows_trailing_non_data_fields(trailing: bytes) -> None:
    decoder = SSEDecoder()
    assert decoder.feed(b"data: complete\n\n" + trailing) == [
        SSEEvent("message", b"complete")
    ]
    decoder.finish()


def test_finish_accepts_crlf_separator_split_before_final_lf() -> None:
    decoder = SSEDecoder()
    events = decoder.feed(b"data: complete\r\n\r") + decoder.feed(b"\n")
    assert events == [SSEEvent("message", b"complete")]
    decoder.finish()


@pytest.mark.parametrize(
    "frame",
    [b"event: error\ndata: busy\n\n", b"data: busy\nevent: error\n\n"],
)
@pytest.mark.parametrize("chunk_size", [1, 1000])
def test_leading_utf8_bom_is_ignored_without_changing_wire_bytes(
    frame: bytes, chunk_size: int
) -> None:
    """Honor the SSE stream signature even when its bytes arrive separately."""
    decoder = SSEDecoder()
    wire = b"\xef\xbb\xbf" + frame
    frames = []
    for start in range(0, len(wire), chunk_size):
        frames.extend(decoder.feed_frames(wire[start : start + chunk_size]))

    assert len(frames) == 1
    assert frames[0].raw == wire
    assert frames[0].event == SSEEvent("error", b"busy")
    decoder.finish()


def test_bom_is_only_ignored_at_the_start_of_the_stream() -> None:
    decoder = SSEDecoder()
    events = decoder.feed(
        b": keepalive\n\n\xef\xbb\xbfevent: error\ndata: ordinary\n\n"
    )

    assert events == [SSEEvent("message", b"ordinary")]


def test_finish_rejects_unterminated_data_after_leading_bom() -> None:
    decoder = SSEDecoder()
    assert decoder.feed(b"\xef\xbb\xbfdata: truncated") == []

    with pytest.raises(SSEDecodeError, match="blank line"):
        decoder.finish()
