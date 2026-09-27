import { useEffect, useRef } from "react";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers, highlightActiveLine } from "@codemirror/view";
import { history, defaultKeymap, indentWithTab } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";
import { useAppState } from "../lib/state";

/**
 * Lazy-loaded CodeMirror 6 editor. Mounted by Reader when the user clicks
 * "编辑". The editor dispatches two state updates on every change:
 *   - `setDirty(true)` — the doc has unsaved edits
 *   - `setDraft(value)` — current content, read by SaveButton in the header
 *
 * Other write operations (rename / delete / create) live in DocContextMenu.
 */
export default function Editor() {
  const { state, setDirty, setDraft } = useAppState();
  const doc = state.current;
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);

  useEffect(() => {
    if (!hostRef.current || !doc) return;
    const lang = doc.kind === "py" ? python() : doc.kind === "md" ? markdown() : [];
    const startState = EditorState.create({
      doc: doc.raw,
      extensions: [
        lineNumbers(),
        highlightActiveLine(),
        history(),
        keymap.of([...defaultKeymap, indentWithTab]),
        lang,
        EditorView.lineWrapping,
        EditorView.updateListener.of((u) => {
          if (u.docChanged) {
            setDraft(u.state.doc.toString());
            setDirty(true);
          }
        }),
      ],
    });
    const view = new EditorView({ state: startState, parent: hostRef.current });
    viewRef.current = view;
    setDraft(doc.raw);
    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, [doc, setDirty, setDraft]);

  if (!doc) return null;
  return (
    <div className="editor-wrap">
      <div ref={hostRef} className="editor" />
    </div>
  );
}