"""Regenerate the README screenshots from synthetic demo data.

Maintainers only. Requires the `screenshots` extra:

    pip install -e ".[screenshots]"
    playwright install chromium     # one-time browser download
    python scripts/screenshots.py

Produces, under assets/screenshots/:
  - cli.svg   : `scrollback list` output, rendered to SVG via rich (no browser)
  - web.png   : the web transcript reader (dark theme), via headless Chromium

All content is synthetic (see scripts/demo_data.py) -- no real data is shown.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

# Make `scripts/` importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from demo_data import demo_sessions, demo_store  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "assets" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)


# -- CLI screenshot (rich -> SVG, no browser) ----------------------------

def render_cli_svg() -> Path:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    from scrollback import termrender

    console = Console(record=True, width=92)
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for col, kw in (
        ("source", {"no_wrap": True}),
        ("id", {"no_wrap": True, "style": "dim"}),
        ("updated", {"no_wrap": True, "style": "dim"}),
        ("msgs", {"justify": "right", "no_wrap": True}),
        ("cost", {"justify": "right", "no_wrap": True}),
        ("tok in/out", {"justify": "right", "no_wrap": True}),
        ("title", {}),
    ):
        table.add_column(col, **kw)

    sessions = sorted(demo_sessions(), key=lambda s: s.updated, reverse=True)
    for s in sessions:
        table.add_row(
            Text(s.source, style=termrender._src_style(s.source)),
            s.short_id,
            termrender._fmt_dt(s.updated),
            str(s.message_count),
            f"${s.cost:.2f}" if s.cost else "",
            f"{termrender._fmt_tokens(s.tokens_input)}/{termrender._fmt_tokens(s.tokens_output)}",
            s.title,
        )

    console.print(Text("$ scrollback list --usage", style="bold green"))
    console.print(table)
    out = OUT / "cli.svg"
    console.save_svg(str(out), title="scrollback")
    print(f"wrote {out}")
    return out


# -- web screenshot (headless Chromium via Playwright) -------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(port: int):
    import uvicorn

    from scrollback.web.app import create_app

    app = create_app(demo_store(), allowed_hosts=[])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                            log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Wait for the port to accept connections.
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return server
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("demo server did not start")


def render_web_png() -> Path:
    from playwright.sync_api import sync_playwright

    port = _free_port()
    server = _serve(port)
    url = f"http://127.0.0.1:{port}/#opencode/ses_demo_heat_eqn_0001"
    out = OUT / "web.png"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 860},
                                    device_scale_factor=2)
            page.goto(url, wait_until="networkidle")
            # Force dark theme + typeset math for the hero shot, then re-open
            # the session so it renders with those settings.
            page.evaluate(
                "localStorage.setItem('scrollback-theme','dark');"
                "localStorage.setItem('scrollback-math','rendered');"
            )
            page.reload(wait_until="networkidle")
            # Give KaTeX a moment to typeset.
            page.wait_for_selector(".katex", timeout=5000)
            page.wait_for_timeout(400)
            page.screenshot(path=str(out))
            browser.close()
    finally:
        server.should_exit = True
    print(f"wrote {out}")
    return out


def main() -> int:
    render_cli_svg()
    try:
        render_web_png()
    except Exception as exc:  # noqa: BLE001
        print(f"web screenshot skipped: {exc}", file=sys.stderr)
        print("  (install the extra + browser: pip install -e '.[screenshots]' "
              "&& playwright install chromium)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
