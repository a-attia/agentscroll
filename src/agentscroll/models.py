"""Common data model shared by all source adapters.

Every adapter normalizes its agent's on-disk representation into these
immutable dataclasses, so the rest of the program (CLI, search, export,
web) is agent-agnostic. Keeping these as plain data structures (rather
than behavior-rich classes) follows the "functions over data structures"
principle: many functions operate on these few shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

PartType = Literal[
    "text",
    "reasoning",
    "tool",
    "file",
    "patch",
    "step-start",
    "step-finish",
    "compaction",
    "unknown",
]

Role = Literal["user", "assistant", "system", "tool"]


def _to_dt(ms_or_iso: int | float | str | None) -> datetime | None:
    """Best-effort conversion of a timestamp to an aware UTC datetime.

    Accepts epoch milliseconds (opencode) or ISO-8601 strings (Claude Code).
    Returns None when the input is missing or unparseable.
    """
    if ms_or_iso is None:
        return None
    if isinstance(ms_or_iso, (int, float)):
        # opencode stores epoch milliseconds.
        return datetime.fromtimestamp(ms_or_iso / 1000.0, tz=timezone.utc)
    if isinstance(ms_or_iso, str):
        s = ms_or_iso.strip()
        if not s:
            return None
        try:
            # Handle trailing Z.
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s)
        except ValueError:
            return None
    return None


@dataclass(frozen=True, slots=True)
class Part:
    """A single content block within a message.

    `text` holds a human-readable rendering of the part regardless of type
    (the message body, the reasoning text, a tool's input/output summary).
    `raw` preserves the adapter's original parsed object for fidelity.
    """

    id: str
    type: PartType
    text: str = ""
    tool_name: str | None = None
    tool_status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class Message:
    """One turn in a conversation, composed of ordered parts."""

    id: str
    role: Role
    created: datetime | None
    parts: tuple[Part, ...] = ()
    model: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def text(self) -> str:
        """Concatenated text of all textual parts (text + reasoning)."""
        return "\n".join(p.text for p in self.parts if p.text)


@dataclass(frozen=True, slots=True)
class Session:
    """A whole conversation: metadata plus (optionally) its messages.

    Listing operations populate metadata only and leave `messages` empty
    for speed; loading a single session populates `messages`.
    """

    id: str
    source: str  # adapter name, e.g. "opencode" / "claudecode"
    title: str
    directory: str | None
    created: datetime | None
    updated: datetime | None
    model: str | None = None
    agent: str | None = None
    parent_id: str | None = None
    message_count: int | None = None
    # Usage accounting (opencode tracks these; None when unknown).
    cost: float | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    # Children populated when subagent folding is enabled.
    children: tuple["Session", ...] = ()
    messages: tuple[Message, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def short_id(self) -> str:
        """A compact id suitable for display and prefix selection."""
        return self.id[:12]

    @property
    def is_subagent(self) -> bool:
        """True if this session was spawned by another (has a parent)."""
        return bool(self.parent_id)

    @property
    def tokens_total(self) -> int | None:
        if self.tokens_input is None and self.tokens_output is None:
            return None
        return (self.tokens_input or 0) + (self.tokens_output or 0)


# Re-export the converter for adapters.
__all__ = ["Part", "Message", "Session", "PartType", "Role", "_to_dt"]
