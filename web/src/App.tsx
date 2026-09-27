import { useEffect } from "react";
import { useHash } from "./lib/hash";
import Sidebar from "./components/Sidebar";
import Reader from "./components/Reader";
import { useAppState } from "./lib/state";

/**
 * Top-level layout. HashRouter is mounted in main.tsx so deep links work
 * before the SPA bundle is hydrated.
 *
 * Layout:
 *   ┌────────────┬──────────────────────────┐
 *   │            │  doc header (actions)    │
 *   │  Sidebar   ├──────────────────────────┤
 *   │            │  markdown / editor       │
 *   └────────────┴──────────────────────────┘
 *
 * URL convention (matches the legacy hash routing in app.js):
 *   #/<rootId>/<rel/path/to/file>
 *   #/                          ← no doc open
 *
 * Changing the hash opens the doc. Switching from one file to another
 * confirms a "discard unsaved changes?" prompt via the global beforeunload
 * hook (state.dirty).
 */
export default function App() {
  const hash = useHash();
  const { openDoc, state, closeDoc, setEditing } = useAppState();

  useEffect(() => {
    // #/<root>/<path…> → openDoc(root, path); "" or "#/" → closeDoc.
    // Each path segment is URL-encoded individually (gotoDoc), so decode
    // per-segment — decodeURIComponent on the whole string would break
    // filenames containing a literal "%2F".
    const m = hash.match(/^#\/([^/]+)\/(.+)$/);
    if (m) {
      const [, root, path] = m;
      const decoded = path
        .split("/")
        .map((seg) => decodeURIComponent(seg))
        .join("/");
      void openDoc(decodeURIComponent(root), decoded);
    } else {
      closeDoc();
    }
  }, [hash, openDoc, closeDoc]);

  // Block leaving while dirty
  useEffect(() => {
    function onBeforeUnload(e: BeforeUnloadEvent) {
      if (state.dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [state.dirty]);

  // Global keyboard shortcuts
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        // Save is owned by Editor; if we're not editing, do nothing.
        if (state.editing) setEditing(true); // no-op signal
      } else if ((e.ctrlKey || e.metaKey) && e.key === "e") {
        e.preventDefault();
        setEditing(!state.editing);
      } else if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "l") {
        e.preventDefault();
        const cur = document.documentElement.dataset.theme ?? "auto";
        const next = cur === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        try {
          localStorage.setItem("sqlHarnessTheme", next);
        } catch {
          /* ignore */
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state.editing, setEditing]);

  return (
    <div className="app">
      <Sidebar />
      <Reader />
    </div>
  );
}