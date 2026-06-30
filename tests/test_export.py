"""Tests for the export renderers using a synthetic session.

Using a hand-built Session (not real on-disk data) keeps these tests
deterministic and independent of the machine.
"""

import json
from datetime import datetime, timezone

from scrollback import export
from scrollback.models import Message, Part, Session


def _sample_session() -> Session:
    created = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    user = Message(
        id="m1",
        role="user",
        created=created,
        parts=(Part(id="p1", type="text", text="How do I list files?"),),
    )
    assistant = Message(
        id="m2",
        role="assistant",
        created=created,
        parts=(
            Part(id="p2", type="reasoning", text="They want ls."),
            Part(id="p3", type="text", text="Use `ls`."),
            Part(id="p4", type="tool", text="$ bash ls\nfile1\nfile2",
                 tool_name="bash", tool_status="completed"),
        ),
    )
    return Session(
        id="ses_test",
        source="opencode",
        title="Listing files",
        directory="/tmp/proj",
        created=created,
        updated=created,
        model="test-model",
        agent="build",
        messages=(user, assistant),
        message_count=2,
    )


def test_markdown_contains_core_fields():
    md = export.to_markdown(_sample_session())
    assert "# Listing files" in md
    assert "How do I list files?" in md
    assert "Use `ls`." in md
    assert "test-model" in md


def test_markdown_can_hide_reasoning_and_tools():
    md = export.to_markdown(_sample_session(), include_reasoning=False, include_tools=False)
    assert "They want ls." not in md
    assert "bash" not in md
    assert "Use `ls`." in md  # plain text always kept


def test_json_is_valid_and_drops_raw():
    out = export.to_json(_sample_session())
    data = json.loads(out)
    assert data["id"] == "ses_test"
    assert len(data["messages"]) == 2
    assert "raw" not in data
    assert all("raw" not in m for m in data["messages"])


def test_html_escapes_and_structures():
    s = _sample_session()
    html = export.to_html(s)
    assert "<title>Listing files</title>" in html
    assert 'class="msg user"' in html
    assert 'class="msg assistant"' in html


def test_html_renders_markdown_in_text_parts():
    # Build a session whose assistant text contains markdown.
    md_text = "## Heading\n\nUse `ls` and:\n\n```bash\nls -la\n```\n"
    msg = Message(
        id="m1", role="assistant", created=None,
        parts=(Part(id="p1", type="text", text=md_text),),
    )
    s = Session(id="s", source="opencode", title="t", directory=None,
                created=None, updated=None, messages=(msg,), message_count=1)
    html = export.to_html(s)
    assert "<h2>Heading</h2>" in html
    assert "<code>ls</code>" in html
    assert 'class="language-bash"' in html
    assert 'class="md"' in html


def test_text_default_hides_reasoning():
    txt = export.to_text(_sample_session())
    assert "They want ls." not in txt  # reasoning off by default
    assert "Use `ls`." in txt
    assert "[tool:bash]" in txt


def _math_session() -> Session:
    text = r"Maxwell: $$\nabla \cdot E = \rho/\epsilon_0$$ and inline $a_i^2$."
    msg = Message(id="m1", role="assistant", created=None,
                  parts=(Part(id="p1", type="text", text=text),))
    return Session(id="s", source="opencode", title="phys", directory=None,
                   created=None, updated=None, messages=(msg,), message_count=1)


def test_html_math_raw_preserves_source_unmangled():
    html = export.to_html(_math_session(), math="raw")
    # The LaTeX survives verbatim -- the underscore/backslash are not mangled.
    assert r"\nabla \cdot E" in html
    assert "$a_i^2$" in html
    assert "<em>" not in html  # the lone subscript underscore is not emphasis


def test_html_math_latex_wraps_verbatim():
    html = export.to_html(_math_session(), math="latex")
    assert '<code class="math-src">$a_i^2$</code>' in html
    assert "data:font/woff2" not in html  # no KaTeX embedded in latex mode


def test_html_math_rendered_embeds_katex_offline():
    html = export.to_html(_math_session(), math="rendered")
    assert 'class="math-tex' in html
    assert 'data-display="true"' in html  # the display $$...$$ span
    # Self-contained: KaTeX JS + fonts inlined, no external asset refs.
    assert "katex" in html.lower()
    assert "data:font/woff2" in html
    assert "url(fonts/" not in html


def test_markdown_and_text_math_is_noop_verbatim():
    md = export.to_markdown(_math_session(), math="latex")
    assert r"$$\nabla \cdot E = \rho/\epsilon_0$$" in md
    txt = export.to_text(_math_session(), math="rendered")
    assert "$a_i^2$" in txt


def test_render_unknown_format_raises():
    try:
        export.render(_sample_session(), "pdf")
    except ValueError as e:
        assert "unknown format" in str(e)
    else:
        raise AssertionError("expected ValueError")
