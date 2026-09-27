import { useState, type MouseEvent } from "react";
import * as ContextMenu from "@radix-ui/react-context-menu";
import type { TreeNode as TreeNodeT } from "../lib/api";
import DocContextMenu from "./DocContextMenu";

type Props = {
  node: TreeNodeT;
  rootId: string;
  /** Path of this node relative to the root, built by joining segments ("a/b/c.md"). */
  relPath: string;
  depth: number;
  onPick: (path: string) => void;
};

/**
 * Recursive tree node. Mirrors the backend contract from webapp.build_tree():
 *   - files:  { name, path, type: "file", kind: "md"|..., editable }
 *   - dirs:   { name, path, type: "dir", children }
 *   - links:  { name, path, type: "link", children }
 *
 * `path` on each node is only the last segment, so the full root-relative path
 * is assembled here (parent relPath + "/" + name) — same as the legacy app.js
 * renderNodes(prefix) logic. Folders use native <details>/<summary>; every row
 * gets a Radix ContextMenu (rename/delete on files, create on folders).
 */
export default function TreeNode({ node, rootId, relPath, depth, onPick }: Props) {
  const [open, setOpen] = useState(depth < 1);

  if (node.type === "dir" || node.type === "link") {
    const childRel = (parent: string, name: string) => (parent ? `${parent}/${name}` : name);
    return (
      <ContextMenu.Root>
        <ContextMenu.Trigger asChild>
          <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
            <summary style={{ paddingLeft: 8 + depth * 14 }}>
              <span className="caret">{open ? "▾" : "▸"}</span>
              <span className="name">{node.name}</span>
            </summary>
            <ul>
              {(node.children ?? []).map((child) => (
                <li key={child.path || child.name}>
                  <TreeNode
                    node={child}
                    rootId={rootId}
                    relPath={childRel(relPath, child.name)}
                    depth={depth + 1}
                    onPick={onPick}
                  />
                </li>
              ))}
            </ul>
          </details>
        </ContextMenu.Trigger>
        <DocContextMenu rootId={rootId} folderPath={relPath} />
      </ContextMenu.Root>
    );
  }

  // type === "file"
  return (
    <ContextMenu.Root>
      <ContextMenu.Trigger asChild>
        <button
          type="button"
          className="leaf"
          style={{ paddingLeft: 8 + depth * 14 }}
          onClick={(e: MouseEvent) => {
            e.preventDefault();
            onPick(relPath);
          }}
          title={node.kind === "other" ? "不支持预览的文件类型" : undefined}
        >
          <span className="name">{node.name}</span>
        </button>
      </ContextMenu.Trigger>
      <DocContextMenu rootId={rootId} filePath={relPath} fileName={node.name} />
    </ContextMenu.Root>
  );
}