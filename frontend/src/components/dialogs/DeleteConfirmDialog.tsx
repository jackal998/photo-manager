// DeleteConfirmDialog — shown before executing when ALL files in scope would
// be deleted (the "all-delete" safety gate, §5.5).
//
// Testid shape:
//   - wrapper:      execute-all-delete-confirm  (EXECUTE_ALL_DELETE_CONFIRM)
//   - confirm btn:  execute-all-delete-confirm-yes
//   - cancel btn:   execute-all-delete-confirm-no
//
// The body must contain a DIGIT representing the delete count so QA can
// assert on it (§5.5: "body containing a DIGIT 'N files will be deleted'").
//
// Props-driven: the caller (ExecuteDialog) passes open/onConfirm/onCancel
// and the file count. This keeps the component pure and easy to test.

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
  onConfirm: () => void;
  onCancel: () => void;
}

export function DeleteConfirmDialog({
  open,
  deleteCount,
  onConfirm,
  onCancel,
}: DeleteConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent data-testid={EXECUTE_ALL_DELETE_CONFIRM}>
        <DialogHeader>
          <DialogTitle>Delete all files?</DialogTitle>
          <DialogDescription>
            {deleteCount} {deleteCount === 1 ? "file" : "files"} will be
            deleted. This operation moves files to the recycle bin and cannot
            easily be undone in bulk. Are you sure?
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
