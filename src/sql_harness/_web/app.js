/* sql-harness doc browser. Vanilla JS, hash routing, no build step.
 *
 * Backend contract (see webapp.py):
 *   GET    /api/health                        → {ok, version, read_only}
 *   GET    /api/tree                          → {version, roots[]}
 *   GET    /api/doc?root&path                 → doc payload (raw/html/frontmatter/sha256)
 *   PUT    /api/doc                           → overwrite (body: base_sha256)
 *   DELETE /api/doc?root&path                 → delete a file
 *   POST   /api/create                        → {root, path, kind, content?}
 *   POST   /api/rename                        → {root, src, path}
 *   GET    /api/config                        → parsed connections.toml
 *   GET    /api/search?q&content=0|1          → {matches[]}
 *
 * Writes are gated server-side (Host/Origin/PUT+JSON); the UI just reflects
 * what /api/health says, so a `--read-only` server hides the buttons too.
 */

const $ = (id) => document.getElementById(id);

const GROUP_LABELS = {
  config: "Configuration",
  source: "Shipped docs",
  runtime: "Runtime workspace",
};

const state = {
  roots: [],
  current: null,     // { root, path, sha256, raw, editable, kind, parsed? }
  editing: false,
  dirty: false,
  readOnly: false,
  deep: false,
  menuTarget: null,  // { root, path|null } the context menu was opened on
};

/* --- helpers ------------------------------------------------------------- */

function setStatus(msg, kind) {
  const el = $("status");
  if (!msg) { el.hidden = true; el.textContent = ""; return; }
  el.hidden = false;
  el.textContent = msg;
  el.className = "status" + (kind ? " " + kind : "");
}

async function api(path, options) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch (_) { /* non-JSON error page */ }
  if (!res.ok) {
    const err = new Error((body && body.error) || res.status + " " + res.statusText);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

const jsonOpts = (method, payload) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
});

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

const baseName = (p) => p.split("/").pop();

/* --- theme --------------------------------------------------------------- */

function applyTheme(theme) {
  if (theme) {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("sqlHarnessTheme", theme); } catch (_) {}
  } else {
    delete document.documentElement.dataset.theme;
    try { localStorage.removeItem("sqlHarnessTheme"); } catch (_) {}
  }
}

function toggleTheme() {
  const cur = document.documentElement.dataset.theme;
  if (!cur) {
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    applyTheme(dark ? "light" : "dark");
  } else {
    applyTheme(cur === "dark" ? "light" : "dark");
  }
}

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("sqlHarnessTheme"); } catch (_) {}
  if (saved) document.documentElement.dataset.theme = saved;
}

/* --- sidebar ------------------------------------------------------------- */

function renderNav() {
  const nav = $("nav");
  nav.textContent = "";

  const groups = {};
  for (const root of state.roots) (groups[root.kind] ||= []).push(root);

  // Config first, then shipped docs, then runtime zones.
  const order = ["config", "source", "runtime"];
  const kinds = Object.keys(groups).sort((a, b) => order.indexOf(a) - order.indexOf(b));

  for (const kind of kinds) {
    const title = el("div", "group-title", GROUP_LABELS[kind] || kind);
    nav.appendChild(title);

    for (const root of groups[kind]) {
      const head = el("div", "root-title");
      head.appendChild(el("span", null, root.label));
      if (root.connection) head.appendChild(el("span", "badge", root.connection));
      if (!root.editable) head.appendChild(el("span", "badge", "只读"));
      head.appendChild(el("span", "count", String(root.file_count)));
      head.appendChild(el("span", "count", root.singleton ? "文件" : ""));
      head.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        openMenu(e.clientX, e.clientY, root, null);
      });
      nav.appendChild(head);

      // A singleton root has exactly one entry — render it as a plain link so
      // connections.toml sits at the top level, not nested under a folder.
      nav.appendChild(renderNodes(root, root.tree, "", root.singleton ? 0 : 1));
    }
  }
  if (!nav.children.length) nav.appendChild(el("div", "empty", "没有文档"));
}

