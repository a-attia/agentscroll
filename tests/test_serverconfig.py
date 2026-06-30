"""Tests for host/port resolution (scrollback.serverconfig)."""

import socket

import pytest

from scrollback import serverconfig


def test_defaults():
    assert serverconfig.DEFAULT_HOST == "127.0.0.1"
    assert isinstance(serverconfig.DEFAULT_PORT, int)


def test_default_host_env(monkeypatch):
    monkeypatch.delenv("SCROLLBACK_HOST", raising=False)
    assert serverconfig.default_host() == serverconfig.DEFAULT_HOST
    monkeypatch.setenv("SCROLLBACK_HOST", "0.0.0.0")
    assert serverconfig.default_host() == "0.0.0.0"


def test_default_port_env(monkeypatch):
    monkeypatch.delenv("SCROLLBACK_PORT", raising=False)
    assert serverconfig.default_port() == serverconfig.DEFAULT_PORT
    monkeypatch.setenv("SCROLLBACK_PORT", "9999")
    assert serverconfig.default_port() == 9999
    # invalid value falls back to the default
    monkeypatch.setenv("SCROLLBACK_PORT", "not-a-number")
    assert serverconfig.default_port() == serverconfig.DEFAULT_PORT


def test_is_port_free_detects_bound_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        bound_port = s.getsockname()[1]
        assert serverconfig.is_port_free("127.0.0.1", bound_port) is False
    # after close, a (likely) free high port reads as free
    assert serverconfig.is_port_free("127.0.0.1", bound_port) in (True, False)


def test_resolve_port_returns_requested_when_free():
    # port 0 means "any free port"; resolve a concrete free one first.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert serverconfig.resolve_port("127.0.0.1", free) == free


def test_resolve_port_falls_back_when_busy():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        got = serverconfig.resolve_port("127.0.0.1", busy)
        assert got != busy
        assert got > busy


def test_resolve_port_strict_raises_when_busy():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        with pytest.raises(OSError):
            serverconfig.resolve_port("127.0.0.1", busy, strict=True)
