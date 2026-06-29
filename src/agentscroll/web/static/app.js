"use strict";

// ---- tiny helpers --------------------------------------------------------

const $ = (sel) => document.querySelector(sel);
const el = (tag, props = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k === "dataset") Object.assign(n.dataset, v);
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const kid of kids) {
    if (kid == null) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return n;
};

const fmtDate = (iso) => {
  if (!iso) return "?";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    year: "2-digit", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
};

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

// ---- state ---------------------------------------------------------------

const state = {
  sources: [],
  enabled: new Set(),     // enabled source names
  current: null,          // {source, id}
  reasoning: false,
  tools: true,
};

// ---- sources + filter chips ---------------------------------------------

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
        onclick: (e) => toggleSource(e.currentTarget, s.name),
      },
        el("span", { class: "dot", style: `background:${srcColor(s.name)}` }),
        s.label || s.name
      )
    )
  );
}

const srcColor = (name) =>
  name === "opencode" ? "var(--opencode)"
  : name === "claudecode" ? "var(--claudecode)"
  : "var(--focus)";

function toggleSource(btn, name) {
  if (state.enabled.has(name)) state.enabled.delete(name);
  else state.enabled.add(name);
  btn.setAttribute("aria-pressed", state.enabled.has(name) ? "true" : "false");
  runQuery();
}

// ---- query: title filter (cheap) vs content search (deep) ---------------

const searchInput = $("#search-input");
const searchModeKbd = $("#search-mode");
let mode = "title"; // "title" | "content"

function detectMode(q) {
  // Lead with a space to force deep content search; otherwise filter titles.
  return q.startsWith(" ") ? "content" : "title";
}

const runQuery = debounce(async () => {
  const raw = searchInput.value;
  const q = raw.trim();
  mode = detectMode(raw);
  searchModeKbd.textContent = mode;
  const sessions = $("#sessions");
  sessions.replaceChildren(el("li", { class: "loading" }, "loading\u2026"));

  try {
    if (mode === "content" && q) {
      await renderSearch(q);
    } else {
      await renderSessions(q);
    }
  } catch (err) {
    sessions.replaceChildren(el("li", { class: "loading" }, "error: " + err.message));
  }
}, 180);

function enabledList() {
  return [...state.enabled];
}

async function renderSessions(titleQuery) {
  const params = new URLSearchParams();
  if (titleQuery) params.set("q", titleQuery);
  params.set("limit", "500");
  let rows = await getJSON("/api/sessions?" + params.toString());
  rows = rows.filter((s) => state.enabled.has(s.source));
  $("#count").textContent = `${rows.length} session${rows.length === 1 ? "" : "s"}`;
  const list = $("#sessions");
  if (!rows.length) {
    list.replaceChildren(el("li", { class: "loading" }, "no sessions"));
    return;
  }
  list.replaceChildren(...rows.map(sessionRow));
}

function sessionRow(s) {
  const node = el("li", {
    class: "session" + (isCurrent(s) ? " active" : ""),
    style: `--src:${srcColor(s.source)}`,
    dataset: { source: s.source, id: s.id },
    onclick: () => openSession(s.source, s.id),
  },
    el("div", { class: "s-title" }, s.title || "(untitled)"),
    el("div", { class: "s-meta" },
      el("span", { class: "s-src" }, s.source),
      el("span", {}, fmtDate(s.updated)),
      s.message_count != null ? el("span", {}, `${s.message_count} msgs`) : null,
      s.directory ? el("span", { class: "s-dir", title: s.directory }, baseName(s.directory)) : null
    )
  );
  return node;
}

async function renderSearch(q) {
  const params = new URLSearchParams({ q, limit: "150" });
  let hits = await getJSON("/api/search?" + params.toString());
  hits = hits.filter((h) => state.enabled.has(h.source));
  $("#count").textContent = `${hits.length} match${hits.length === 1 ? "" : "es"}`;
  const list = $("#sessions");
  if (!hits.length) {
    list.replaceChildren(el("li", { class: "loading" }, "no matches"));
    return;
  }
  list.replaceChildren(...hits.map((h) => searchRow(h, q)));
}

