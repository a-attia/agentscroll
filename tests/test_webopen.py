"""Tests for the portable window-opening helper (scrollback.webopen).

These avoid actually launching a browser by stubbing the discovery and
subprocess calls, so they are deterministic and cross-platform.
"""

from scrollback import webopen


def test_open_window_uses_app_mode_when_chromium_found(monkeypatch):
    calls = {}
    monkeypatch.setattr(webopen, "_find_chromium", lambda: "/fake/chrome")

    class FakePopen:
        def __init__(self, cmd, **kw):
            calls["cmd"] = cmd

    monkeypatch.setattr(webopen.subprocess, "Popen", FakePopen)
    result = webopen.open_window("http://127.0.0.1:8765")
    assert result == "app"
    # The Chromium app-mode flag must be present and point at our URL.
    assert any(a == "--app=http://127.0.0.1:8765" for a in calls["cmd"])
    assert calls["cmd"][0] == "/fake/chrome"


def test_open_window_falls_back_to_tab(monkeypatch):
    monkeypatch.setattr(webopen, "_find_chromium", lambda: None)
    opened = {}
    monkeypatch.setattr(webopen.webbrowser, "open", lambda url: opened.setdefault("url", url) or True)
    result = webopen.open_window("http://127.0.0.1:8765")
    assert result == "tab"
    assert opened["url"] == "http://127.0.0.1:8765"


def test_open_window_reports_failure(monkeypatch):
    monkeypatch.setattr(webopen, "_find_chromium", lambda: None)
    monkeypatch.setattr(webopen.webbrowser, "open", lambda url: False)
    assert webopen.open_window("http://x") == "failed"


def test_find_chromium_is_data_driven():
    # The discovery tables exist for the major platforms (data, not logic).
    assert "darwin" in webopen._CHROMIUM_PATHS
    assert "win32" in webopen._CHROMIUM_PATHS
    assert webopen._CHROMIUM_BINARIES  # PATH names checked on all platforms