function renderNodes(root, nodes, prefix, depth) {
  const frag = document.createDocumentFragment();
  nodes.sort((a, b) => (a.type === "file") - (b.type === "file") || a.name.localeCompare(b.name));

  for (const node of nodes) {
    const path = prefix ? prefix + "/" + node.name : node.name;
    if (node.type === "file") {
      const a = document.createElement("a");
      a.href = "#/" + encodeURIComponent(root.id) + "/" + encodeURI(path.split("/").map(encodeURIComponent).join("/"));
      a.textContent = node.name;
      a.dataset.key = root.id + ":" + path;
      if (depth === 0) a.classList.add("flat");
      if (node.kind === "other") a.title = "不支持预览的文件类型";
      a.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        openMenu(e.clientX, e.clientY, root, path);
      });
      frag.appendChild(a);
    } else if (node.type === "dir") {
      const det = document.createElement("details");
      const sum = el("summary", null, node.name);
      sum.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        openMenu(e.clientX, e.clientY, root, path);
      });
      det.appendChild(sum);
      det.appendChild(renderNodes(root, node.children || [], path, depth + 1));
      frag.appendChild(det);
    }
  }
  return frag;
}

function markActive() {
  const key = state.current ? state.current.root + ":" + state.current.path : "";
  document.querySelectorAll("#nav a").forEach((a) => {
    const on = a.dataset.key === key;
    a.classList.toggle("active", on);
    if (on) {
      a.closest("details")?.setAttribute("open", "");
      a.scrollIntoView({ block: "nearest" });
    }
  });
}

/* --- search -------------------------------------------------------------- */

function showResults(matches, q) {
  const box = $("results");
  box.textContent = "";
  if (!matches.length) {
    box.appendChild(el("div", "none", "没有匹配 " + q));
    box.hidden = false;
    return;
  }
  for (const m of matches) {
    const btn = el("button", "hit");
    btn.type = "button";
    btn.appendChild(el("div", "where", m.root_label + m.path));
    btn.appendChild(el("div", "name", m.name));
    if (m.field === "content") btn.appendChild(el("div", "snippet", m.snippet));
    btn.addEventListener("click", () => {
      location.hash = "#/" + encodeURIComponent(m.root) + "/" +
        encodeURI(m.path.split("/").map(encodeURIComponent).join("/"));
      hideResults();
    });
    box.appendChild(btn);
  }
  box.hidden = false;
}

function hideResults() { $("results").hidden = true; $("results").textContent = ""; }

let searchTimer = null;
function onFilterInput() {
  const q = $("filter").value.trim().toLowerCase();
  if (!q) { hideResults(); return; }

  // Filename filtering in the tree is instant; a content search hits the API.
  document.querySelectorAll("#nav a").forEach((a) => {
    a.style.display = !q || a.textContent.toLowerCase().includes(q) ? "" : "none";
  });
  const anyVisible = [...document.querySelectorAll("#nav a")].some((a) => a.style.display !== "none");
  if (anyVisible) { hideResults(); return; }

  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    try {
      const res = await api("/api/search?q=" + encodeURIComponent(q) + "&content=" + (state.deep ? "1" : "0"));
      showResults(res.matches, q);
    } catch (err) {
      setStatus("搜索失败：" + err.message, "err");
    }
  }, 180);
}

/* --- context menu -------------------------------------------------------- */

