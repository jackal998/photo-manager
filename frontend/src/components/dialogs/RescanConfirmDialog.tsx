// RescanConfirmDialog — shown when the user starts a scan while the loaded
// manifest still has pending (un-executed) decisions. Re-scanning rebuilds the
// manifest from scratch, so those staged decisions would be lost. Mirrors the
// Qt "Discard pending decisions?" QMessageBox (MainWindow._confirm_no_pending_decisions,
// app/views/main_window.py) — Qt scenario s27_rescan_confirm (#142).
//
// Testid shape:
//   - wrapper:      scan-rescan-confirm-dialog   (SCAN_RESCAN_CONFIRM_DIALOG)
//   - cancel btn:   scan-rescan-confirm-cancel    (the safe default — keeps decisions)
//   - discard btn:  scan-rescan-confirm-discard   (destructive — proceeds with the scan)
//
// The body interpolates the pending-decision COUNT (pluralized) so QA can
// assert on it, matching the Qt prompt body which states "{n} pending decision(s)".
//
// Props-driven: the caller (ScanDialog) owns the open state + the decision
// count + the proceed/cancel handlers. This keeps the component pure and the
// nested-modal interaction (it portals over the open ScanDialog) handled by the
// caller's onInteractOutside guard rather than by store wiring.

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
  SCAN_RESCAN_CONFIRM_DIALOG,
  SCAN_RESCAN_CONFIRM_CANCEL,
  SCAN_RESCAN_CONFIRM_DISCARD,
} from "@/testids";

export interface RescanConfirmDialogProps {
  open: boolean;
  pendingCount: number;
  onConfirm: () => void;
  onCancel: () => void;
}

export function RescanConfirmDialog({
  open,
  pendingCount,
  onConfirm,
  onCancel,
}: RescanConfirmDialogProps) {
  const t = useT();

  const noun =
    pendingCount === 1
      ? t("web.rescan_confirm.noun_singular", "pending decision")
      : t("web.rescan_confirm.noun_plural", "pending decisions");
  // "1 pending decision" / "2 pending decisions" — the count phrase the body
  // and the QA assertion both key on (Qt parity).
  const pending = `${pendingCount} ${noun}`;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent data-testid={SCAN_RESCAN_CONFIRM_DIALOG}>
        <DialogHeader>
          <DialogTitle>
            {t("web.rescan_confirm.title", "Discard pending decisions?")}
          </DialogTitle>
          <DialogDescription>
            {t(
              "web.rescan_confirm.body",
              "You have {pending} on the loaded manifest. Starting a new scan will discard them.",
              { pending }
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2">
          <Button
            variant="outline"
            data-testid={SCAN_RESCAN_CONFIRM_CANCEL}
            onClick={onCancel}
          >
            {t("web.rescan_confirm.cancel", "Cancel")}
          </Button>
          <Button
            variant="destructive"
            data-testid={SCAN_RESCAN_CONFIRM_DISCARD}
            onClick={onConfirm}
          >
            {t("web.rescan_confirm.discard_and_rescan", "Discard & Rescan")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
