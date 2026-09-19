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
//
// #917 / design REPLY Q6 — the auditable body. When the caller passes
// `groups`, the dialog additionally renders the delete rows bucketed by folder
// (count + size subtotal per folder) with one reason line per file, and pins
// the totals + the confirm button OUTSIDE the scroll area so «the total never
// scrolls out of view». Q6's argument for the list: a count can only be
// accepted or cancelled wholesale, a list with reasons can be *checked*.
// Also additive — a caller with no `groups` (ActionDialog's bulk-decide flow)
// gets exactly the dialog it got before.

import { useMemo } from "react";

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
import type { Group } from "@/api/types";
import { formatBytes } from "@/lib/format";
import {
  deleteFolderBuckets,
  deleteReasonText,
} from "@/lib/deleteConfirmRows";
import {
  ACTION_DELETE_CONFIRM_SUMMARY,
  EXECUTE_ALL_DELETE_CONFIRM,
  EXECUTE_ALL_DELETE_CONFIRM_NO,
  EXECUTE_ALL_DELETE_CONFIRM_YES,
  EXECUTE_DELETE_CONFIRM_FOLDER,
  EXECUTE_DELETE_CONFIRM_REASON,
  EXECUTE_DELETE_CONFIRM_TOTALS,
} from "@/testids";

export interface DeleteConfirmDialogProps {
  open: boolean;
  deleteCount: number;
  /**
   * The groups whose delete-marked rows this confirm covers (#917). When
   * present the dialog renders the folder-bucketed row list, the pinned
   * totals line and the Recycle-Bin sentence. Omitted by callers with no
   * group concept — unchanged compact dialog.
   */
  groups?: readonly Group[];
  /**
   * The execute call's scope — exactly the `scope_paths` that will be sent.
   * `null`/omitted = the unscoped commit (every delete row in `groups`).
   * "Execute (only selected)" passes the selection, so the list names the
   * files that will actually be deleted rather than the whole group's.
   */
  scopePaths?: readonly string[] | null;
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
  groups,
  scopePaths = null,
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

  const buckets = useMemo(
    () => (groups === undefined ? [] : deleteFolderBuckets(groups, scopePaths)),
    [groups, scopePaths]
  );
  const hasRowList = buckets.length > 0;
  // The pinned total describes the list directly below it, so it is summed
  // FROM the buckets rather than taken from `deleteCount` — the two cannot
  // then drift into a dialog that shows one number above another list.
  const listCount = buckets.reduce((acc, b) => acc + b.count, 0);
  const listBytes = buckets.reduce((acc, b) => acc + b.bytes, 0);

  /** "{n} {file(s)} · {size}" — the shape Q6 gives the grand total and every
   *  folder subtotal alike, so the two read as the same kind of fact. */
  const countSize = (count: number, bytes: number) =>
    t("web.delete_confirm.count_size", "{n} {fileWord} · {size}", {
      n: count,
      fileWord:
        count === 1
          ? t("web.delete_confirm.file_singular", "file")
          : t("web.delete_confirm.file_plural", "files"),
      size: formatBytes(bytes),
    });

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
      <DialogContent
        data-testid={EXECUTE_ALL_DELETE_CONFIRM}
        // Q6: «Make the modal tall (min 60vh) and scroll the list inside it,
        // with the counts and the confirm button pinned outside the scroll
        // area.» The column only appears with a row list — a caller without
        // one keeps the old auto-height sheet.
        className={
          hasRowList
            ? "flex max-h-[85vh] min-h-[60vh] w-full max-w-2xl flex-col gap-4"
            : undefined
        }
      >
        <DialogHeader className={hasRowList ? "flex-shrink-0" : undefined}>
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

        {hasRowList && (
          <>
            {/* The scroll area. `min-h-0` is what lets a flex child actually
                shrink below its content height — without it the list grows
                the dialog instead of scrolling and the pinned rows leave the
                viewport. */}
            <div className="min-h-0 flex-1 overflow-y-auto rounded-md border border-hairline bg-toolbar">
              {buckets.map((bucket) => (
                <section key={bucket.folder}>
                  <header
                    data-testid={EXECUTE_DELETE_CONFIRM_FOLDER}
                    // Sticky so the folder a row belongs to stays readable
                    // while its rows scroll — the bucket is the unit Q6 wants
                    // the user to audit ("why is anything from X in here").
                    className="sticky top-0 z-10 flex items-baseline justify-between gap-3 border-b border-hairline bg-toolbar px-3 py-1.5"
                  >
                    <span className="truncate font-mono text-[11.5px] text-ink">
                      {bucket.folder}
                    </span>
                    <span className="whitespace-nowrap text-[11.5px] text-ink-muted">
                      {countSize(bucket.count, bucket.bytes)}
                    </span>
                  </header>
                  <ul className="bg-panel">
                    {bucket.rows.map((row) => (
                      <li
                        key={row.path}
                        className="border-b border-hairline-soft px-3 py-1.5 last:border-b-0"
                      >
                        <div className="flex items-baseline justify-between gap-3">
                          <span className="truncate text-sm text-ink">
                            {row.basename}
                          </span>
                          <span className="whitespace-nowrap text-[11.5px] text-ink-muted">
                            {formatBytes(row.bytes)}
                          </span>
                        </div>
                        {/* Q6: the reason line is the load-bearing part — every
                            row says WHY it is on this list. Sans, 12px, dim. */}
                        <p
                          data-testid={EXECUTE_DELETE_CONFIRM_REASON}
                          className="text-[12px] text-ink-muted"
                        >
                          {deleteReasonText(row, t)}
                        </p>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>

            <div className="flex-shrink-0 space-y-1">
              <p
                data-testid={EXECUTE_DELETE_CONFIRM_TOTALS}
                className="text-sm font-semibold text-danger-warm"
              >
                {countSize(listCount, listBytes)}
              </p>
              {/* Q6: «The one line the modal must carry regardless of framing,
                  because it is the whole safety story.» */}
              <p className="text-[12px] text-ink-muted">
                {t(
                  "web.delete_confirm.recycle_note",
                  "Files are moved to the Recycle Bin and can be restored."
                )}
              </p>
            </div>
          </>
        )}

        <DialogFooter className={hasRowList ? "flex-shrink-0 gap-2" : "gap-2"}>
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
