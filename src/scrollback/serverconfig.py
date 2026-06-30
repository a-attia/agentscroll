"""Single source of truth for the web server's host/port configuration.

Resolution order for host and port (highest precedence first):
1. an explicit CLI flag (`--host` / `--port`)
2. an environment variable (`SCROLLBACK_HOST` / `SCROLLBACK_PORT`)
3. the built-in default

This lets launchers (which just call `scrollback web --window`) be
configured without editing any script -- set the env var. It also keeps
the default in exactly one place rather than scattered magic numbers.
"""

from __future__ import annotations

import os
import socket

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

_ENV_HOST = "SCROLLBACK_HOST"
_ENV_PORT = "SCROLLBACK_PORT"


def default_host() -> str:
    return os.environ.get(_ENV_HOST, DEFAULT_HOST)


def default_port() -> int:
    raw = os.environ.get(_ENV_PORT)
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return DEFAULT_PORT


def is_port_free(host: str, port: int) -> bool:
    """Return True if a TCP server can bind (host, port) right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def resolve_port(host: str, port: int, *, strict: bool = False, tries: int = 20) -> int:
    """Return a bindable port.

    If `port` is free, use it. Otherwise, unless `strict`, scan upward for
    the next free port (port+1, port+2, ...). Raises OSError if `strict`
    and the port is taken, or if no free port is found within `tries`.
    """
    if is_port_free(host, port):
        return port
    if strict:
        raise OSError(f"port {port} on {host} is already in use")
    for candidate in range(port + 1, port + 1 + tries):
        if is_port_free(host, candidate):
            return candidate
    raise OSError(
        f"no free port found near {port} on {host} (tried {tries} ports)"
    )