function openMenu(x, y, root, path) {
  state.menuTarget = { root, path };
  const menu = $("menu");
  menu.textContent = "";

  const canWrite = root.editable && !state.readOnly;
  const isFile = path && !isDirTarget(root, path);

  if (canWrite && !root.singleton) {
    menu.appendChild(el("div", "menu-label", root.connection || root.label));
    const dir = isFile ? path.split("/").slice(0, -1).join("/") : (path || "");
    for (const kind of ["md", "py"]) {
      const b = el("button", null, "新建 ." + kind);
      b.type = "button";
      b.addEventListener("click", () => { closeMenu(); createDoc(root, dir, kind); });
      menu.appendChild(b);
    }
    if (isFile) {
      menu.appendChild(document.createElement("hr"));
      const r = el("button", null, "重命名…");
      r.type = "button";
      r.addEventListener("click", () => { closeMenu(); renameDoc(root, path); });
      menu.appendChild(r);
      const d = el("button", "danger", "删除");
      d.type = "button";
      d.addEventListener("click", () => { closeMenu(); deleteDoc(root, path); });
      menu.appendChild(d);
    }
  } else {
    menu.appendChild(el("div", "menu-label", state.readOnly ? "只读模式" : "不可写"));
  }

  menu.hidden = false;
  const rect = menu.getBoundingClientRect();
  menu.style.left = Math.min(x, innerWidth - rect.width - 8) + "px";
  menu.style.top = Math.min(y, innerHeight - rect.height - 8) + "px";
}

function isDirTarget(root, path) {
  const node = findNode(root, path);
  return node ? node.type !== "file" : false;
}

function findNode(root, path) {
  const parts = path.split("/");
  let nodes = root.tree || [];
  let node = null;
  for (const part of parts) {
    node = nodes.find((n) => n.name === part);
    if (!node) return null;
    nodes = node.children || [];
  }
  return node;
}

function closeMenu() { $("menu").hidden = true; state.menuTarget = null; }

/* --- CRUD ---------------------------------------------------------------- */

async function createDoc(root, dir, kind) {
  const entered = prompt("新建 ." + kind + " 文件名（可含子路径）：", "");
  if (!entered) return;
  let rel = entered.trim().replace(/^\/+/, "");
  if (!rel) return;
  const parent = dir ? dir.replace(/\/+$/, "") + "/" : "";
  const path = parent + rel;

  try {
    const res = await api("/api/create", jsonOpts("POST", { root: root.id, path, kind }));
    await refreshTree();
    setStatus("已创建 " + res.path, "ok");
    location.hash = "#/" + encodeURIComponent(root.id) + "/" +
      encodeURI(res.path.split("/").map(encodeURIComponent).join("/"));
  } catch (err) {
    setStatus("创建失败：" + err.message, "err");
  }
}

async function renameDoc(root, path) {
  const entered = prompt("重命名为：", baseName(path));
  if (!entered || entered === baseName(path)) return;
  const next = entered.trim().replace(/^\/+/, "");
  if (!next) return;
  try {
    const res = await api("/api/rename", jsonOpts("POST", { root: root.id, src: path, path: next }));
    await refreshTree();
    setStatus("已重命名为 " + res.path, "ok");
    if (state.current && state.current.root === root.id && state.current.path === path) {
      location.hash = "#/" + encodeURIComponent(root.id) + "/" +
        encodeURI(res.path.split("/").map(encodeURIComponent).join("/"));
    }
  } catch (err) {
    setStatus("重命名失败：" + err.message, "err");
  }
}

async function deleteDoc(root, path) {
  if (!confirm("确定删除 " + path + "？此操作不可撤销。")) return;
  try {
    await api("/api/doc?root=" + encodeURIComponent(root.id) + "&path=" + encodeURIComponent(path),
      { method: "DELETE" });
    await refreshTree();
    setStatus("已删除 " + path, "ok");
    if (state.current && state.current.root === root.id && state.current.path === path) {
      state.current = null;
      state.dirty = false;
      $("doc").textContent = "";
      $("crumb").textContent = "已删除 " + path;
      setEditing(false);
    }
  } catch (err) {
    setStatus("删除失败：" + err.message, "err");
  }
}

/* --- document ------------------------------------------------------------ */

/** The structured connections panel only applies to the config root; a stray
 *  `.toml` in a doc tree is just text. */
function isConfigDoc(doc) {
  return doc.kind === "toml" && doc.root === "config";
}

