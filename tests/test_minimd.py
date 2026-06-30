"""Tests for the stdlib-only Markdown renderer (scrollback.minimd).

Expected HTML values are derived by hand from the documented scope of the
renderer (headings, lists, code, emphasis, links, blockquotes, rules).
"""

from scrollback import minimd


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


# -- math protection -------------------------------------------------------


def test_math_inline_dollar_not_mangled():
    # Underscores/carets/backslashes inside $...$ must survive verbatim, not
    # be turned into emphasis or dropped.
    out = minimd.render(r"energy is $E = mc^2$ and $a_i \cdot b_i$")
    assert "$E = mc^2$" in out
    assert r"$a_i \cdot b_i$" in out
    assert "<em>" not in out  # the lone underscores must NOT become italics


def test_math_display_dollar():
    out = minimd.render(r"$$\nabla \cdot u = 0$$")
    assert r"$$\nabla \cdot u = 0$$" in out


def test_math_escaped_paren_and_bracket():
    assert r"\(\alpha_1\)" in minimd.render(r"see \(\alpha_1\) here")
    assert r"\[\int_0^1 x\,dx\]" in minimd.render(r"\[\int_0^1 x\,dx\]")


def test_math_currency_is_not_treated_as_math():
    # `$5 to $10` is prose, not math: emphasis/escaping must apply normally
    # and no math span is formed.
    out = minimd.render("it cost $5 to $10 today")
    assert out == "<p>it cost $5 to $10 today</p>"


def test_math_raw_mode_preserves_original_delimiters():
    # raw mode (default) restores the exact source delimiters verbatim.
    assert "$E$" in minimd.render(r"x $E$ y")
    assert r"\(E\)" in minimd.render(r"x \(E\) y")


def test_math_latex_mode_wraps_verbatim_source():
    out = minimd.render(r"x $a_i$ y", math="latex")
    assert '<code class="math-src">$a_i$</code>' in out


def test_math_rendered_mode_emits_typeset_placeholder():
    out = minimd.render(r"x $a_i$ y", math="rendered")
    assert 'class="math-tex"' in out
    assert 'data-display="false"' in out
    assert "a_i" in out  # the body, escaped, for KaTeX to read
    out_d = minimd.render(r"$$a_i$$", math="rendered")
    assert 'data-display="true"' in out_d
    assert "math-display" in out_d


def test_math_body_is_html_escaped_in_rendered_mode():
    # A `<` inside the math body must be escaped so it cannot inject HTML.
    out = minimd.render(r"$a < b$", math="rendered")
    assert "<span" in out  # our wrapper span
    assert "a &lt; b" in out
    assert "a < b" not in out


def test_math_inside_code_span_is_left_alone():
    # `$x$` inside an inline code span is code, not math.
    out = minimd.render("`$x_i$`")
    assert "<code>$x_i$</code>" in out
