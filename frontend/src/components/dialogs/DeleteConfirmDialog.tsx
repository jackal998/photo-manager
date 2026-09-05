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
//
// #741 sub-item C — pattern-aware variant: when the caller (the ActionDialog
// bulk-decide flow) passes `patternSummary`, this OVERRIDES the two generic
// branches above with Qt-parity DEFERRED-DECISION copy (mirrors
// app/views/dialogs/delete_regex_confirm_dialog.py::DeleteRegexConfirmDialog)
// — Apply only QUEUES a decision; nothing is deleted until Execute Action
// runs, so the wording must say "mark for deletion", not "will be deleted".
// The prop is optional/additive: every existing caller (no patternSummary)
// keeps its pre-existing copy unchanged.

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
  ACTION_DELETE_CONFIRM_SUMMARY,
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
  /**
   * Human-readable pattern summary (see lib/patternSummary.ts), e.g.
   * "File Name contains 'IMG'". When present, this dialog renders the
   * pattern-aware deferred-decision copy instead of the generic branches
   * below — see the module doc comment. Undefined for every non-ActionDialog
   * caller (unchanged behaviour).
   */
  patternSummary?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function DeleteConfirmDialog({
  open,
  deleteCount,
  groupIds = [],
  patternSummary,
  onConfirm,
  onCancel,
}: DeleteConfirmDialogProps) {
  const t = useT();
  const hasGroups = groupIds.length > 0;
  const hasPatternSummary = patternSummary !== undefined;
  // "file(s)" would flatten the singular/plural distinction the copy relies
  // on ("1 file" vs "5 files") — translate the two forms separately instead
  // (zh_TW has no plural marking, so both keys carry the same Chinese noun).
  const fileWord =
    deleteCount === 1
      ? t("web.delete_confirm.file_singular", "file")
      : t("web.delete_confirm.file_plural", "files");

  const title = hasPatternSummary
    ? t("web.action_dialog.delete_confirm_title", "Confirm bulk-delete decision")
    : hasGroups
    ? t("web.delete_confirm.title_groups", "Entire group(s) will be deleted")
    : t("web.delete_confirm.title_all", "Delete all files?");

  const confirmLabel = hasPatternSummary
    ? t(
        "web.action_dialog.delete_confirm_button",
        "Mark {matched} files for deletion",
        { matched: deleteCount }
      )
    : t("web.delete_confirm.yes_delete_all", "Yes, delete all");

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent data-testid={EXECUTE_ALL_DELETE_CONFIRM}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {hasPatternSummary ? (
              <span data-testid={ACTION_DELETE_CONFIRM_SUMMARY}>
                {t(
                  "web.action_dialog.delete_confirm_body",
                  "This will mark {matched} file(s) for deletion via {summary}. Files move to the Recycle Bin only once you run Execute Action.",
                  { matched: deleteCount, summary: patternSummary }
                )}
              </span>
            ) : hasGroups ? (
              t(
                "web.delete_confirm.body_groups",
                "Group(s) {groups} will have EVERY file deleted ({n} {fileWord} will be deleted). Files will be sent to the Recycle Bin. Continue?",
                { groups: groupIds.join(", "), n: deleteCount, fileWord }
              )
            ) : (
              t(
                "web.delete_confirm.body_all",
                "{n} {fileWord} will be deleted. This operation moves files to the recycle bin and cannot easily be undone in bulk. Are you sure?",
                { n: deleteCount, fileWord }
              )
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2">
          <Button
            variant="outline"
            data-testid={EXECUTE_ALL_DELETE_CONFIRM_NO}
            onClick={onCancel}
          >
            {t("web.delete_confirm.cancel", "Cancel")}
          </Button>
          <Button
            variant="destructive"
            data-testid={EXECUTE_ALL_DELETE_CONFIRM_YES}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
