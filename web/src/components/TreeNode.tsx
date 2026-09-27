import { useState, type MouseEvent } from "react";
import * as ContextMenu from "@radix-ui/react-context-menu";
import type { TreeNode as TreeNodeT } from "../lib/api";
import DocContextMenu from "./DocContextMenu";

type Props = {
  node: TreeNodeT;
  rootId: string;
  depth: number;
  onPick: (path: string) => void;
};

/**
 * Recursive tree node. Folders use native <details>/<summary> for instant
 * expand/collapse with zero JS state. Singletons (a single file rendered as
 * a "root", like SKILL.md) render as a single clickable row.
 *
 * Right-click on any row opens a Radix ContextMenu — see DocContextMenu.
 */
export default function TreeNode({ node, rootId, depth, onPick }: Props) {
  const [open, setOpen] = useState(depth < 1);
  const isFolder = node.kind === "folder" && node.children && node.children.length > 0;
  const isFile = node.kind === "file" || node.kind === "singleton";

  if (isFolder) {
    return (
      <ContextMenu.Root>
        <ContextMenu.Trigger asChild>
          <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
            <summary style={{ paddingLeft: 8 + depth * 14 }}>
              <span className="caret">{open ? "▾" : "▸"}</span>
              <span className="name">{node.name}</span>
            </summary>
            <ul>
              {node.children!.map((child) => (
                <li key={child.path || child.name}>
                  <TreeNode
                    node={child}
                    rootId={rootId}
                    depth={depth + 1}
                    onPick={onPick}
                  />
                </li>
              ))}
            </ul>
          </details>
        </ContextMenu.Trigger>
        <DocContextMenu rootId={rootId} folderPath={node.path} />
      </ContextMenu.Root>
    );
  }

  if (isFile) {
    const path = node.path || node.name;
    return (
      <ContextMenu.Root>
        <ContextMenu.Trigger asChild>
          <button
            type="button"
            className="leaf"
            style={{ paddingLeft: 8 + depth * 14 }}
            onClick={(e: MouseEvent) => {
              e.preventDefault();
              onPick(path);
            }}
          >
            <span className="name">{node.name}</span>
          </button>
        </ContextMenu.Trigger>
        <DocContextMenu rootId={rootId} filePath={path} fileName={node.name} />
      </ContextMenu.Root>
    );
  }

  // Unknown — render nothing
  return null;
}