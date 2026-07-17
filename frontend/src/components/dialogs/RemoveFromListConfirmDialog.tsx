// RemoveFromListConfirmDialog — shown before the Execute dialog's row menu
// finalizes a "Remove from list" (outcome='ignored').
//
// Mirrors Qt's "Remove from List" QMessageBox (default No) in
// execute_action_dialog.py: removing only updates the manifest, no file on
// disk is touched, and the row will not reappear until a re-scan.
//
// Testid shape (EXECUTE_REMOVE_CONFIRM family):
//   - wrapper:     execute-remove-confirm
//   - confirm btn: execute-remove-confirm-yes
//   - cancel btn:  execute-remove-confirm-no   (the default — explicit Yes req.)
//
// Props-driven (like DeleteConfirmDialog) so the component stays pure.

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n/useT";
import {
  EXECUTE_REMOVE_CONFIRM,
  EXECUTE_REMOVE_CONFIRM_NO,
  EXECUTE_REMOVE_CONFIRM_YES,
} from "@/testids";

export interface RemoveFromListConfirmDialogProps {
  open: boolean;
  basename: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function RemoveFromListConfirmDialog({
  open,
  basename,
  onConfirm,
  onCancel,
}: RemoveFromListConfirmDialogProps) {
  const t = useT();
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent data-testid={EXECUTE_REMOVE_CONFIRM}>
        <DialogHeader>
          <DialogTitle>
            {t("web.remove_confirm.title", "Remove from list?")}
          </DialogTitle>
          <DialogDescription>
            {t(
              "web.remove_confirm.body",
              "Remove {basename} from the list? This only updates the manifest — no file on disk is touched. The removed row will not appear in the review tree until you re-scan.",
              { basename: basename || t("web.remove_confirm.this_item", "this item") }
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2">
          <Button
            variant="outline"
            data-testid={EXECUTE_REMOVE_CONFIRM_NO}
            onClick={onCancel}
          >
            {t("web.remove_confirm.cancel", "Cancel")}
          </Button>
          <Button
            variant="destructive"
            data-testid={EXECUTE_REMOVE_CONFIRM_YES}
            onClick={onConfirm}
          >
            {t("web.remove_confirm.yes_remove", "Yes, remove")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
