import { lazy, Suspense } from "react";
import { useAppState } from "../lib/state";
import MarkdownView from "./MarkdownView";
import ConnectionsPanel from "./ConnectionsPanel";
import SaveButton from "./SaveButton";

const Editor = lazy(() => import("./Editor"));

/**
 * The right-hand pane. Shows one of three views depending on the loaded doc:
 *   1. editing === true → lazy-loaded Editor (CodeMirror 6)
 *   2. doc.kind === "toml" AND doc.parsed set → ConnectionsPanel
 *   3. otherwise → MarkdownView (md) or a "no preview" placeholder (py/toml raw)
 *
 * The header has the breadcrumb, edit/cancel buttons, and a "save" button
 * (delegates to Editor, which owns the save flow).
 */
export default function Reader() {
  const { state, setEditing } = useAppState();
  const doc = state.current;
  if (!doc) {
    return (
      <main className="reader empty">
        <p className="hint">从左侧选一个文档开始。</p>
      </main>
    );
  }

  const editable = doc.editable && !state.readOnly;
  const isConnDoc = doc.kind === "toml" && doc.parsed;

  return (
    <main className="reader" data-current-root={doc.root}>
      <header className="doc-head">
        <span className="crumb">
          {doc.root} / {doc.path} · {doc.size} B
        </span>
        <div className="actions">
          {state.editing ? (
            <>
              <button type="button" onClick={() => setEditing(false)}>
                取消
              </button>
              <SaveButton />
            </>
          ) : (
            <button
              type="button"
              className="primary"
              disabled={!editable}
              onClick={() => setEditing(true)}
              title={editable ? "进入编辑" : state.readOnly ? "服务器只读" : "不可编辑"}
            >
              编辑
            </button>
          )}
        </div>
      </header>

      {doc.frontmatter && Object.keys(doc.frontmatter).length > 0 ? (
        <section className="frontmatter">
          {Object.entries(doc.frontmatter).map(([k, v]) => (
            <span key={k} className="kv">
              <em>{k}</em>
              <span>{v}</span>
            </span>
          ))}
        </section>
      ) : null}

      {state.editing ? (
        <Suspense fallback={<p className="hint">编辑器加载中…</p>}>
          <Editor />
        </Suspense>
      ) : isConnDoc ? (
        <ConnectionsPanel summary={doc.parsed!} />
      ) : doc.kind === "md" ? (
        <MarkdownView body={doc.raw} />
      ) : (
        <pre className="raw">{doc.raw}</pre>
      )}
    </main>
  );
}