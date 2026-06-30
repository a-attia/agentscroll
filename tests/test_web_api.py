"""API tests for the web app using a synthetic in-memory Store.

We build adapters backed by hand-made sessions so these tests are fully
deterministic and do not depend on local agent data. FastAPI's TestClient
exercises the real routing + serialization path.
"""

from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from scrollback.models import Message, Part, Session  # noqa: E402
from scrollback.sources.base import Source  # noqa: E402
from scrollback.store import Store  # noqa: E402
from scrollback.web.app import create_app  # noqa: E402


def _session(sid: str, title: str, body: str) -> Session:
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    msg = Message(
        id=f"{sid}-m1",
        role="user",
        created=created,
        parts=(Part(id=f"{sid}-p1", type="text", text=body),),
    )
    return Session(
        id=sid,
        source="fake",
        title=title,
        directory="/tmp/proj",
        created=created,
        updated=created,
        model="m",
        messages=(msg,),
        message_count=1,
    )


class FakeSource(Source):
    name = "fake"
    label = "Fake"

    def __init__(self):
        self._sessions = {
            "s1": _session("s1", "First session", "hello world about pytest"),
            "s2": _session("s2", "Second session", "another conversation entirely"),
        }

    def is_available(self):
        return True

    def location(self):
        from pathlib import Path

        return Path("/tmp/fake")

    def list_sessions(self):
        return iter(self._sessions.values())

    def load_session(self, session_id):
        return self._sessions.get(session_id)


@pytest.fixture
def client():
    # allowed_hosts=[] disables the Host guard for the TestClient (whose
    # default Host header is "testserver"); the guard has its own tests.
    store = Store([FakeSource()])
    return TestClient(create_app(store, allowed_hosts=[]))


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "fake" in r.json()["sources"]


def test_sources(client):
    data = client.get("/api/sources").json()
    assert data[0]["name"] == "fake"
    assert data[0]["available"] is True


def test_sources_includes_unavailable_known_adapters(client):
    # The store has only the 'fake' source, but /api/sources should also
    # surface the registered adapters as unavailable, so the UI can show them.
    data = client.get("/api/sources").json()
    by_name = {s["name"]: s for s in data}
    assert by_name["fake"]["available"] is True
    # Registered adapters not in the store appear, marked unavailable.
    for name in ("opencode", "claudecode", "codex", "aider"):
        assert name in by_name
        assert by_name[name]["available"] is False


def test_sessions_list_and_title_filter(client):
    data = client.get("/api/sessions").json()
    assert set(data) >= {"sessions", "offset", "limit", "has_more"}
    assert len(data["sessions"]) == 2
    filtered = client.get("/api/sessions?q=First").json()["sessions"]
    assert len(filtered) == 1
    assert filtered[0]["title"] == "First session"


def test_sessions_unknown_source_is_400(client):
    # Mirror the CLI's unknown-source contract on the API.
    assert client.get("/api/sessions?source=bogus").status_code == 400
    assert client.get("/api/sessions?source=fake").status_code == 200


def test_sessions_pagination_has_more(client):
    page = client.get("/api/sessions?limit=1").json()
    assert len(page["sessions"]) == 1
    assert page["has_more"] is True
    page2 = client.get("/api/sessions?limit=1&offset=1").json()
    assert len(page2["sessions"]) == 1
    assert page2["sessions"][0]["id"] != page["sessions"][0]["id"]


def test_session_detail_and_404(client):
    ok = client.get("/api/sessions/fake/s1").json()
    assert ok["id"] == "s1"
    assert len(ok["messages"]) == 1
    missing = client.get("/api/sessions/fake/nope")
    assert missing.status_code == 404


def test_session_meta_has_no_messages(client):
    meta = client.get("/api/sessions/fake/s1/meta").json()
    assert meta["id"] == "s1"
    assert "messages" not in meta
    assert meta["message_count"] == 1


def test_session_messages_window(client):
    data = client.get("/api/sessions/fake/s1/messages?offset=0&limit=10").json()
    assert set(data) >= {"messages", "has_more"}
    assert len(data["messages"]) == 1
    assert data["has_more"] is False


