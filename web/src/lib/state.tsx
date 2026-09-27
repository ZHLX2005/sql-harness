import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  type ReactNode,
} from "react";
import { api, type DocPayload, type TreePayload } from "./api";

/**
 * Doc state machine. A single useReducer holds everything the UI needs:
 *   - roots: /api/tree payload (cached on mount, refreshable)
 *   - current: the open doc; null when nothing is open
 *   - editing: whether the editor is visible
 *   - dirty: whether the editor has unsaved changes
 *   - readOnly: server-enforced (also surfaced as a UI gate)
 *
 * No Zustand, no Redux — useReducer + dispatch is enough for a doc library.
 */

export type State = {
  roots: TreePayload | null;
  current: DocPayload | null;
  editing: boolean;
  dirty: boolean;
  readOnly: boolean;
  loadError: string | null;
  /** Latest editor content (set by Editor on every change). Read by SaveButton. */
  draft: string;
};

const initial: State = {
  roots: null,
  current: null,
  editing: false,
  dirty: false,
  readOnly: false,
  loadError: null,
  draft: "",
};

type Action =
  | { type: "set_roots"; roots: TreePayload }
  | { type: "set_read_only"; readOnly: boolean }
  | { type: "open"; doc: DocPayload }
  | { type: "close" }
  | { type: "edit"; on: boolean }
  | { type: "dirty"; on: boolean }
  | { type: "draft"; value: string }
  | { type: "error"; message: string | null };

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "set_roots":
      return { ...state, roots: action.roots };
    case "set_read_only":
      return { ...state, readOnly: action.readOnly };
    case "open":
      return { ...state, current: action.doc, editing: false, dirty: false, loadError: null, draft: action.doc.raw };
    case "close":
      return { ...state, current: null, editing: false, dirty: false, draft: "" };
    case "edit":
      return { ...state, editing: action.on };
    case "dirty":
      return { ...state, dirty: action.on };
    case "draft":
      return { ...state, draft: action.value };
    case "error":
      return { ...state, loadError: action.message };
    default:
      return state;
  }
}

type Ctx = {
  state: State;
  refreshTree: () => Promise<void>;
  openDoc: (root: string, path: string) => Promise<void>;
  closeDoc: () => void;
  setEditing: (on: boolean) => void;
  setDirty: (on: boolean) => void;
  setDraft: (value: string) => void;
};

const StateCtx = createContext<Ctx | null>(null);

export function StateProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initial);

  // Boot: load /api/tree + /api/health once. Health tells us readOnly.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [tree, health] = await Promise.all([api.tree(), api.health()]);
        if (cancelled) return;
        dispatch({ type: "set_roots", roots: tree });
        dispatch({ type: "set_read_only", readOnly: health.read_only });
      } catch (err: any) {
        if (cancelled) return;
        dispatch({ type: "error", message: err?.message ?? "load failed" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const refreshTree = useCallback(async () => {
    const tree = await api.tree();
    dispatch({ type: "set_roots", roots: tree });
  }, []);

  const openDoc = useCallback(async (root: string, path: string) => {
    try {
      const doc = await api.doc(root, path);
      dispatch({ type: "open", doc });
    } catch (err: any) {
      dispatch({ type: "error", message: err?.message ?? "open failed" });
      throw err;
    }
  }, []);

  const closeDoc = useCallback(() => dispatch({ type: "close" }), []);
  const setEditing = useCallback((on: boolean) => dispatch({ type: "edit", on }), []);
  const setDirty = useCallback((on: boolean) => dispatch({ type: "dirty", on }), []);
  const setDraft = useCallback((value: string) => dispatch({ type: "draft", value }), []);

  const value = useMemo<Ctx>(
    () => ({ state, refreshTree, openDoc, closeDoc, setEditing, setDirty, setDraft }),
    [state, refreshTree, openDoc, closeDoc, setEditing, setDirty, setDraft],
  );

  return <StateCtx.Provider value={value}>{children}</StateCtx.Provider>;
}

export function useAppState(): Ctx {
  const ctx = useContext(StateCtx);
  if (!ctx) throw new Error("useAppState must be inside <StateProvider>");
  return ctx;
}