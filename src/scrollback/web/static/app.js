"use strict";

// ====================================================================
// helpers
// ====================================================================

const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k === "dataset") Object.assign(n.dataset, v);
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v);
  }
  for (const kid of kids) {
    if (kid == null) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return n;
};

const fmtDate = (iso) => {
  if (!iso) return "?";
  return new Date(iso).toLocaleString(undefined, {
    year: "2-digit", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
};

// Compact relative time for list rows: "3m", "2h", "5d", "3w", "4mo", "2y".
const fmtRelative = (iso) => {
  if (!iso) return "?";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "?";
  const s = Math.max(0, (Date.now() - then) / 1000);
  if (s < 60) return "just now";
  const m = s / 60;
  if (m < 60) return `${Math.floor(m)}m ago`;
  const h = m / 60;
  if (h < 24) return `${Math.floor(h)}h ago`;
  const d = h / 24;
  if (d < 7) return `${Math.floor(d)}d ago`;
  if (d < 30) return `${Math.floor(d / 7)}w ago`;
  if (d < 365) return `${Math.floor(d / 30)}mo ago`;
  return `${Math.floor(d / 365)}y ago`;
};

const fmtTokens = (n) => {
  if (n == null) return "";
  if (n < 1000) return String(n);
  if (n < 1e6) return (n / 1e3).toFixed(1) + "k";
  return (n / 1e6).toFixed(1) + "M";
};

const baseName = (p) => (p ? p.split("/").filter(Boolean).slice(-1)[0] || p : "");

// ---- math spans (delimited LaTeX) ----------------------------------------
// Mirror of the Python `mathspan` module: detect $...$, $$...$$, \(...\), and
// \[...\] and shield them from the Markdown pass (which would otherwise mangle
// `\`, `_`, `*`, `^`). Placeholders use private-use-area sentinels that marked
// treats as inert text and DOMPurify preserves.
const MATH_PH_OPEN = "\uE000MATH";
const MATH_PH_CLOSE = "\uE001";
const MATH_PH_RE = /\uE000MATH(\d+)\uE001/g;

const _esc = (s) => s.replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function findMathSpans(text) {
  // Exclude fenced + inline code regions, then collect non-overlapping spans.
  const code = [];
  const fence = /^[ \t]*(`{3,}|~{3,})[\s\S]*?(?:^[ \t]*\1[ \t]*$|$(?![\s\S]))/gm;
  let m;
  while ((m = fence.exec(text))) code.push([m.index, m.index + m[0].length]);
  const inFence = (p) => code.some(([a, b]) => a <= p && p < b);
  const inlineCode = /(`+)[\s\S]+?\1/g;
  while ((m = inlineCode.exec(text))) {
    if (!inFence(m.index)) code.push([m.index, m.index + m[0].length]);
  }
  const inCode = (s, e) => code.some(([a, b]) => s < b && a < e);

  const patterns = [
    [/\$\$([\s\S]+?)\$\$/g, true],
    [/\\\[([\s\S]+?)\\\]/g, true],
    [/\\\(([\s\S]+?)\\\)/g, false],
    [/\$(?!\s)([^$\n]*[^$\s])\$(?!\d)/g, false],
  ];
  const cands = [];
  for (const [re, display] of patterns) {
    re.lastIndex = 0;
    while ((m = re.exec(text))) {
      if (inCode(m.index, m.index + m[0].length)) continue;
      cands.push({ start: m.index, end: m.index + m[0].length, body: m[1], display, raw: m[0] });
    }
  }
  cands.sort((a, b) => a.start - b.start || (b.end - b.start) - (a.end - a.start));
  const chosen = [];
  let claimed = -1;
  for (const c of cands) {
    if (c.start >= claimed) { chosen.push(c); claimed = c.end; }
  }
  return chosen;
}

function protectMath(text) {
  const spans = findMathSpans(text);
  if (!spans.length) return { masked: text, tokens: [] };
  let out = "";
  let last = 0;
  spans.forEach((s, i) => {
    out += text.slice(last, s.start) + MATH_PH_OPEN + i + MATH_PH_CLOSE;
    last = s.end;
  });
  out += text.slice(last);
  return { masked: out, tokens: spans };
}

// Replace placeholders in the *sanitized HTML string* with per-mode markup.
function restoreMathHtml(html, tokens, mode) {
  if (!tokens.length) return html;
  return html.replace(MATH_PH_RE, (_, idx) => {
    const s = tokens[+idx];
    if (mode === "rendered") {
      const cls = s.display ? "math-tex math-display" : "math-tex";
      return `<span class="${cls}" data-display="${s.display}">${_esc(s.body)}</span>`;
    }
    if (mode === "latex") return `<code class="math-src">${_esc(s.raw)}</code>`;
    return _esc(s.raw); // raw: verbatim source
  });
}

// ---- markdown rendering (vendored marked + highlight.js) -----------------

let _mdReady = false;
function setupMarkdown() {
  if (_mdReady || typeof marked === "undefined") return _mdReady;
  marked.setOptions({
    gfm: true,
    breaks: true,
    highlight: (code, lang) => {
      if (typeof hljs === "undefined") return code;
      try {
        if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang }).value;
        return hljs.highlightAuto(code).value;
      } catch { return code; }
    },
  });
  _mdReady = true;
  return true;
}