def test_search(client):
    hits = client.get("/api/search?q=pytest").json()
    assert len(hits) == 1
    assert hits[0]["session_id"] == "s1"
    assert "pytest" in hits[0]["snippet"].lower()


def test_export_formats_and_headers(client):
    md = client.get("/api/export/fake/s1?format=markdown")
    assert md.status_code == 200
    assert "First session" in md.text
    assert md.headers["content-type"].startswith("text/markdown")

    dl = client.get("/api/export/fake/s1?format=json&download=true")
    assert "attachment" in dl.headers.get("content-disposition", "")

    bad = client.get("/api/export/fake/s1?format=pdf")
    assert bad.status_code == 400


class _MathSource(FakeSource):
    """A source whose one session carries delimited LaTeX, for math tests."""

    def __init__(self):
        self._sessions = {
            "sm": _session("sm", "Math session", r"energy $E = mc^2$ and $$a_i^2$$"),
        }


@pytest.fixture
def math_client():
    return TestClient(create_app(Store([_MathSource()]), allowed_hosts=[]))


def test_export_math_modes(math_client):
    # raw: verbatim source, shielded from markdown mangling.
    raw = math_client.get("/api/export/fake/sm?format=html&math=raw")
    assert raw.status_code == 200
    assert "$E = mc^2$" in raw.text
    assert "<em>" not in raw.text  # the subscript underscore is not emphasis

    # latex: verbatim source wrapped, never typeset, no KaTeX embedded.
    latex = math_client.get("/api/export/fake/sm?format=html&math=latex")
    assert '<code class="math-src">$E = mc^2$</code>' in latex.text
    assert "data:font/woff2" not in latex.text

    # rendered: KaTeX typeset spans + embedded offline assets.
    rendered = math_client.get("/api/export/fake/sm?format=html&math=rendered")
    assert 'class="math-tex' in rendered.text
    assert "data:font/woff2" in rendered.text

    # an unknown math mode is rejected.
    assert math_client.get("/api/export/fake/sm?format=html&math=bogus").status_code == 400


def test_print_view_accepts_math_mode(math_client):
    r = math_client.get("/print/fake/sm?math=rendered")
    assert r.status_code == 200
    assert "window.print" in r.text
    assert 'class="math-tex' in r.text
    assert math_client.get("/print/fake/sm?math=bogus").status_code == 400


def test_heartbeat_config_off_by_default(client):
    cfg = client.get("/api/heartbeat-config").json()
    assert cfg["enabled"] == 0.0


def test_heartbeat_endpoints_enabled_with_watchdog():
    # When a watchdog is configured, the config reports enabled and the
    # heartbeat endpoint accepts pings. (The timing-based auto-shutdown has a
    # >=10s grace period by design; its end-to-end behaviour is covered by a
    # CLI smoke test rather than a slow unit test.)
    app = create_app(Store([FakeSource()]), on_idle=lambda: None, idle_timeout=10.0,
                     allowed_hosts=[])
    c = TestClient(app)
    assert c.get("/api/heartbeat-config").json()["enabled"] == 1.0
    assert c.post("/api/heartbeat").json()["status"] == "ok"


def test_host_guard_rejects_foreign_host():
    # Default (loopback-only) guard: a foreign Host header (DNS-rebinding) 403s,
    # while loopback hosts pass.
    app = create_app(Store([FakeSource()]))  # default loopback allowlist
    c = TestClient(app)
    assert c.get("/api/health", headers={"host": "127.0.0.1:8765"}).status_code == 200
    assert c.get("/api/health", headers={"host": "localhost"}).status_code == 200
    assert c.get("/api/health", headers={"host": "evil.example.com"}).status_code == 403


def test_host_guard_allows_configured_host():
    app = create_app(Store([FakeSource()]), allowed_hosts=["myhost.local"])
    c = TestClient(app)
    assert c.get("/api/health", headers={"host": "myhost.local:9000"}).status_code == 200
    assert c.get("/api/health", headers={"host": "other.com"}).status_code == 403
