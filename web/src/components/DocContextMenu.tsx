import { useState } from "react";
import * as ContextMenu from "@radix-ui/react-context-menu";
import * as Dialog from "@radix-ui/react-dialog";
import { api } from "../lib/api";
import { useAppState } from "../lib/state";
import { toast } from "sonner";

type Props = {
  rootId: string;
  filePath?: string;
  fileName?: string;
  folderPath?: string;
};

/**
 * Right-click context menu attached to every tree node. Three actions when a
 * file is the target:
 *   - 重命名 — rename the file in place
 *   - 删除   — delete (with confirmation dialog)
 * Two actions when on a folder root:
 *   - 新建文档 — create a new file under that folder
 *
 * The rename + create + delete hit /api/{doc,create,rename} and re-render the
 * tree on success. Errors surface as a toast so the user sees what failed.
 */
export default function DocContextMenu({ rootId, filePath, fileName, folderPath }: Props) {
  const { refreshTree } = useAppState();
  const [renameOpen, setRenameOpen] = useState(false);
  const [newName, setNewName] = useState(fileName ?? "");
  const [confirmDelete, setConfirmDelete] = useState(false);

  async function doRename() {
    if (!filePath || !newName.trim()) return;
    try {
      await api.renameDoc({ root: rootId, src: filePath, path: newName.trim() });
      toast.success(`已重命名为 ${newName}`);
      setRenameOpen(false);
      await refreshTree();
    } catch (e: any) {
      toast.error(`重命名失败:${e?.message ?? e}`);
    }
  }

  async function doDelete() {
    if (!filePath) return;
    try {
      await api.deleteDoc(rootId, filePath);
      toast.success(`已删除 ${fileName ?? filePath}`);
      setConfirmDelete(false);
      await refreshTree();
    } catch (e: any) {
      toast.error(`删除失败:${e?.message ?? e}`);
    }
  }

  function onCreatePrompt() {
    const name = window.prompt("新文件名(含扩展名,例如 demo.md):");
    if (!name) return;
    const kind = name.endsWith(".py")
      ? "py"
      : name.endsWith(".toml")
        ? "toml"
        : "md";
    void (async () => {
      try {
        await api.createDoc({ root: rootId, path: name, kind });
        toast.success(`已创建 ${name}`);
        await refreshTree();
      } catch (e: any) {
        toast.error(`创建失败:${e?.message ?? e}`);
      }
    })();
  }

  return (
    <>
      <ContextMenu.Portal>
        <ContextMenu.Content className="menu">
          {filePath ? (
            <>
              <ContextMenu.Item
                className="item"
                onSelect={(e) => {
                  e.preventDefault();
                  setNewName(fileName ?? filePath);
                  setRenameOpen(true);
                }}
              >
                重命名
              </ContextMenu.Item>
              <ContextMenu.Item
                className="item danger"
                onSelect={(e) => {
                  e.preventDefault();
                  setConfirmDelete(true);
                }}
              >
                删除
              </ContextMenu.Item>
            </>
          ) : folderPath !== undefined ? (
            <ContextMenu.Item className="item" onSelect={() => onCreatePrompt()}>
              新建文档
            </ContextMenu.Item>
          ) : null}
        </ContextMenu.Content>
      </ContextMenu.Portal>

      {/* Rename dialog */}
      <Dialog.Root open={renameOpen} onOpenChange={setRenameOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content className="modal-box small">
            <Dialog.Title>重命名</Dialog.Title>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void doRename();
              }}
            >
              <input
                autoFocus
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
              />
              <div className="actions">
                <Dialog.Close asChild>
                  <button type="button">取消</button>
                </Dialog.Close>
                <button type="submit" className="primary">
                  确定
                </button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      {/* Delete confirmation */}
      <Dialog.Root open={confirmDelete} onOpenChange={setConfirmDelete}>
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content className="modal-box small">
            <Dialog.Title>删除 {fileName ?? filePath}?</Dialog.Title>
            <p className="hint">该操作不可撤销。</p>
            <div className="actions">
              <Dialog.Close asChild>
                <button type="button">取消</button>
              </Dialog.Close>
              <button type="button" className="danger" onClick={() => void doDelete()}>
                删除
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}