function renderMarkdownInto(node, text) {
  // Render `text` as markdown into `node`. Transcript text is UNTRUSTED (the
  // model/user can write arbitrary HTML/script into a message), so the marked
  // output MUST be sanitized before it touches innerHTML. We require both
  // marked and DOMPurify; if either is missing we fall back to plain text so
  // we never inject unsanitized HTML.
  if (setupMarkdown() && typeof DOMPurify !== "undefined") {
    node.classList.add("md");
    // Shield delimited-math spans before Markdown so the renderer can't
    // mangle them; the placeholders are restored after sanitizing.
    const mode = state.math || "raw";
    const { masked, tokens } = protectMath(text);
    const dirty = restoreMathHtml(marked.parse(masked), tokens, mode);
    node.innerHTML = DOMPurify.sanitize(dirty, {
      // Allow normal markdown output but strip scripts, event handlers, and
      // dangerous URI schemes. Forbid iframe/object/embed/form outright.
      FORBID_TAGS: ["script", "style", "iframe", "object", "embed", "form"],
      FORBID_ATTR: ["style"],
    });
    // Highlight any code blocks marked.highlight missed (older marked APIs).
    if (typeof hljs !== "undefined") {
      node.querySelectorAll("pre code:not(.hljs)").forEach((b) => {
        try { hljs.highlightElement(b); } catch { /* ignore */ }
      });
    }
    if (mode === "rendered") typesetMath(node);
  } else {
    node.textContent = text;
  }
}

// Typeset every .math-tex placeholder under `root` with KaTeX (vendored). The
// LaTeX body lives in textContent (escaped on the way in), so it is inert
// until KaTeX reads it. Failures degrade to showing the source.
function typesetMath(root) {
  if (typeof katex === "undefined") return;
  root.querySelectorAll(".math-tex").forEach((node) => {
    if (node.dataset.mathDone) return;
    const src = node.textContent;
    try {
      katex.render(src, node, {
        displayMode: node.dataset.display === "true",
        throwOnError: false,
        output: "html",
      });
      node.dataset.mathDone = "1";
    } catch {
      node.textContent = src; // leave the source visible on failure
    }
  });
}

const debounce = (fn, ms) => {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
};

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 2200);
}

const _SRC_COLORS = {
  opencode: "var(--opencode)",
  claudecode: "var(--claudecode)",
  codex: "var(--codex)",
  aider: "var(--aider)",
};
const _SRC_SOFTS = {
  opencode: "var(--opencode-soft)",
  claudecode: "var(--claudecode-soft)",
  codex: "var(--codex-soft)",
  aider: "var(--aider-soft)",
};
const srcColor = (name) => _SRC_COLORS[name] || "var(--focus)";
const srcSoft = (name) => _SRC_SOFTS[name] || "var(--focus-soft)";

// ====================================================================
// state
// ====================================================================

const PAGE = 50;            // session list page size
const MSG_PAGE = 40;        // transcript message window size

const state = {
  sources: [],
  enabled: new Set(),
  // search scope: which targets the query is matched against.
  scope: { titles: true, contents: false },
  query: "",
  since: "",
  until: "",
  // list pagination
  list: { offset: 0, hasMore: false, loading: false, kind: "sessions" },
  // open transcript
  current: null,            // {source, id}
  msg: { offset: 0, hasMore: false, loading: false },
  reasoning: false,
  tools: true,
  math: "raw",            // raw | latex | rendered (persisted like theme)
  headAutoCollapsed: false, // transient: auto-collapse state when no manual pref
};

// ====================================================================
// theme
// ====================================================================

function applyHljsTheme(theme) {
  const dark = $("#hljs-dark"), light = $("#hljs-light");
  if (!dark || !light) return;
  dark.disabled = theme !== "dark";
  light.disabled = theme !== "light";
}
function initTheme() {
  const saved = localStorage.getItem("scrollback-theme");
  const theme = saved || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  document.documentElement.dataset.theme = theme;
  applyHljsTheme(theme);
}
function toggleTheme() {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("scrollback-theme", next);
  applyHljsTheme(next);
}

// math render mode (raw | latex | rendered), persisted like the theme.
const MATH_MODES = ["raw", "latex", "rendered"];
function initMath() {
  const saved = localStorage.getItem("scrollback-math");
  state.math = MATH_MODES.includes(saved) ? saved : "raw";
}
function setMath(mode) {
  if (!MATH_MODES.includes(mode)) return;
  state.math = mode;
  localStorage.setItem("scrollback-math", mode);
  // Repaint any export buttons whose label reflects the math mode.
  document.querySelectorAll(".btn.math-aware").forEach(
    (b) => b.dispatchEvent(new CustomEvent("scrollback:math")));
  rerenderMessages();
}

// ====================================================================
// sources + filter chips
// ====================================================================

async function loadSources() {
  state.sources = await getJSON("/api/sources");
  // Only sources with data are enabled/filterable; unavailable ones render
  // greyed-out so users can see what scrollback could read.
  state.sources.forEach((s) => { if (s.available) state.enabled.add(s.name); });
  const wrap = $("#srcfilter");
  wrap.replaceChildren(
    ...state.sources.map((s) => {
      if (!s.available) {
        return el("button", {
          class: "src-toggle src-unavailable",
          disabled: "disabled",
          "aria-pressed": "false",
          dataset: { source: s.name },
          title: `${s.label || s.name}: no sessions found on this machine`,
          style: `--src:${srcColor(s.name)};--src-soft:${srcSoft(s.name)}`,
        },
          el("span", { class: "dot" }),
          s.label || s.name
        );
      }
      return el("button", {
        class: "src-toggle",
        "aria-pressed": "true",
        dataset: { source: s.name },
        title: s.location || s.name,
        style: `--src:${srcColor(s.name)};--src-soft:${srcSoft(s.name)}`,
        onclick: (e) => toggleSource(e.currentTarget, s.name),
      },
        el("span", { class: "dot" }),
        el("span", { class: "check" }, "\u2713"),
        s.label || s.name
      );
    })
  );
}

function toggleSource(btn, name) {
  if (state.enabled.has(name)) state.enabled.delete(name);
  else state.enabled.add(name);
  const on = state.enabled.has(name);
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.querySelector(".check").textContent = on ? "\u2713" : "";
  resetAndLoad();
}