function setEditing(on) {
  const configDoc = !!(state.current && isConfigDoc(state.current));
  state.editing = on;
  $("editor").hidden = !on;
  $("doc").hidden = on || configDoc;
  $("conn").hidden = on || !configDoc;
  $("meta").hidden = on || $("meta").dataset.empty === "1";
  $("btn-edit").hidden = on || !(state.current && state.current.editable);
  $("btn-save").hidden = !on;
  $("btn-cancel").hidden = !on;
  $("btn-save").disabled = !state.dirty;
  syncFileActions();
  if (on) $("editor").focus();
}

/** Rename/delete only make sense for a real file in a writable, non-singleton
 *  root — and never while the editor is open. */
function syncFileActions() {
  const cur = state.current;
  const root = cur && state.roots.find((r) => r.id === cur.root);
  const can = !!(cur && root && root.editable && !root.singleton && !state.readOnly);
  $("btn-rename").hidden = !can || state.editing;
  $("btn-delete").hidden = !can || state.editing;
}

function renderMeta(meta) {
  const el2 = $("meta");
  const keys = Object.keys(meta || {}).filter((k) => k !== "_raw");
  el2.textContent = "";
  if (!keys.length) {
    el2.hidden = true;
    el2.dataset.empty = "1";
    return;
  }
  const dl = document.createElement("dl");
  for (const key of keys) {
    dl.appendChild(el("dt", null, key));
    dl.appendChild(el("dd", null, meta[key]));
  }
  el2.appendChild(dl);
  el2.dataset.empty = "0";
  el2.hidden = state.editing;
}

/* Structured view of connections.toml. Never renders unsanitised server
   strings as HTML — everything goes through textContent. */
function renderConfig(doc) {
  const box = $("conn");
  box.textContent = "";
  const parsed = doc.parsed || {};

  if (parsed.ok === false) {
    box.appendChild(el("div", "banner err", "解析失败：" + parsed.error));
    box.appendChild(el("div", "raw-note", "切到「编辑」可以修复原始 TOML；保存前会校验。"));
    return;
  }

  const s = parsed.summary;
  if (!s) { box.appendChild(el("div", "banner", "尚无解析结果。")); return; }

  const def = el("p", "summary-line");
  def.appendChild(document.createTextNode("default_workspace = "));
  def.appendChild(el("code", null, s.default_workspace || "(未设置)"));
  box.appendChild(def);

  const pd = s.pool_defaults;
  const pdLine = el("p", "summary-line");
  pdLine.appendChild(document.createTextNode("pool_defaults: "));
  pdLine.appendChild(el("code", null, `size=${pd.size} recycle=${pd.recycle} pre_ping=${pd.pre_ping} echo=${pd.echo}`));
  box.appendChild(pdLine);

  box.appendChild(el("h2", null, `连接（${s.connections.length}）`));

  for (const c of s.connections) {
    const card = el("div", "card");
    const head = el("div", "card-head");
    head.appendChild(el("span", "name", c.name));
    head.appendChild(el("span", "driver", c.driver));
    if (c.name === s.default_workspace) head.appendChild(el("span", "pill def", "默认"));
    head.appendChild(el("span", "spacer"));
    head.appendChild(el("span", "pill " + (c.read_only ? "ro" : "rw"), c.read_only ? "只读" : "可写"));
    card.appendChild(head);

    const body = el("div", "card-body");
    const dl = document.createElement("dl");
    const row = (k, v) => { dl.appendChild(el("dt", null, k)); dl.appendChild(el("dd", null, v)); };
    row("url", c.url);
    if (c.description) row("description", c.description);
    row("application_name", c.application_name);
    row("password", c.password_set ? "已设置（在 URL 或 password 字段）" : "未设置");
    row("pool", `size=${c.pool.size} recycle=${c.pool.recycle} pre_ping=${c.pool.pre_ping} echo=${c.pool.echo}`);
    body.appendChild(dl);
    card.appendChild(body);
    box.appendChild(card);
  }

  box.appendChild(el("div", "raw-note",
    "密码已打码显示。点「编辑」可直接修改原文 TOML，保存前后端会做一次解析校验。"));
}

