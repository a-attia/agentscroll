"""Registry of available source adapters.

To add a new agent: implement a `Source` subclass and append it to
`ALL_SOURCES`. Everything else (CLI, search, export, web) picks it up
automatically.
"""

from __future__ import annotations

from .base import Source
from .claudecode import ClaudeCodeSource
from .opencode import OpenCodeSource

#: Every adapter the program knows about, in display order.
ALL_SOURCES: tuple[type[Source], ...] = (
    OpenCodeSource,
    ClaudeCodeSource,
)


def all_sources() -> list[Source]:
    """Instantiate every registered adapter (cheap; no I/O yet)."""
    return [cls() for cls in ALL_SOURCES]


def available_sources() -> list[Source]:
    """Only adapters whose data store exists on this machine."""
    return [s for s in all_sources() if s.is_available()]


def get_source(name: str) -> Source | None:
    for s in all_sources():
        if s.name == name:
            return s
    return None