function searchRow(h, q) {
  return el("li", {
    class: "session",
    style: `--src:${srcColor(h.source)}`,
    dataset: { source: h.source, id: h.session_id },
    onclick: () => openSession(h.source, h.session_id, h.message_id),
  },
    el("div", { class: "s-title" }, h.title || "(untitled)"),
    el("div", { class: "s-meta" },
      el("span", { class: "s-src" }, h.source),
      el("span", {}, `[${h.role}]`),
      h.tool_name ? el("span", {}, h.tool_name) : null
    ),
    snippetNode(h.snippet, q)
  );
}

function snippetNode(snippet, q) {
  const div = el("div", { class: "s-snippet" });
  const lc = snippet.toLowerCase();
  const ql = q.trim().toLowerCase();
  let i = 0, pos;
  if (ql && (pos = lc.indexOf(ql, i)) !== -1) {
    while (pos !== -1) {
      div.append(snippet.slice(i, pos));
      div.append(el("mark", {}, snippet.slice(pos, pos + ql.length)));
      i = pos + ql.length;
      pos = lc.indexOf(ql, i);
    }
    div.append(snippet.slice(i));
  } else {
    div.append(snippet);
  }
  return div;
}

const baseName = (p) => (p ? p.split("/").filter(Boolean).slice(-1)[0] || p : "");

function isCurrent(s) {
  return state.current && state.current.source === s.source && state.current.id === s.id;
}

// ---- transcript reader ---------------------------------------------------

async function openSession(source, id, focusMessageId) {
  state.current = { source, id };
  // Reflect the open session in the URL hash for deep-linking / reload.
  const hash = `#${source}/${id}`;
  if (location.hash !== hash) history.replaceState(null, "", hash);
  markActiveRow();
  const reader = $("#reader");
  $("#empty").hidden = true;
  const t = $("#transcript");
  t.hidden = false;
  t.replaceChildren(el("div", { class: "loading" }, "loading transcript\u2026"));
  reader.scrollTop = 0;

  let sess;
  try {
    sess = await getJSON(`/api/sessions/${encodeURIComponent(source)}/${encodeURIComponent(id)}`);
  } catch (err) {
    t.replaceChildren(el("div", { class: "loading" }, "error: " + err.message));
    return;
  }
  renderTranscript(sess);
  if (focusMessageId) {
    const node = t.querySelector(`[data-mid="${CSS.escape(focusMessageId)}"]`);
    if (node) node.scrollIntoView({ block: "center" });
  }
}

function markActiveRow() {
  document.querySelectorAll(".session.active").forEach((n) => n.classList.remove("active"));
  if (!state.current) return;
  const sel = `.session[data-source="${CSS.escape(state.current.source)}"][data-id="${CSS.escape(state.current.id)}"]`;
  const row = document.querySelector(sel);
  if (row) row.classList.add("active");
}

function renderTranscript(sess) {
  const t = $("#transcript");
  t.style.setProperty("--src", srcColor(sess.source));
  const head = el("div", { class: "t-head" },
    el("h1", { class: "t-title" }, sess.title || "(untitled)"),
    el("div", { class: "t-meta" },
      el("span", { class: "src" }, sess.source),
      el("span", {}, sess.short_id),
      sess.model ? el("span", {}, "model: " + sess.model) : null,
      sess.agent ? el("span", {}, "agent: " + sess.agent) : null,
      el("span", {}, fmtDate(sess.created)),
      el("span", {}, `${sess.messages.length} messages`),
      sess.directory ? el("span", {}, sess.directory) : null
    ),
    actionBar(sess)
  );
  const body = el("div", { class: "t-body" },
    ...sess.messages.map(messageNode).filter(Boolean));
  t.replaceChildren(head, body);
}

