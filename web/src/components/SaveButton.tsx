import { useState } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { api, ApiError } from "../lib/api";
import { useAppState } from "../lib/state";
import { toast } from "sonner";

/**
 * Save button + confirm dialog. Lives in Reader's header; reads the live
 * draft content from app state (Editor dispatches `draft` on every change).
 *
 * 409 stale-sha flow: server reports the on-disk sha; the user can pick
 * "force overwrite" to retry with the new base_sha256, or "重新载入" to
 * discard their changes and re-read the file.
 */
export default function SaveButton() {
  return (
    <AlertDialog.Root>
      <AlertDialog.Trigger asChild>
        <button type="button" className="primary">
          保存
        </button>
      </AlertDialog.Trigger>
      <SaveDialog />
    </AlertDialog.Root>
  );
}

function SaveDialog() {
  const { state, openDoc, refreshTree, setDirty, setEditing } = useAppState();
  const doc = state.current;
  const [busy, setBusy] = useState(false);
  const [pendingForce, setPendingForce] = useState(false);

  if (!doc) return null;

  async function doSave(force: boolean) {
    if (!doc) return;
    setBusy(true);
    try {
      await api.putDoc({
        root: doc.root,
        path: doc.path,
        content: state.draft,
        base_sha256: force ? doc.sha256 : doc.sha256,
      });
      toast.success(`已保存 ${doc.path}`);
      setDirty(false);
      setEditing(false);
      setPendingForce(false);
      await refreshTree();
      await openDoc(doc.root, doc.path);
    } catch (e: any) {
      if (e instanceof ApiError && e.status === 409) {
        // Re-read to grab the latest sha, then mark force-retry pending.
        await openDoc(doc.root, doc.path);
        setPendingForce(true);
        toast.message("磁盘上的版本已变更 — 再点一次保存以覆盖");
      } else {
        toast.error(`保存失败:${e?.message ?? e}`);
        setPendingForce(false);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <AlertDialog.Portal>
      <AlertDialog.Overlay className="overlay" />
      <AlertDialog.Content className="modal-box small">
        <AlertDialog.Title>保存 {doc.path}?</AlertDialog.Title>
        <AlertDialog.Description>
          {pendingForce
            ? "磁盘版本已变更。继续保存会用你的编辑覆盖磁盘上最新的版本。"
            : "覆盖磁盘上当前的版本。该操作会立即写入文件。"}
        </AlertDialog.Description>
        <div className="actions">
          <AlertDialog.Cancel asChild>
            <button type="button">取消</button>
          </AlertDialog.Cancel>
          <AlertDialog.Action asChild>
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={(e) => {
                e.preventDefault();
                void doSave(pendingForce);
              }}
            >
              保存
            </button>
          </AlertDialog.Action>
        </div>
      </AlertDialog.Content>
    </AlertDialog.Portal>
  );
}