function enabledParam() {
  // If exactly one source is enabled, pass it to the API for efficiency.
  return state.enabled.size === 1 ? [...state.enabled][0] : null;
}

// ====================================================================
// query / search
// ====================================================================

const searchInput = $("#search-input");
const scopeTitlesBtn = $("#scope-titles");
const scopeContentsBtn = $("#scope-contents");

function updateScopeButtons() {
  scopeTitlesBtn.setAttribute("aria-pressed", String(state.scope.titles));
  scopeContentsBtn.setAttribute("aria-pressed", String(state.scope.contents));
  // Placeholder reflects the active scope so intent is always clear.
  const where =
    state.scope.titles && state.scope.contents ? "titles + contents"
    : state.scope.contents ? "message contents"
    : "titles";
  searchInput.placeholder = `search ${where}\u2026`;
}

function toggleScope(which) {
  state.scope[which] = !state.scope[which];
  // Never allow an empty scope; fall back to the other target.
  if (!state.scope.titles && !state.scope.contents) {
    state.scope[which === "titles" ? "contents" : "titles"] = true;
  }
  updateScopeButtons();
  resetAndLoad();
}

const onSearchInput = debounce(() => {
  state.query = searchInput.value.trim();
  resetAndLoad();
}, 200);

// ====================================================================
// session list (paginated + infinite scroll)
// ====================================================================

const railEl = $("#rail");
const sessionsEl = $("#sessions");

function resetAndLoad() {
  state.list.offset = 0;
  state.list.hasMore = false;
  sessionsEl.replaceChildren(el("li", { class: "loading" }, "loading\u2026"));
  loadListPage(true);
}

async function loadListPage(reset = false) {
  if (state.list.loading) return;
  state.list.loading = true;
  try {
    const q = state.query;
    const wantContents = state.scope.contents && q;
    const wantTitles = state.scope.titles;
    if (wantContents && wantTitles && q) {
      await loadCombined(reset);          // titles + contents
    } else if (wantContents) {
      await loadSearch(reset);            // contents only
    } else {
      await loadSessions(reset);          // titles only (or no query)
    }
  } catch (err) {
    if (reset) sessionsEl.replaceChildren(el("li", { class: "loading" }, "error: " + err.message));
  } finally {
    state.list.loading = false;
  }
}

async function loadSessions(reset) {
  state.list.kind = "sessions";
  const p = new URLSearchParams({ offset: String(state.list.offset), limit: String(PAGE), fold: "true" });
  if (state.query) p.set("q", state.query);
  if (state.since) p.set("since", state.since);
  if (state.until) p.set("until", state.until);
  const src = enabledParam();
  if (src) p.set("source", src);

  const data = await getJSON("/api/sessions?" + p.toString());
  let rows = data.sessions.filter((s) => state.enabled.has(s.source));
  state.list.hasMore = data.has_more;
  state.list.offset += data.sessions.length;

  if (reset) {
    sessionsEl.replaceChildren();
    $("#count").textContent = `${rows.length}${data.has_more ? "+" : ""} sessions`;
  } else {
    const prev = parseInt($("#count").dataset.n || "0", 10) + rows.length;
    $("#count").textContent = `${prev}${data.has_more ? "+" : ""} sessions`;
  }
  $("#count").dataset.n = String((parseInt($("#count").dataset.n || "0", 10)) + rows.length);
  rows.forEach((s) => sessionsEl.append(sessionRow(s)));
  if (!sessionsEl.children.length) sessionsEl.append(emptyListNode());
}

function emptyListNode() {
  // No sources at all -> onboarding help; sources present -> just no matches.
  if (!state.sources.length) {
    return el("li", { class: "empty-list" },
      el("div", { class: "empty-list-title" }, "No AI-agent sessions found"),
      el("div", { class: "empty-list-body" },
        "scrollback reads, by default:"),
      el("ul", { class: "empty-list-paths" },
        el("li", {}, el("code", {}, "~/.local/share/opencode/opencode.db")),
        el("li", {}, el("code", {}, "~/.claude/projects/"))),
      el("div", { class: "empty-list-body" },
        "Run ", el("code", {}, "scrollback doctor"), " to see what was detected."));
  }
  return el("li", { class: "loading" }, "no sessions");
}

async function loadSearch(reset) {
  state.list.kind = "search";
  // Search is not offset-paginated server-side; fetch a generous cap once.
  if (!reset) return;
  const p = new URLSearchParams({ q: state.query, limit: "200" });
  if (state.since) p.set("since", state.since);
  if (state.until) p.set("until", state.until);
  const hits = (await getJSON("/api/search?" + p.toString()))
    .filter((h) => state.enabled.has(h.source));
  state.list.hasMore = false;
  $("#count").textContent = `${hits.length} content matches`;
  $("#count").dataset.n = String(hits.length);
  sessionsEl.replaceChildren();
  if (!hits.length) { sessionsEl.append(el("li", { class: "loading" }, "no matches")); return; }
  hits.forEach((h) => sessionsEl.append(searchRow(h)));
}