function actionBar(sess) {
  const exportBtn = (fmt, label) =>
    el("button", {
      class: "btn",
      onclick: () => downloadExport(sess, fmt),
    }, label);

  return el("div", { class: "t-actions" },
    el("button", { class: "btn", onclick: () => copySession(sess, "markdown") },
      "copy ", el("span", { class: "k" }, "md")),
    exportBtn("markdown", "\u2193 md"),
    exportBtn("html", "\u2193 html"),
    exportBtn("json", "\u2193 json"),
    el("div", { class: "toggle-group" },
      el("button", {
        class: "btn", "aria-pressed": String(state.reasoning),
        onclick: (e) => { state.reasoning = !state.reasoning; e.currentTarget.setAttribute("aria-pressed", String(state.reasoning)); renderTranscript(sess); },
      }, "reasoning"),
      el("button", {
        class: "btn", "aria-pressed": String(state.tools),
        onclick: (e) => { state.tools = !state.tools; e.currentTarget.setAttribute("aria-pressed", String(state.tools)); renderTranscript(sess); },
      }, "tools")
    )
  );
}

function messageNode(m) {
  const parts = [];
  for (const p of m.parts) {
    if (p.type === "text" && p.text) {
      parts.push(el("div", { class: "part text" }, el("pre", {}, p.text)));
    } else if (p.type === "reasoning" && state.reasoning && p.text) {
      parts.push(el("div", { class: "part reasoning" },
        el("span", { class: "tag" }, "reasoning"), el("pre", {}, p.text)));
    } else if (p.type === "tool" && state.tools && p.text) {
      const err = p.tool_status === "error";
      parts.push(el("div", { class: "part tool" + (err ? " is-error" : "") },
        el("span", { class: "tag" }, p.tool_name || p.tool_status || "tool"),
        el("pre", {}, p.text)));
    }
  }
  if (!parts.length) return null;
  const cls = m.role === "user" ? "user" : "assistant";
  return el("div", { class: "msg " + cls, dataset: { mid: m.id } },
    el("div", { class: "m-role" },
      el("span", {}, m.role),
      m.created ? el("span", { class: "m-time" }, fmtDate(m.created)) : null),
    ...parts);
}

// ---- export / copy -------------------------------------------------------

function exportUrl(sess, fmt, download) {
  const p = new URLSearchParams({
    format: fmt,
    reasoning: String(state.reasoning),
    tools: String(state.tools),
  });
  if (download) p.set("download", "true");
  return `/api/export/${encodeURIComponent(sess.source)}/${encodeURIComponent(sess.id)}?${p}`;
}

function downloadExport(sess, fmt) {
  const a = el("a", { href: exportUrl(sess, fmt, true) });
  document.body.append(a);
  a.click();
  a.remove();
}

async function copySession(sess, fmt) {
  try {
    const r = await fetch(exportUrl(sess, fmt, false));
    const text = await r.text();
    await navigator.clipboard.writeText(text);
    toast(`copied ${text.length} chars (${fmt})`);
  } catch (err) {
    toast("copy failed: " + err.message);
  }
}

// ---- keyboard ------------------------------------------------------------

document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== searchInput) {
    e.preventDefault();
    searchInput.focus();
    searchInput.select();
  } else if (e.key === "Escape" && document.activeElement === searchInput) {
    searchInput.blur();
  }
});

searchInput.addEventListener("input", runQuery);

// ---- boot ----------------------------------------------------------------

function openFromHash() {
  const h = decodeURIComponent(location.hash.replace(/^#/, ""));
  const slash = h.indexOf("/");
  if (slash > 0) {
    const source = h.slice(0, slash);
    const id = h.slice(slash + 1);
    if (source && id) openSession(source, id);
  }
}

function prefillSearchFromUrl() {
  const params = new URLSearchParams(location.search);
  const q = params.get("q");
  if (q) {
    // Lead with a space to engage deep content search mode.
    searchInput.value = " " + q;
    return true;
  }
  return false;
}

(async function boot() {
  try {
    await loadSources();
    const prefilled = prefillSearchFromUrl();
    await runQuery();
    if (location.hash) openFromHash();
  } catch (err) {
    $("#sessions").replaceChildren(el("li", { class: "loading" }, "failed to load: " + err.message));
  }
})();
