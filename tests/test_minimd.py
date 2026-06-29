"""Tests for the stdlib-only Markdown renderer (agentscroll.minimd).

Expected HTML values are derived by hand from the documented scope of the
renderer (headings, lists, code, emphasis, links, blockquotes, rules).
"""

from agentscroll import minimd


def test_headings():
    assert minimd.render("# A") == "<h1>A</h1>"
    assert minimd.render("### C") == "<h3>C</h3>"


def test_paragraph_and_linebreak():
    assert minimd.render("hello") == "<p>hello</p>"
    # consecutive non-blank lines join with <br>
    assert minimd.render("a\nb") == "<p>a<br>b</p>"


def test_emphasis():
    assert minimd.render("**b**") == "<p><strong>b</strong></p>"
    assert minimd.render("*i*") == "<p><em>i</em></p>"
    assert minimd.render("__b__") == "<p><strong>b</strong></p>"


def test_inline_code_is_escaped_and_isolated():
    out = minimd.render("`a<b>` x")
    assert "<code>a&lt;b&gt;</code>" in out
    # emphasis markers inside code must not be interpreted
    assert minimd.render("`**x**`") == "<p><code>**x**</code></p>"


def test_unordered_and_ordered_lists():
    assert minimd.render("- a\n- b") == "<ul><li>a</li><li>b</li></ul>"
    assert minimd.render("1. a\n2. b") == "<ol><li>a</li><li>b</li></ol>"


def test_fenced_code_block_with_language():
    # With a language tag, the code is syntax-highlighted (spans added).
    out = minimd.render("```python\nx = 1\n```")
    assert out.startswith('<pre><code class="language-python">')
    assert out.endswith("</code></pre>")
    assert 'class="hl-num"' in out  # the numeric literal is highlighted
    assert ">1<" in out


def test_fenced_code_block_without_language_is_plain():
    # No language tag => plain escaped code, no highlight spans.
    out = minimd.render("```\nx = 1\n```")
    assert out == "<pre><code>x = 1</code></pre>"


def test_fenced_code_block_escapes_html():
    out = minimd.render("```\n<script>bad()</script>\n```")
    assert "&lt;script&gt;" in out
    assert "<script>" not in out


def test_blockquote():
    assert minimd.render("> hi") == "<blockquote><p>hi</p></blockquote>"


def test_hr():
    assert minimd.render("---") == "<hr>"


def test_link_escaped():
    out = minimd.render("[go](https://example.com/a?b=1)")
    assert '<a href="https://example.com/a?b=1">go</a>' in out


def test_safety_raw_html_is_escaped():
    out = minimd.render("plain <b>x</b> & <i>y</i>")
    assert "&lt;b&gt;" in out and "&amp;" in out
    assert "<b>" not in out
