// DeleteConfirmDialog — shown before executing when one or more GROUPS would
// have EVERY in-scope member deleted (the "complete-delete-group" safety
// gate, #733 — Qt parity with ``_complete_delete_groups`` in
// app/views/dialogs/execute_action_dialog.py). This fires per-GROUP, not
// per-scope: a mixed manifest where only SOME groups are entirely delete
// still blocks, even though the overall scope also contains kept rows.
//
// Testid shape:
//   - wrapper:      execute-all-delete-confirm  (EXECUTE_ALL_DELETE_CONFIRM)
//   - confirm btn:  execute-all-delete-confirm-yes
//   - cancel btn:   execute-all-delete-confirm-no
//
// The body must contain a DIGIT representing the delete count so QA can
// assert on it (§5.5: "body containing a DIGIT 'N files will be deleted'").
//
// Props-driven: the caller (ExecuteDialog) passes open/onConfirm/onCancel,
// the file count, and the qualifying group IDs. This keeps the component
// pure and easy to test.

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  EXECUTE_ALL_DELETE_CONFIRM,
  EXECUTE_ALL_DELETE_CONFIRM_NO,
  EXECUTE_ALL_DELETE_CONFIRM_YES,
} from "@/testids";

export interface DeleteConfirmDialogProps {
  open: boolean;
  deleteCount: number;
  /**
   * The complete-delete group IDs (as strings) backing this confirm.
   * Optional/empty for callers with no group concept (the field-based
   * bulk-decide ActionDialog) — falls back to the generic copy below.
   */
  groupIds?: string[];
  onConfirm: () => void;
  onCancel: () => void;
}

export function DeleteConfirmDialog({
  open,
  deleteCount,
  groupIds = [],
  onConfirm,
  onCancel,
}: DeleteConfirmDialogProps) {
  const hasGroups = groupIds.length > 0;
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent data-testid={EXECUTE_ALL_DELETE_CONFIRM}>
        <DialogHeader>
          <DialogTitle>
            {hasGroups ? "Entire group(s) will be deleted" : "Delete all files?"}
          </DialogTitle>
          <DialogDescription>
            {hasGroups ? (
              <>
                Group(s) {groupIds.join(", ")} will have EVERY file deleted (
                {deleteCount} {deleteCount === 1 ? "file" : "files"} will be
                deleted). Files will be sent to the Recycle Bin. Continue?
              </>
            ) : (
              <>
                {deleteCount} {deleteCount === 1 ? "file" : "files"} will be
                deleted. This operation moves files to the recycle bin and
                cannot easily be undone in bulk. Are you sure?
              </>
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2">
          <Button
            variant="outline"
            data-testid={EXECUTE_ALL_DELETE_CONFIRM_NO}
            onClick={onCancel}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            data-testid={EXECUTE_ALL_DELETE_CONFIRM_YES}
            onClick={onConfirm}
          >
            Yes, delete all
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