async function openDoc(rootId, path) {
  if (state.dirty && !confirm("有未保存的修改，确定要离开吗？")) return;
  setStatus("载入中…");
  try {
    const doc = await api("/api/doc?root=" + encodeURIComponent(rootId) + "&path=" + encodeURIComponent(path));
    state.current = doc;
    state.dirty = false;

    $("crumb").textContent = doc.root + " / " + doc.path + "   ·   " + doc.size + " B";
    $("editor").value = doc.raw;

    if (isConfigDoc(doc)) {
      renderConfig(doc);
    } else if (doc.html === null) {
      $("doc").innerHTML = "";
      $("doc").appendChild(el("p", null, "该文件类型不支持预览（" + doc.kind + "）。"));
    } else {
      $("doc").innerHTML = doc.html;
    }
    renderMeta(doc.frontmatter);
    setEditing(false);
    markActive();
    closeMenu();
    setStatus("");
  } catch (err) {
    setStatus("打开失败：" + err.message, "err");
  }
}

async function save() {
  if (!state.current) return;
  const content = $("editor").value;
  $("btn-save").disabled = true;
  setStatus("保存中…");
  try {
    const res = await api("/api/doc", jsonOpts("PUT", {
      root: state.current.root,
      path: state.current.path,
      content,
      base_sha256: state.current.sha256,
    }));
    state.dirty = false;
    setStatus("已保存 " + res.size + " B", "ok");
    await refreshTree();
    await openDoc(state.current.root, state.current.path);
  } catch (err) {
    $("btn-save").disabled = false;
    if (err.status === 409) {
      const overwrite = confirm(
        "这个文件在磁盘上已经被改过了（可能是 agent 写的）。\n\n" +
        "确定 = 用我编辑器里的内容覆盖它\n取消 = 丢弃我的修改，重新载入磁盘版本"
      );
      if (overwrite) {
        state.current.sha256 = err.body.sha256;
        return save();
      }
      await refreshTree();
      await openDoc(state.current.root, state.current.path);
      return;
    }
    setStatus("保存失败：" + err.message, "err");
  }
}

/* --- shell --------------------------------------------------------------- */

async function refreshTree() {
  const data = await api("/api/tree");
  state.roots = data.roots;
  $("version").textContent = "v" + data.version;
  renderNav();
  markActive();
}

