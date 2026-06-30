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

const fmtTokens = (n) => {
  if (n == null) return "";
  if (n < 1000) return String(n);
  if (n < 1e6) return (n / 1e3).toFixed(1) + "k";
  return (n / 1e6).toFixed(1) + "M";
};

const baseName = (p) => (p ? p.split("/").filter(Boolean).slice(-1)[0] || p : "");

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
    const dirty = marked.parse(text);
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
  } else {
    node.textContent = text;
  }
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

const srcColor = (name) =>
  name === "opencode" ? "var(--opencode)"
  : name === "claudecode" ? "var(--claudecode)"
  : "var(--focus)";
const srcSoft = (name) =>
  name === "opencode" ? "var(--opencode-soft)"
  : name === "claudecode" ? "var(--claudecode-soft)"
  : "var(--focus-soft)";

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
  const saved = localStorage.getItem("agentscroll-theme");
  const theme = saved || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  document.documentElement.dataset.theme = theme;
  applyHljsTheme(theme);
}
function toggleTheme() {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("agentscroll-theme", next);
  applyHljsTheme(next);
}

// ====================================================================
// sources + filter chips
// ====================================================================

async function loadSources() {
  state.sources = await getJSON("/api/sources");
  state.sources.forEach((s) => state.enabled.add(s.name));
  const wrap = $("#srcfilter");
  wrap.replaceChildren(
    ...state.sources.map((s) =>
      el("button", {
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
      )
    )
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
  if (!sessionsEl.children.length) sessionsEl.append(el("li", { class: "loading" }, "no sessions"));
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
    el("span", {}, fmtDate(s.updated)),
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

  const head = el("div", { class: "t-head" },
    el("h1", { class: "t-title" }, meta.title || "(untitled)"),
    el("div", { class: "t-meta" },
      el("span", { class: "src" }, meta.source),
      copyId,
      meta.model ? el("span", {}, "model: " + meta.model) : null,
      meta.git_branch ? el("span", {}, "branch: " + meta.git_branch) : null,
      meta.tokens_input != null ? el("span", {}, `tokens ${fmtTokens(meta.tokens_input)}/${fmtTokens(meta.tokens_output)}`) : null,
      el("span", {}, fmtDate(meta.created)),
      el("span", {}, `${meta.message_count} messages`),
      meta.directory ? el("span", {}, meta.directory) : null
    ),
    findBar(),
    actionBar(meta)
  );
  const body = el("div", { class: "t-body", id: "t-body" });
  t.replaceChildren(head, body);
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

function actionBar(meta) {
  const exp = (fmt, label) => el("button", { class: "btn", onclick: () => downloadExport(meta, fmt) }, label);
  return el("div", { class: "t-actions" },
    el("button", { class: "btn", onclick: () => copySession(meta, "markdown") },
      "copy ", el("span", { class: "k" }, "md")),
    el("button", { class: "btn", onclick: () => printSession(meta) }, "\u2399 print"),
    exp("markdown", "\u2193 md"), exp("html", "\u2193 html"), exp("json", "\u2193 json"),
    el("div", { class: "toggle-group" },
      el("button", { class: "btn", "aria-pressed": String(state.reasoning),
        onclick: (e) => { state.reasoning = !state.reasoning; e.currentTarget.setAttribute("aria-pressed", String(state.reasoning)); rerenderMessages(); } }, "reasoning"),
      el("button", { class: "btn", "aria-pressed": String(state.tools),
        onclick: (e) => { state.tools = !state.tools; e.currentTarget.setAttribute("aria-pressed", String(state.tools)); rerenderMessages(); } }, "tools")));
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

// auto-load more messages as the reader scrolls near the bottom
$("#reader").addEventListener("scroll", () => {
  const r = $("#reader");
  if (state.current && state.msg.hasMore && !state.msg.loading) {
    if (r.scrollTop + r.clientHeight >= r.scrollHeight - 400) loadMessages(false);
  }
});

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

function exportUrl(meta, fmt, download) {
  const p = new URLSearchParams({ format: fmt, reasoning: String(state.reasoning), tools: String(state.tools) });
  if (download) p.set("download", "true");
  return `/api/export/${enc(meta.source)}/${enc(meta.id)}?${p}`;
}
function downloadExport(meta, fmt) {
  const a = el("a", { href: exportUrl(meta, fmt, true) });
  document.body.append(a); a.click(); a.remove();
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
  // Render the full session as standalone HTML (server-side, print-friendly)
  // into a hidden iframe, then invoke the browser's print dialog. Using an
  // iframe (rather than window.open) avoids popup blockers.
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
  // Wait for the iframe document to finish laying out before printing.
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