async function loadCombined(reset) {
  state.list.kind = "search";        // single-shot, no infinite scroll
  if (!reset) return;
  // Fetch title matches and content matches in parallel.
  const sp = new URLSearchParams({ offset: "0", limit: "200", fold: "true", q: state.query });
  if (state.since) sp.set("since", state.since);
  if (state.until) sp.set("until", state.until);
  const src = enabledParam();
  if (src) sp.set("source", src);

  const cp = new URLSearchParams({ q: state.query, limit: "200" });
  if (state.since) cp.set("since", state.since);
  if (state.until) cp.set("until", state.until);

  const [titleData, contentHits] = await Promise.all([
    getJSON("/api/sessions?" + sp.toString()),
    getJSON("/api/search?" + cp.toString()),
  ]);
  const titleRows = titleData.sessions.filter((s) => state.enabled.has(s.source));
  const titleIds = new Set(titleRows.map((s) => s.source + ":" + s.id));
  // Drop content hits whose session already appears as a title match.
  const hits = contentHits.filter(
    (h) => state.enabled.has(h.source) && !titleIds.has(h.source + ":" + h.session_id)
  );

  state.list.hasMore = false;
  $("#count").textContent = `${titleRows.length} title + ${hits.length} content`;
  $("#count").dataset.n = String(titleRows.length + hits.length);
  sessionsEl.replaceChildren();
  if (!titleRows.length && !hits.length) {
    sessionsEl.append(el("li", { class: "loading" }, "no matches"));
    return;
  }
  if (titleRows.length) {
    sessionsEl.append(el("li", { class: "group-label" }, "title matches"));
    titleRows.forEach((s) => sessionsEl.append(sessionRow(s)));
  }
  if (hits.length) {
    sessionsEl.append(el("li", { class: "group-label" }, "content matches"));
    hits.forEach((h) => sessionsEl.append(searchRow(h)));
  }
}

railEl.addEventListener("scroll", () => {
  if (state.list.kind !== "sessions" || !state.list.hasMore || state.list.loading) return;
  if (railEl.scrollTop + railEl.clientHeight >= railEl.scrollHeight - 200) {
    loadListPage(false);
  }
});

function sessionRow(s) {
  const li = el("li", {
    class: "session" + (isCurrent(s) ? " active" : ""),
    style: `--src:${srcColor(s.source)}`,
    dataset: { source: s.source, id: s.id },
    onclick: (e) => { if (e.target.closest(".s-children-toggle")) return; openSession(s.source, s.id); },
  },
    el("div", { class: "s-title", title: s.title }, s.title || "(untitled)"),
    metaLine(s)
  );

  if (s.children && s.children.length) {
    const childWrap = el("ul", { class: "s-children", hidden: true });
    s.children.forEach((c) => childWrap.append(childRow(c)));
    const toggle = el("button", { class: "s-children-toggle",
      onclick: () => {
        const open = childWrap.hidden;
        childWrap.hidden = !open;
        toggle.firstChild.textContent = open ? "\u25be" : "\u25b8";
      },
    }, el("span", {}, "\u25b8"), ` ${s.children.length} subagent${s.children.length === 1 ? "" : "s"}`);
    li.append(toggle, childWrap);
  }
  return li;
}

function childRow(c) {
  return el("li", {
    class: "s-child" + (isCurrent(c) ? " active" : ""),
    style: `--src:${srcColor(c.source)}`,
    dataset: { source: c.source, id: c.id },
    onclick: () => openSession(c.source, c.id),
  },
    el("div", { class: "s-title", title: c.title }, c.title || "(untitled)"),
    metaLine(c)
  );
}

function metaLine(s) {
  return el("div", { class: "s-meta" },
    el("span", { class: "s-src" }, s.source),
    el("span", { title: fmtDate(s.updated) }, fmtRelative(s.updated)),
    s.message_count != null ? el("span", {}, `${s.message_count} msgs`) : null,
    s.tokens_input != null ? el("span", { class: "s-badge", title: "tokens in/out" },
      `${fmtTokens(s.tokens_input)}/${fmtTokens(s.tokens_output)}`) : null,
    s.directory ? el("span", { class: "s-dir", title: s.directory }, baseName(s.directory)) : null
  );
}

function searchRow(h) {
  return el("li", {
    class: "session",
    style: `--src:${srcColor(h.source)}`,
    dataset: { source: h.source, id: h.session_id },
    onclick: () => openSession(h.source, h.session_id, h.message_id),
  },
    el("div", { class: "s-title", title: h.title }, h.title || "(untitled)"),
    el("div", { class: "s-meta" },
      el("span", { class: "s-src" }, h.source),
      el("span", {}, `[${h.role}]`),
      h.tool_name ? el("span", {}, h.tool_name) : null),
    snippetNode(h.snippet, state.query)
  );
}

function snippetNode(snippet, q) {
  const div = el("div", { class: "s-snippet" });
  const lc = snippet.toLowerCase();
  const ql = q.trim().toLowerCase();
  let i = 0, pos = ql ? lc.indexOf(ql) : -1;
  if (pos !== -1) {
    while (pos !== -1) {
      div.append(snippet.slice(i, pos));
      div.append(el("mark", {}, snippet.slice(pos, pos + ql.length)));
      i = pos + ql.length;
      pos = lc.indexOf(ql, i);
    }
    div.append(snippet.slice(i));
  } else div.append(snippet);
  return div;
}

function isCurrent(s) {
  return state.current && state.current.source === s.source && state.current.id === s.id;
}
function markActiveRow() {
  document.querySelectorAll(".session.active, .s-child.active").forEach((n) => n.classList.remove("active"));
  if (!state.current) return;
  const sel = `[data-source="${CSS.escape(state.current.source)}"][data-id="${CSS.escape(state.current.id)}"]`;
  document.querySelectorAll(sel).forEach((n) => n.classList.add("active"));
}

// ====================================================================
// transcript reader (meta + windowed messages)
// ====================================================================

let transcriptMeta = null;

