"""Tests for the dependency-free code highlighter (agentscroll.highlight)."""

from agentscroll import highlight


def test_python_keywords_and_numbers():
    out = highlight.highlight("def f():\n    return 42", "python")
    assert '<span class="hl-kw">def</span>' in out
    assert '<span class="hl-kw">return</span>' in out
    assert '<span class="hl-num">42</span>' in out


def test_strings_highlighted_and_escaped():
    out = highlight.highlight('x = "a<b>"', "python")
    assert '<span class="hl-str">' in out
    # the string content is HTML-escaped
    assert "&lt;b&gt;" in out
    assert "<b>" not in out


def test_whole_line_comment():
    out = highlight.highlight("# just a note", "python")
    assert '<span class="hl-com"># just a note</span>' in out


def test_bash_keywords():
    out = highlight.highlight("for f in *; do echo hi; done", "bash")
    assert '<span class="hl-kw">for</span>' in out
    assert '<span class="hl-kw">echo</span>' in out


def test_unknown_language_still_escapes_safely():
    out = highlight.highlight("<script>alert(1)</script>", "ada")
    assert "&lt;script&gt;" in out
    assert "<script>" not in out


def test_injection_in_strings_is_safe():
    out = highlight.highlight('s = "</span><script>x</script>"', "javascript")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
