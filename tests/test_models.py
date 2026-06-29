"""Unit tests for pure model/conversion helpers (no I/O, deterministic)."""

from datetime import timezone

from agentscroll.models import Message, Part, Session, _to_dt


def test_to_dt_epoch_millis():
    # 1772993881154 ms == 2026-03-06T... UTC; verify round-trip to seconds.
    dt = _to_dt(1772993881154)
    assert dt is not None
    assert dt.tzinfo is timezone.utc
    # Expected value derived directly from the input: 1772993881.154 s.
    assert abs(dt.timestamp() - 1772993881.154) < 1e-3


def test_to_dt_iso_with_z():
    dt = _to_dt("2026-06-08T00:25:52.018Z")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 6 and dt.day == 8
    assert dt.tzinfo is not None


def test_to_dt_none_and_garbage():
    assert _to_dt(None) is None
    assert _to_dt("") is None
    assert _to_dt("not-a-date") is None


def test_message_text_concatenates_textual_parts():
    parts = (
        Part(id="1", type="text", text="hello"),
        Part(id="2", type="reasoning", text="thinking"),
        Part(id="3", type="tool", text="$ ls", tool_name="bash"),
    )
    msg = Message(id="m1", role="assistant", created=None, parts=parts)
    # `.text` includes text + reasoning (both carry .text); tool also has text.
    assert "hello" in msg.text
    assert "thinking" in msg.text


def test_session_short_id():
    s = Session(
        id="ses_0123456789abcdef",
        source="opencode",
        title="t",
        directory=None,
        created=None,
        updated=None,
    )
    assert s.short_id == "ses_01234567"
    assert len(s.short_id) == 12
