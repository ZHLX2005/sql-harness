import * as Dialog from "@radix-ui/react-dialog";

/**
 * Shortcut help. Radix Dialog provides:
 *   - portal (immune to z-index / overflow ancestors)
 *   - focus trap (Tab cycles inside, Escape closes, focus moves to close btn on open)
 *   - outside pointerdown dismisses
 *   - body scroll lock while open
 *   - aria-labelledby / aria-describedby
 *
 * This replaces the legacy #help modal that could not be closed on narrow
 * viewports because the centered box covered the backdrop and there was no
 * scroll cap on .modal-box.
 */
export default function HelpDialog() {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button type="button" className="icon" title="快捷键 (?)">
          ?
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className="modal-box">
          <Dialog.Title>快捷键</Dialog.Title>
          <table className="keys">
            <tbody>
              <tr>
                <td>
                  <kbd>Ctrl</kbd>+<kbd>S</kbd>
                </td>
                <td>保存</td>
              </tr>
              <tr>
                <td>
                  <kbd>Ctrl</kbd>+<kbd>E</kbd>
                </td>
                <td>编辑 / 退出编辑</td>
              </tr>
              <tr>
                <td>
                  <kbd>Ctrl</kbd>+<kbd>N</kbd>
                </td>
                <td>新建文档</td>
              </tr>
              <tr>
                <td>
                  <kbd>F2</kbd>
                </td>
                <td>重命名当前文档</td>
              </tr>
              <tr>
                <td>
                  <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>L</kbd>
                </td>
                <td>切换明暗主题</td>
              </tr>
              <tr>
                <td>
                  <kbd>Esc</kbd>
                </td>
                <td>取消编辑 / 关闭弹层</td>
              </tr>
            </tbody>
          </table>
          <p className="hint">
            在左侧文件上右键,可以新建 / 重命名 / 删除。
            <code>connections.toml</code> 会以结构化面板展示,同时保留原文编辑。
          </p>
          <Dialog.Close asChild>
            <button type="button" className="primary">
              知道了
            </button>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}