function applyHash() {
  const hash = decodeURIComponent(location.hash.replace(/^#\/?/, ""));
  if (!hash) return;
  const idx = hash.indexOf("/");
  if (idx < 0) return;
  const rootId = hash.slice(0, idx);
  const path = hash.slice(idx + 1).split("/").map(decodeURIComponent).join("/");
  openDoc(rootId, path);
}

function openHelp(on) { $("help").hidden = !on; }

function boot() {
  initTheme();

  $("btn-refresh").addEventListener("click", async () => {
    await refreshTree();
    setStatus("文件树已刷新", "ok");
  });
  $("btn-new").addEventListener("click", () => {
    const root = state.current
      ? state.roots.find((r) => r.id === state.current.root)
      : state.roots.find((r) => r.editable && !r.singleton);
    if (!root || !root.editable || root.singleton) {
      setStatus("当前没有可写入的目录", "err");
      return;
    }
    const dir = state.current && state.current.path.includes("/")
      ? state.current.path.split("/").slice(0, -1).join("/")
      : "";
    createDoc(root, dir, "md");
  });
  $("btn-rename").addEventListener("click", () => {
    const root = state.roots.find((r) => r.id === state.current?.root);
    if (root && state.current) renameDoc(root, state.current.path);
  });
  $("btn-delete").addEventListener("click", () => {
    const root = state.roots.find((r) => r.id === state.current?.root);
    if (root && state.current) deleteDoc(root, state.current.path);
  });
  $("btn-edit").addEventListener("click", () => setEditing(true));
  $("btn-cancel").addEventListener("click", () => {
    if (state.dirty && !confirm("放弃未保存的修改？")) return;
    state.dirty = false;
    $("editor").value = state.current ? state.current.raw : "";
    setEditing(false);
    setStatus("");
  });
  $("btn-save").addEventListener("click", save);
  $("btn-theme").addEventListener("click", toggleTheme);
  $("btn-help").addEventListener("click", () => openHelp(true));
  $("btn-help-close").addEventListener("click", () => openHelp(false));
  $("help").addEventListener("click", (e) => { if (e.target === $("help")) openHelp(false); });

  $("editor").addEventListener("input", () => {
    state.dirty = $("editor").value !== (state.current ? state.current.raw : "");
    $("btn-save").disabled = !state.dirty;
  });
  $("filter").addEventListener("input", onFilterInput);
  $("filter").addEventListener("keydown", (e) => { if (e.key === "Escape") { $("filter").value = ""; onFilterInput(); } });
  $("btn-deep").addEventListener("click", () => {
    state.deep = !state.deep;
    $("btn-deep").classList.toggle("on", state.deep);
    if ($("filter").value.trim()) { $("filter").dispatchEvent(new Event("input")); }
  });

  document.addEventListener("click", (e) => { if (!e.target.closest("#menu")) closeMenu(); });
  document.addEventListener("scroll", closeMenu, true);
  window.addEventListener("resize", closeMenu);
  window.addEventListener("hashchange", applyHash);
  window.addEventListener("beforeunload", (e) => {
    if (state.dirty) { e.preventDefault(); e.returnValue = ""; }
  });
  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    const key = e.key.toLowerCase();
    if (mod && key === "s") {
      e.preventDefault();
      if (state.editing && state.dirty) save();
    } else if (mod && key === "e") {
      e.preventDefault();
      if (state.current && state.current.editable) setEditing(!state.editing);
    } else if (mod && key === "n") {
      e.preventDefault();
      $("btn-new").click();
    } else if (mod && e.shiftKey && key === "l") {
      e.preventDefault();
      toggleTheme();
    } else if (e.key === "F2" && state.current) {
      e.preventDefault();
      $("btn-rename").click();
    } else if (e.key === "Escape") {
      if (!$("help").hidden) openHelp(false);
      else if (state.editing) $("btn-cancel").click();
      else closeMenu();
    }
  });

  api("/api/health").then((h) => {
    state.readOnly = !!h.read_only;
    $("ro-label").textContent = h.read_only ? "只读模式" : "可编辑";
    if (h.read_only) $("ro-dot").classList.add("ro");
    // Reflect server-side write permission in the chrome.
    $("btn-new").hidden = h.read_only;
    syncFileActions();
  });

  refreshTree()
    .then(() => {
      if (location.hash) applyHash();
      else if (state.roots.length) {
        // Open something real rather than an empty pane — prefer the config
        // file, since that's the thing users come here to look at.
        const config = state.roots.find((r) => r.id === "config" && r.file_count > 0);
        const first = state.roots.find((r) => r.file_count > 0);
        const pick = firstFile(config) ? config : first;
        const f = firstFile(pick);
        if (pick && f) location.hash = "#/" + encodeURIComponent(pick.id) + "/" + f;
      }
    })
    .catch((err) => setStatus("初始化失败：" + err.message, "err"));
}

function firstFile(root) {
  if (!root) return null;
  if (root.singleton) return root.tree?.[0]?.name || null;
  const walk = (nodes, prefix) => {
    for (const n of nodes) {
      const path = prefix ? prefix + "/" + n.name : n.name;
      if (n.type === "file") return path;
      const deeper = n.children && walk(n.children, path);
      if (deeper) return deeper;
    }
    return null;
  };
  return walk(root.tree || [], "");
}

document.addEventListener("DOMContentLoaded", boot);