async function openSession(source, id, focusMessageId) {
  state.current = { source, id };
  state.msg = { offset: 0, hasMore: false, loading: false };
  state.headAutoCollapsed = false;   // a freshly opened session starts expanded
  const hash = `#${source}/${id}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
  markActiveRow();

  $("#empty").hidden = true;
  const t = $("#transcript");
  t.hidden = false;
  t.replaceChildren(el("div", { class: "loading" }, "loading transcript\u2026"));
  $("#reader").scrollTop = 0;

  let meta;
  try {
    meta = await getJSON(`/api/sessions/${enc(source)}/${enc(id)}/meta`);
  } catch (err) {
    t.replaceChildren(el("div", { class: "loading" }, "error: " + err.message));
    return;
  }
  transcriptMeta = meta;
  renderHeader(meta);
  await loadMessages(true, focusMessageId);
}

function enc(s) { return encodeURIComponent(s); }

function renderHeader(meta) {
  const t = $("#transcript");
  t.style.setProperty("--src", srcColor(meta.source));
  const copyId = el("button", { class: "copy-id", title: "copy session id",
    onclick: () => { navigator.clipboard.writeText(meta.id); toast("session id copied"); } },
    meta.short_id + " \u29c9");

  // Collapse/expand toggle: frees vertical space for the transcript by hiding
  // the meta / find / action rows, leaving just the title. Persisted.
  const collapseBtn = el("button", { class: "head-collapse", id: "head-collapse",
    title: "Collapse / expand the session header",
    "aria-label": "Collapse or expand the session header",
    onclick: () => toggleHeaderCollapsed() });

  // Compact summary shown only while the header is collapsed, so a little
  // context (source + message count) survives the collapse.
  const miniMeta = el("span", { class: "t-mini-meta" },
    el("span", { class: "src" }, meta.source),
    el("span", {}, `${meta.message_count} msgs`));

  const head = el("div", { class: "t-head" },
    el("div", { class: "t-titlebar" },
      el("h1", { class: "t-title" }, meta.title || "(untitled)"),
      miniMeta,
      collapseBtn),
    el("div", { class: "t-meta" },
      el("span", { class: "src" }, meta.source),
      copyId,
      meta.model ? el("span", {}, "model: " + meta.model) : null,
      meta.git_branch ? el("span", {}, "branch: " + meta.git_branch) : null,
      meta.tokens_input != null ? el("span", { title: "input / output tokens" }, `tokens ${fmtTokens(meta.tokens_input)}/${fmtTokens(meta.tokens_output)}`) : null,
      meta.tokens_cache_read != null && (meta.tokens_cache_read || meta.tokens_cache_write)
        ? el("span", { title: "prompt cache read / write" }, `cache ${fmtTokens(meta.tokens_cache_read)}/${fmtTokens(meta.tokens_cache_write)}`) : null,
      el("span", {}, fmtDate(meta.created)),
      el("span", {}, `${meta.message_count} messages`),
      meta.directory ? el("span", {}, meta.directory) : null
    ),
    findBar(),
    actionBar(meta)
  );
  const body = el("div", { class: "t-body", id: "t-body" });
  body.addEventListener("scroll", () => {
    // Load more messages as the (frozen-header) message body nears its bottom.
    if (state.current && state.msg.hasMore && !state.msg.loading) {
      if (body.scrollTop + body.clientHeight >= body.scrollHeight - 400) loadMessages(false);
    }
    autoCollapseOnScroll(body.scrollTop);
  });
  t.replaceChildren(head, body);
  applyHeaderCollapsed();
}

// Header collapse: a manual preference (persisted, "1"/"0") always wins. When
// no manual preference is set we are in AUTO mode -- the header collapses once
// the transcript is scrolled down and expands again near the top.
function headerPref() {
  return localStorage.getItem("scrollback-head-collapsed");   // "1" | "0" | null
}
function isHeaderCollapsed() {
  const pref = headerPref();
  if (pref === "1") return true;
  if (pref === "0") return false;
  return state.headAutoCollapsed === true;   // auto mode
}
function applyHeaderCollapsed() {
  const t = $("#transcript");
  if (!t) return;
  const on = isHeaderCollapsed();
  t.classList.toggle("head-collapsed", on);
  const btn = $("#head-collapse");
  if (btn) {
    btn.textContent = on ? "\u25be" : "\u25b4";   // down (expand) / up (collapse)
    btn.setAttribute("aria-expanded", String(!on));
    btn.title = on ? "Expand the session header (h)" : "Collapse the session header (h)";
  }
}
function toggleHeaderCollapsed() {
  // A manual toggle pins the opposite of the current visible state.
  localStorage.setItem("scrollback-head-collapsed", isHeaderCollapsed() ? "0" : "1");
  applyHeaderCollapsed();
}
function autoCollapseOnScroll(scrollTop) {
  if (headerPref() !== null) return;   // manual preference set -> no auto behaviour
  const want = scrollTop > 120;
  if (want !== state.headAutoCollapsed) {
    state.headAutoCollapsed = want;
    applyHeaderCollapsed();
  }
}

function findBar() {
  const input = el("input", { id: "find-input", type: "search", placeholder: "find in transcript\u2026",
    autocomplete: "off", spellcheck: "false",
    oninput: debounce(() => runFind(input.value), 150),
    onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); stepFind(e.shiftKey ? -1 : 1); } } });
  return el("div", { class: "t-find" },
    input,
    el("span", { class: "find-count", id: "find-count" }, ""),
    el("button", { class: "btn", onclick: () => stepFind(-1) }, "\u2191"),
    el("button", { class: "btn", onclick: () => stepFind(1) }, "\u2193"));
}

// A checkbox-style toggle: a leading box (checked/unchecked) + a label, so
// it is obvious at a glance what is shown vs hidden.
function checkToggle(label, key) {
  const render = (btn) => {
    const on = state[key];
    btn.classList.toggle("on", on);
    btn.setAttribute("aria-pressed", String(on));
    btn.replaceChildren(
      el("span", { class: "chk", "aria-hidden": "true" }, on ? "\u2611" : "\u2610"),
      label,
    );
  };
  const btn = el("button", {
    class: "toggle", role: "checkbox",
    title: `Show or hide ${label}`,
    onclick: (e) => { state[key] = !state[key]; render(e.currentTarget); rerenderMessages(); },
  });
  render(btn);
  return btn;
}

// Which exports the math mode actually changes. Markdown / copy / JSON are
// always verbatim LaTeX source, so the mode is inert for them.
const MATH_AWARE_FMT = new Set(["html"]);
// Short suffix naming the active math mode, for buttons it affects.
const MATH_SUFFIX = { raw: "", latex: " \u00b7 LaTeX", rendered: " \u00b7 typeset" };

function actionBar(meta) {
  // -- VIEW zone: how the transcript is shown on screen ------------------
  const show = el("div", { class: "ctrl-grp" },
    el("span", { class: "ctrl-label" }, "show"),
    checkToggle("reasoning", "reasoning"),
    checkToggle("tool calls", "tools"));

  const mathSel = el("select", { class: "select", id: "math-select",
    title: "How LaTeX math is displayed on screen, and in print / HTML export",
    onchange: (e) => setMath(e.currentTarget.value) },
    el("option", { value: "raw" }, "source ($\u2026$)"),
    el("option", { value: "latex" }, "LaTeX (paste-ready)"),
    el("option", { value: "rendered" }, "typeset"));
  mathSel.value = state.math;
  const math = el("div", { class: "ctrl-grp" },
    el("label", { class: "ctrl-label", for: "math-select" }, "math"),
    mathSel);

  const view = el("div", { class: "bar-zone" },
    el("span", { class: "zone-label" }, "view"), show, math);

  // -- EXPORT zone: actions that produce a file / clipboard -------------
  // A button whose label reflects the math mode when math applies to it.
  const exp = (fmt, base) => {
    const aware = MATH_AWARE_FMT.has(fmt);
    const btn = el("button", {
      class: "btn" + (aware ? " math-aware" : ""),
      onclick: () => downloadExport(meta, fmt),
    });
    const paint = () => {
      btn.textContent = "\u2193 " + base + (aware ? MATH_SUFFIX[state.math] : "");
    };
    paint();
    if (aware) btn.addEventListener("scrollback:math", paint);
    return btn;
  };

  const printBtn = el("button", { class: "btn math-aware", onclick: () => printSession(meta) });
  const paintPrint = () => { printBtn.textContent = "\u2399 print" + MATH_SUFFIX[state.math]; };
  paintPrint();
  printBtn.addEventListener("scrollback:math", paintPrint);

  const exportRow = el("div", { class: "action-grp" },
    el("button", { class: "btn", title: "Copy as Markdown (LaTeX kept as source)",
      onclick: () => copySession(meta, "markdown") },
      "copy ", el("span", { class: "k" }, "md")),
    printBtn,
    exp("html", "html"), exp("markdown", "md"), exp("json", "json"));

  const exportZone = el("div", { class: "bar-zone" },
    el("span", { class: "zone-label" }, "export"), exportRow,
    el("span", { class: "zone-note", title:
      "Math display affects on-screen view, print, and HTML export. "
      + "Markdown, copy, and JSON always keep LaTeX as verbatim source." },
      "\u24d8 md / copy / json keep LaTeX source"));

  return el("div", { class: "t-actions" }, view, exportZone);
}

let loadedMessages = [];   // accumulates message objects as we page

async function loadMessages(reset, focusMessageId) {
  if (state.msg.loading) return;
  state.msg.loading = true;
  const body = $("#t-body");
  if (reset) { loadedMessages = []; body.replaceChildren(el("div", { class: "loading" }, "loading messages\u2026")); }
  try {
    const { source, id } = state.current;
    const p = new URLSearchParams({ offset: String(state.msg.offset), limit: String(MSG_PAGE) });
    const data = await getJSON(`/api/sessions/${enc(source)}/${enc(id)}/messages?` + p.toString());
    state.msg.hasMore = data.has_more;
    state.msg.offset += data.messages.length;
    loadedMessages.push(...data.messages);
    renderMessages(reset);
    if (focusMessageId) {
      const node = body.querySelector(`[data-mid="${CSS.escape(focusMessageId)}"]`);
      if (node) node.scrollIntoView({ block: "center" });
    }
  } catch (err) {
    if (reset) body.replaceChildren(el("div", { class: "loading" }, "error: " + err.message));
  } finally {
    state.msg.loading = false;
  }
}

function renderMessages(reset) {
  const body = $("#t-body");
  if (reset) body.replaceChildren();
  else body.querySelector(".load-more")?.remove();

  const start = body.querySelectorAll(".msg").length;
  for (let i = start; i < loadedMessages.length; i++) {
    const node = messageNode(loadedMessages[i]);
    if (node) body.append(node);
  }
  if (state.msg.hasMore) {
    body.append(el("button", { class: "load-more",
      onclick: () => loadMessages(false) },
      `load more (${state.msg.offset} of ${transcriptMeta.message_count} loaded)`));
  }
}

function rerenderMessages() {
  // Re-render already-loaded messages in place (toggle reasoning/tools).
  const body = $("#t-body");
  if (!body) return;
  body.querySelectorAll(".msg").forEach((n) => n.remove());
  const more = body.querySelector(".load-more");
  loadedMessages.forEach((m) => { const n = messageNode(m); if (n) more ? body.insertBefore(n, more) : body.append(n); });
  if (findState.term) runFind(findState.term);
}

function messageToText(m) {
  // Plain-text rendering of a single message, honouring current toggles.
  const lines = [];
  for (const p of m.parts) {
    if (p.type === "text" && p.text) lines.push(p.text);
    else if (p.type === "reasoning" && state.reasoning && p.text) lines.push("[reasoning] " + p.text);
    else if (p.type === "tool" && state.tools && p.text)
      lines.push(`[${p.tool_name || p.tool_status || "tool"}] ${p.text}`);
  }
  return lines.join("\n\n");
}

async function copyMessage(m, btn) {
  const text = messageToText(m);
  try {
    await navigator.clipboard.writeText(text);
    const prev = btn.textContent;
    btn.textContent = "\u2713";
    setTimeout(() => (btn.textContent = prev), 1100);
    toast(`copied message (${text.length} chars)`);
  } catch (err) { toast("copy failed: " + err.message); }
}

function messageNode(m) {
  const parts = [];
  for (const p of m.parts) {
    if (p.type === "text" && p.text) {
      const box = el("div", { class: "part text" });
      renderMarkdownInto(box, p.text);
      parts.push(box);
    } else if (p.type === "reasoning" && state.reasoning && p.text)
      parts.push(el("div", { class: "part reasoning" }, el("span", { class: "tag" }, "reasoning"), el("pre", {}, p.text)));
    else if (p.type === "tool" && state.tools && p.text) {
      const err = p.tool_status === "error";
      parts.push(el("div", { class: "part tool" + (err ? " is-error" : "") },
        el("span", { class: "tag" }, p.tool_name || p.tool_status || "tool"), el("pre", {}, p.text)));
    }
  }
  if (!parts.length) return null;
  const cls = m.role === "user" ? "user" : "assistant";
  const copyBtn = el("button", {
    class: "msg-copy", title: "copy this message",
    onclick: (e) => { e.stopPropagation(); copyMessage(m, e.currentTarget); },
  }, "\u29c9");
  return el("div", { class: "msg " + cls, dataset: { mid: m.id } },
    copyBtn,
    el("div", { class: "m-role" }, el("span", {}, m.role),
      m.created ? el("span", { class: "m-time" }, fmtDate(m.created)) : null),
    ...parts);
}

// (message-body scroll handler is attached per-render in renderHeader, since
//  the .t-body element is recreated for each opened session)

// ====================================================================
// in-transcript find
// ====================================================================

const findState = { term: "", hits: [], idx: -1 };

function clearFindMarks() {
  document.querySelectorAll("mark.find-hit").forEach((m) => {
    const parent = m.parentNode;
    parent.replaceChild(document.createTextNode(m.textContent), m);
    parent.normalize();
  });
}

function runFind(term) {
  clearFindMarks();
  findState.term = term;
  findState.hits = [];
  findState.idx = -1;
  const tl = term.trim().toLowerCase();
  if (!tl) { $("#find-count").textContent = ""; return; }
  const body = $("#t-body");
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  const targets = [];
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement.closest("mark")) continue;
    if (node.nodeValue.toLowerCase().includes(tl)) targets.push(node);
  }
  for (const text of targets) {
    const frag = document.createDocumentFragment();
    const val = text.nodeValue;
    const low = val.toLowerCase();
    let i = 0, pos = low.indexOf(tl);
    while (pos !== -1) {
      if (pos > i) frag.append(val.slice(i, pos));
      const mark = el("mark", { class: "find-hit" }, val.slice(pos, pos + tl.length));
      frag.append(mark);
      findState.hits.push(mark);
      i = pos + tl.length;
      pos = low.indexOf(tl, i);
    }
    if (i < val.length) frag.append(val.slice(i));
    text.parentNode.replaceChild(frag, text);
  }
  $("#find-count").textContent = findState.hits.length ? `${findState.hits.length} found` : "no matches";
  if (findState.hits.length) stepFind(1);
}

function stepFind(dir) {
  if (!findState.hits.length) return;
  if (findState.idx >= 0) findState.hits[findState.idx]?.classList.remove("current");
  findState.idx = (findState.idx + dir + findState.hits.length) % findState.hits.length;
  const cur = findState.hits[findState.idx];
  cur.classList.add("current");
  cur.scrollIntoView({ block: "center" });
  $("#find-count").textContent = `${findState.idx + 1} / ${findState.hits.length}`;
}

// ====================================================================
// export / copy
// ====================================================================

// True when running inside the native pywebview window (no browser download
// manager or working window.print()); the Python bridge handles those.
function nativeApi() {
  return (window.pywebview && window.pywebview.api) || null;
}

function exportUrl(meta, fmt, download) {
  const p = new URLSearchParams({
    format: fmt, reasoning: String(state.reasoning), tools: String(state.tools),
    math: state.math,
  });
  if (download) p.set("download", "true");
  return `/api/export/${enc(meta.source)}/${enc(meta.id)}?${p}`;
}

const _EXT = { markdown: "md", md: "md", json: "json", html: "html", text: "txt", txt: "txt" };

async function downloadExport(meta, fmt) {
  const ext = _EXT[fmt] || "txt";
  const fname = `${meta.source}_${meta.short_id}.${ext}`;
  let text;
  try {
    const r = await fetch(exportUrl(meta, fmt, false));
    if (!r.ok) throw new Error(`${r.status}`);
    text = await r.text();
  } catch (err) { toast("export failed: " + err.message); return; }

  const api = nativeApi();
  if (api) {
    // Native window: save via a real OS dialog through the Python bridge.
    try {
      const res = await api.save_file(fname, text);
      toast(res.startsWith("saved") ? `saved ${fname}` : res);
    } catch (err) { toast("save failed: " + err.message); }
    return;
  }
  // Browser: force a real download via a Blob URL + the download attribute
  // (reliable regardless of the response Content-Type).
  const blob = new Blob([text], { type: "application/octet-stream" });
  const objUrl = URL.createObjectURL(blob);
  const a = el("a", { href: objUrl, download: fname });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(objUrl), 2000);
}

async function copySession(meta, fmt) {
  try {
    const r = await fetch(exportUrl(meta, fmt, false));
    const text = await r.text();
    await navigator.clipboard.writeText(text);
    toast(`copied ${text.length} chars (${fmt})`);
  } catch (err) { toast("copy failed: " + err.message); }
}

async function printSession(meta) {
  const api = nativeApi();
  if (api) {
    // The native webview can't reliably open the OS print dialog; open a
    // dedicated print page in the user's real browser, which can print.
    const q = `/print/${enc(meta.source)}/${enc(meta.id)}?` +
      new URLSearchParams({ reasoning: String(state.reasoning), tools: String(state.tools), math: state.math });
    try {
      await api.open_external(q);
      toast("opened print view in your browser");
    } catch (err) { toast("print failed: " + err.message); }
    return;
  }

  // Browser: load the print-friendly HTML in a hidden iframe and print it.
  toast("preparing print\u2026");
  let html;
  try {
    const r = await fetch(exportUrl(meta, "html", false));
    html = await r.text();
  } catch (err) { toast("print failed: " + err.message); return; }

  const frame = el("iframe", { class: "print-frame", "aria-hidden": "true" });
  document.body.append(frame);
  const doc = frame.contentWindow.document;
  doc.open();
  doc.write(html);
  doc.close();
  const go = () => {
    frame.contentWindow.focus();
    frame.contentWindow.print();
    setTimeout(() => frame.remove(), 1000);
  };
  if (doc.readyState === "complete") setTimeout(go, 150);
  else frame.onload = () => setTimeout(go, 150);
}

// ====================================================================
// keyboard navigation
// ====================================================================

function rowList() {
  return [...document.querySelectorAll(".session, .s-child")].filter((n) => n.offsetParent !== null);
}
function moveSelection(dir) {
  const rows = rowList();
  if (!rows.length) return;
  let idx = rows.findIndex((r) => r.classList.contains("kbd-sel"));
  rows.forEach((r) => r.classList.remove("kbd-sel"));
  idx = Math.max(0, Math.min(rows.length - 1, (idx === -1 ? 0 : idx + dir)));
  const row = rows[idx];
  row.classList.add("kbd-sel");
  row.style.outline = "1px solid var(--focus)";
  setTimeout(() => (row.style.outline = ""), 600);
  row.scrollIntoView({ block: "nearest" });
}
function openSelection() {
  const row = document.querySelector(".session.kbd-sel, .s-child.kbd-sel") || rowList()[0];
  if (row) openSession(row.dataset.source, row.dataset.id);
}

document.addEventListener("keydown", (e) => {
  const typing = ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName);
  if (e.key === "/" && !typing) { e.preventDefault(); searchInput.focus(); searchInput.select(); return; }
  if (typing) {
    if (e.key === "Escape") document.activeElement.blur();
    return;
  }
  if (e.key === "j") { e.preventDefault(); moveSelection(1); }
  else if (e.key === "k") { e.preventDefault(); moveSelection(-1); }
  else if (e.key === "Enter") { e.preventDefault(); openSelection(); }
  else if (e.key === "f" && state.current) {
    e.preventDefault(); $("#find-input")?.focus();
  }
  else if (e.key === "h" && state.current) {
    e.preventDefault(); toggleHeaderCollapsed();
  }
});

// ====================================================================
// boot
// ====================================================================

function clearAll() {
  // Reset to the initial "home" state: no query, default scope, all sources
  // on, no date filters, no open transcript.
  searchInput.value = "";
  state.query = "";
  state.scope = { titles: true, contents: false };
  updateScopeButtons();

  state.enabled = new Set(state.sources.map((s) => s.name));
  document.querySelectorAll(".src-toggle").forEach((b) => {
    b.setAttribute("aria-pressed", "true");
    const c = b.querySelector(".check");
    if (c) c.textContent = "\u2713";
  });

  state.since = ""; state.until = "";
  const since = $("#since"), until = $("#until");
  if (since) since.value = "";
  if (until) until.value = "";

  // Close the open transcript.
  state.current = null;
  $("#transcript").hidden = true;
  $("#empty").hidden = false;
  history.replaceState(null, "", location.pathname);

  resetAndLoad();
  $("#rail").scrollTop = 0;
}

searchInput.addEventListener("input", onSearchInput);
scopeTitlesBtn.addEventListener("click", () => toggleScope("titles"));
scopeContentsBtn.addEventListener("click", () => toggleScope("contents"));
$("#home-btn").addEventListener("click", clearAll);
$("#brand").addEventListener("click", clearAll);
$("#brand").style.cursor = "pointer";
$("#theme-toggle").addEventListener("click", toggleTheme);
$("#since").addEventListener("change", (e) => { state.since = e.target.value; resetAndLoad(); });
$("#until").addEventListener("change", (e) => { state.until = e.target.value; resetAndLoad(); });

function openFromHash() {
  const h = decodeURIComponent(location.hash.replace(/^#/, ""));
  const slash = h.indexOf("/");
  if (slash > 0) openSession(h.slice(0, slash), h.slice(slash + 1));
}
function prefillSearchFromUrl() {
  const q = new URLSearchParams(location.search).get("q");
  if (q) {
    // A ?q= deep link implies a content search.
    searchInput.value = q;
    state.query = q;
    state.scope.contents = true;
    return true;
  }
  return false;
}

// Heartbeat: when the server is launched with auto-shutdown, ping it on an
// interval so it knows the window is still open. When the window/tab closes,
// pings stop and the server shuts itself down (freeing the port).
async function startHeartbeat() {
  let cfg;
  try {
    cfg = await getJSON("/api/heartbeat-config");
  } catch {
    return;
  }
  if (!cfg || !cfg.enabled) return;
  const ms = Math.max((cfg.interval || 3) * 1000, 1000);
  const ping = () => { fetch("/api/heartbeat", { method: "POST", keepalive: true }).catch(() => {}); };
  ping();
  setInterval(ping, ms);
}

(async function boot() {
  initTheme();
  initMath();
  try {
    await loadSources();
    prefillSearchFromUrl();
    updateScopeButtons();
    await loadListPage(true);
    if (location.hash) openFromHash();
    startHeartbeat();
  } catch (err) {
    sessionsEl.replaceChildren(el("li", { class: "loading" }, "failed to load: " + err.message));
  }
})();
