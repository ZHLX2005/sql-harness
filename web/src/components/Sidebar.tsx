import { useMemo, useState } from "react";
import { api } from "../lib/api";
import { useAppState } from "../lib/state";
import TreeNode from "./TreeNode";
import HelpDialog from "./HelpDialog";
import logoUrl from "../assets/logo.png";

/** Navigate by hash. App.tsx listens on hashchange and opens the doc. */
export function gotoDoc(root: string, path: string) {
  const enc = path
    .split("/")
    .map(encodeURIComponent)
    .join("/");
  window.location.hash = `#/${encodeURIComponent(root)}/${enc}`;
}

/**
 * Left rail: brand → search → tree → footer (theme + help + read-only chip).
 * Tree is rendered recursively from /api/tree. Search overlays filtered hits.
 *
 * The search input filters filenames client-side against the cached tree
 * (instant), and a separate "search content" toggle calls /api/search?q=…
 * for full-text matches across all docs.
 */
export default function Sidebar() {
  const { state, openDoc } = useAppState();
  const [filter, setFilter] = useState("");
  const [results, setResults] = useState<Array<{ root: string; path: string; name: string }>>([]);
  const [searching, setSearching] = useState(false);

  const filteredRoots = useMemo(() => {
    if (!filter.trim() || !state.roots) return state.roots?.roots ?? [];
    const q = filter.toLowerCase();
    return state.roots.roots
      .map((r) => ({ ...r, tree: filterTree(r.tree, q) }))
      .filter((r) => r.tree.length > 0);
  }, [filter, state.roots]);

  async function onSearchSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!filter.trim()) {
      setResults([]);
      return;
    }
    setSearching(true);
    try {
      const r = await api.search(filter, false);
      setResults(r.matches.map((m) => ({ root: m.root, path: m.path, name: m.name })));
    } finally {
      setSearching(false);
    }
  }

  return (
    <aside className="sidebar">
      <header className="brand">
        <img src={logoUrl} alt="" className="logo" width={28} height={24} />
        <span>sql-harness</span>
        {state.readOnly ? (
          <span className="ro-chip" title="server is read-only">
            RO
          </span>
        ) : null}
      </header>

      <form className="search" onSubmit={onSearchSubmit}>
        <input
          type="search"
          placeholder="搜索文件名…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="filter"
        />
      </form>

      {results.length > 0 ? (
        <ul className="results" aria-label="search results">
          {results.map((r) => (
            <li key={`${r.root}::${r.path}`}>
              <button
                type="button"
                onClick={() => {
                  setResults([]);
                  setFilter("");
                  gotoDoc(r.root, r.path);
                  void openDoc(r.root, r.path);
                }}
              >
                <span className="where">
                  {r.root} / {r.path}
                </span>
                <span className="name">{r.name}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : searching ? (
        <p className="hint">搜索中…</p>
      ) : null}

      <nav id="nav" className="tree" aria-label="docs">
        {state.roots === null ? (
          <p className="hint">加载中…</p>
        ) : state.loadError ? (
          <p className="err">加载失败:{state.loadError}</p>
        ) : (
          filteredRoots.map((root) => (
            <section key={root.id} className="root-group">
              <header className="root-title">
                <span>{root.label}</span>
                {root.connection ? <span className="badge">{root.connection}</span> : null}
                {!root.editable ? <span className="badge">只读</span> : null}
                <span className="count">{String(root.file_count)}</span>
              </header>
              {root.tree.map((child) => (
                <TreeNode
                  key={child.path || child.name}
                  node={child}
                  rootId={root.id}
                  relPath={child.name}
                  depth={0}
                  onPick={(path) => {
                    gotoDoc(root.id, path);
                    void openDoc(root.id, path);
                  }}
                />
              ))}
            </section>
          ))
        )}
      </nav>

      <footer className="sidebar-foot">
        <ThemeButton />
        <HelpDialog />
      </footer>
    </aside>
  );
}

function filterTree(nodes: any[], q: string): any[] {
  const out: any[] = [];
  for (const n of nodes) {
    if (n.name.toLowerCase().includes(q)) {
      out.push(n);
    } else if (n.children) {
      const sub = filterTree(n.children, q);
      if (sub.length > 0) out.push({ ...n, children: sub });
    }
  }
  return out;
}

function ThemeButton() {
  const onClick = () => {
    const cur = document.documentElement.dataset.theme ?? "auto";
    const next = cur === "dark" ? "light" : cur === "light" ? "auto" : "dark";
    if (next === "auto") {
      document.documentElement.removeAttribute("data-theme");
    } else {
      document.documentElement.dataset.theme = next;
    }
    try {
      localStorage.setItem("sqlHarnessTheme", next);
    } catch {
      // localStorage may be blocked (private mode); the in-memory toggle still works.
    }
  };
  return (
    <button type="button" className="icon" onClick={onClick} title="切换主题 (Ctrl+Shift+L)">
      ◐
    </button>
  );
}