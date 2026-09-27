/**
 * fetch wrapper around the 11 sql-harness /api/* routes.
 *
 * - Methods map 1:1 to webapp.py: GET /api/{health,tree,doc,config,search}
 * - Writes: PUT /api/doc, POST /api/{create,rename}, DELETE /api/doc
 *
 * Error contract (uniform across every endpoint — webapp._fail):
 *   { error: str, code: "forbidden"|"not_found"|"conflict"|"too_large"|"unsupported_media_type"|"internal",
 *     sha256?: str, raw?: str  // only on 409 Conflict }
 *
 * On non-2xx responses we throw ApiError(status, body) so callers can branch
 * on `.status === 409` for the stale-sha path without parsing JSON themselves.
 */

export class ApiError extends Error {
  status: number;
  body: any;
  constructor(status: number, body: any, message: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function request<T = any>(
  path: string,
  init?: RequestInit & { json?: unknown },
): Promise<T> {
  const headers = new Headers(init?.headers);
  let body: BodyInit | undefined;
  if (init?.body !== undefined && init.body !== null) body = init.body as BodyInit;
  if (init?.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const res = await fetch(path, { ...init, headers, body });
  const text = await res.text();
  let parsed: any = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      // non-JSON body — leave parsed null
    }
  }
  if (!res.ok) {
    throw new ApiError(
      res.status,
      parsed,
      parsed?.error ?? `${res.status} ${res.statusText}`,
    );
  }
  return parsed as T;
}

export const api = {
  // --- reads ---
  health: () => request<Health>("/api/health"),
  tree: () => request<TreePayload>("/api/tree"),
  doc: (root: string, path: string) =>
    request<DocPayload>(
      `/api/doc?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`,
    ),
  config: () => request<ConfigPayload>("/api/config"),
  search: (q: string, content = false) =>
    request<SearchPayload>(
      `/api/search?q=${encodeURIComponent(q)}&content=${content ? "1" : "0"}`,
    ),

  // --- writes ---
  putDoc: (body: { root: string; path: string; content: string; base_sha256: string }) =>
    request<PutDocResult>("/api/doc", { method: "PUT", json: body }),
  createDoc: (body: {
    root: string;
    path: string;
    kind: "md" | "py" | "toml";
    content?: string;
  }) => request<CreateDocResult>("/api/create", { method: "POST", json: body }),
  renameDoc: (body: { root: string; src: string; path: string }) =>
    request<RenameDocResult>("/api/rename", { method: "POST", json: body }),
  deleteDoc: (root: string, path: string) =>
    request<DeleteDocResult>(
      `/api/doc?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`,
      { method: "DELETE" },
    ),
};

// --- Response types -------------------------------------------------------- //
//
// Mirrors webapp.py payload shapes. Keep field names 1:1 with the backend so
// renaming a JSON key on either side is a TS error, not a runtime silent miss.

export type Health = { ok: true; version: string; read_only: boolean };

export type DocRootSummary = {
  id: string;
  label: string;
  kind: "source" | "runtime" | "config";
  connection: string | null;
  editable: boolean;
  base: string;
  singleton: boolean;
  file_count: number;
  tree: TreeNode[];
};

export type TreePayload = { version: string; roots: DocRootSummary[] };

export type TreeNodeFile = {
  name: string;
  /** Last path segment only — the full path is built by joining segments down the tree. */
  path: string;
  type: "file";
  /** File-extension class: "md" | "py" | "toml" | "other". NOT the node type. */
  kind: "md" | "py" | "toml" | "other" | string;
  size: number;
  mtime: number;
  editable: boolean;
};

export type TreeNodeDir = {
  name: string;
  path: string;
  type: "dir";
  children: TreeNode[];
};

export type TreeNodeLink = {
  name: string;
  path: string;
  type: "link";
  children: TreeNode[];
};

export type TreeNode = TreeNodeFile | TreeNodeDir | TreeNodeLink;

export type DocPayload = {
  root: string;
  path: string;
  abs: string;
  raw: string;
  /** legacy field — ignored by the React app; MD is rendered client-side. */
  html: string | null;
  frontmatter: Record<string, string>;
  kind: string;
  editable: boolean;
  size: number;
  mtime: number;
  sha256: string;
  parsed?: ConnectionsSummary;
};

export type ConnectionSummary = {
  name: string;
  driver: string;
  url: string; // masked
  description: string;
  password_set: boolean;
  read_only: boolean;
  pool: { size: number; recycle: number; pre_ping: boolean; echo: boolean };
  application_name: string;
};

export type ConnectionsSummary = {
  default_workspace: string;
  pool_defaults: { size: number; recycle: number; pre_ping: boolean; echo: boolean };
  connections: ConnectionSummary[];
};

export type ConfigPayload = {
  raw: string;
  size: number;
  ok: boolean;
  summary?: ConnectionsSummary;
  error?: string;
  path: string;
};

export type SearchHit = {
  root: string;
  root_label: string;
  path: string;
  name: string;
  kind: string;
  field?: string;
  snippet?: string;
};

export type SearchPayload = {
  q: string;
  matches: SearchHit[];
  truncated?: boolean;
};

export type PutDocResult = {
  saved: true;
  root: string;
  path: string;
  sha256: string;
  size: number;
  mtime: number;
};

export type CreateDocResult = PutDocResult & { created: true };

export type RenameDocResult = PutDocResult & {
  renamed: true;
  src: string;
};

export type DeleteDocResult = { deleted: true; root: string; path: string };