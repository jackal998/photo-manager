// ActionDialog — modal for setting decisions by field pattern (bulk-decide).
//
// Responsibilities:
//   - Reads action state from the Zustand store (actionDialogOpen, field, pattern, action, ...).
//   - FIELD combo: selects which column to match against.
//   - INPUT MODE (switches on field):
//       * Numeric fields → NumericPanel (threshold or top-N).
//       * String fields → RegexRow + SimpleRow (writes through to regex).
//   - ACTION combo: which decision to apply to matched rows.
//   - LIVE PREVIEW: debounced 300ms, calls store.previewBulkDecide().
//   - APPLY button: shows DeleteConfirmDialog for "delete" action; handles
//     409 locked_paths with a local LockConfirmActionDialog.
//
// Mounted prop-less from App.tsx (like ExecuteDialog), driven entirely by store.

import { type ChangeEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

import { useAppStore } from "@/store/useAppStore";
import { useT } from "@/i18n/useT";
import { ApiConflictError } from "@/api/client";
import {
  ACTION_DIALOG,
  ACTION_FIELD_COMBO,
  ACTION_ACTION_COMBO,
  ACTION_BTN_APPLY,
  ACTION_MATCH_COUNTER,
  ACTION_PREVIEW_LIST,
  ACTION_PREVIEW_TRUNCATED,
  ACTION_LOCK_CONFIRM_DIALOG,
  ACTION_LOCK_CONFIRM_BTN_UNLOCK_APPLY,
  ACTION_LOCK_CONFIRM_BTN_UNLOCKED_ONLY,
  ACTION_LOCK_CONFIRM_BTN_CANCEL,
} from "@/testids";

import { RegexPanel } from "./RegexPanel";
import { NumericPanel } from "./NumericPanel";
import { DeleteConfirmDialog } from "@/components/dialogs/DeleteConfirmDialog";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** All 11 fields in field-combo order, value = canonical English label. */
const FIELDS: { key: string; label: string }[] = [
  { key: "file_name", label: "File Name" },
  { key: "folder", label: "Folder" },
  { key: "size_bytes", label: "Size (Bytes)" },
  { key: "group_count", label: "Group Count" },
  { key: "similarity", label: "Similarity" },
  { key: "score", label: "Score" },
  { key: "creation_date", label: "Creation Date" },
  { key: "shot_date", label: "Shot Date" },
  { key: "resolution", label: "Resolution" },
  { key: "action", label: "Action" },
  { key: "lock", label: "Lock" },
];

/** Numeric fields: show NumericPanel instead of RegexPanel. */
export const NUMERIC_FIELDS = new Set([
  "Size (Bytes)",
  "Group Count",
  "Similarity",
  "Score",
  "Creation Date",
  "Shot Date",
]);

/** Action options: display key → wire value. */
const ACTION_OPTIONS: { key: string; wireValue: string }[] = [
  { key: "keep", wireValue: "" },
  { key: "delete", wireValue: "delete" },
  { key: "remove_from_list", wireValue: "__ignore__" },
  { key: "lock", wireValue: "__lock__" },
  { key: "unlock", wireValue: "__unlock__" },
];

// Radix Select needs a non-empty value; map "" → "__keep__"
const KEEP_SENTINEL = "__keep__";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ActionDialog() {
  const t = useT();

  // Store state
  const actionDialogOpen = useAppStore((s) => s.action.actionDialogOpen);
  const field = useAppStore((s) => s.action.field);
  const pattern = useAppStore((s) => s.action.pattern);
  const action = useAppStore((s) => s.action.action);
  const previewMatched = useAppStore((s) => s.action.previewMatched);
  const previewSample = useAppStore((s) => s.action.previewSample);
  const previewTruncated = useAppStore((s) => s.action.previewTruncated);
  const actionError = useAppStore((s) => s.action.actionError);
  const actionRunning = useAppStore((s) => s.action.actionRunning);
  const manifestPath = useAppStore((s) => s.manifest.path);
  const totalFiles = useAppStore((s) => s.manifest.totalFiles);
  const totalGroups = useAppStore((s) => s.manifest.totalGroups);

  // Store actions
  const closeActionDialog = useAppStore((s) => s.closeActionDialog);
  const setActionField = useAppStore((s) => s.setActionField);
  const setActionPattern = useAppStore((s) => s.setActionPattern);
  const setActionAction = useAppStore((s) => s.setActionAction);
  const previewBulkDecide = useAppStore((s) => s.previewBulkDecide);
  const applyBulkDecide = useAppStore((s) => s.applyBulkDecide);

  // Local state for confirm dialogs
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [lockConflictPaths, setLockConflictPaths] = useState<string[]>([]);
  const [lockConfirmOpen, setLockConfirmOpen] = useState(false);
  // Full matched count from the 409 (#674) — sizes the unlocked subset so the
  // "Apply to Unlocked Only" button enables/disables without a stale preview.
  const [lockConfirmMatchedTotal, setLockConfirmMatchedTotal] = useState<
    number | null
  >(null);

  // Debounce timer ref
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isNumeric = NUMERIC_FIELDS.has(field);

  // ---------------------------------------------------------------------------
  // Debounced live preview
  // ---------------------------------------------------------------------------

  const triggerPreview = useCallback(() => {
    if (debounceRef.current !== null) {
      clearTimeout(debounceRef.current);
    }
    debounceRef.current = setTimeout(() => {
      debounceRef.current = null;
      // Skip preview if pattern is empty or manifest not loaded.
      if (pattern === "" || manifestPath === null) return;
      // Response ordering is enforced INSIDE the store (a monotonic sequence
      // drops out-of-order responses before they commit) — the component only
      // debounces. The previous component-side seqRef guarded an already-
      // committed store write, so it did nothing (adversarial-review).
      void previewBulkDecide();
    }, 300);
  }, [pattern, manifestPath, previewBulkDecide]);

  useEffect(() => {
    triggerPreview();
    return () => {
      if (debounceRef.current !== null) {
        clearTimeout(debounceRef.current);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [field, pattern]);

  // ---------------------------------------------------------------------------
  // Apply handler
  // ---------------------------------------------------------------------------

  const handleApply = useCallback(async () => {
    if (action === "delete") {
      setDeleteConfirmOpen(true);
      return;
    }
    await doApply({});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action]);

  const doApply = useCallback(
    async (opts: { forceLocked?: boolean; skipLocked?: boolean }) => {
      try {
        await applyBulkDecide(opts);
        closeActionDialog();
      } catch (err) {
        if (err instanceof ApiConflictError && err.code === "locked_paths") {
          setLockConflictPaths(err.lockedPaths ?? []);
          // matched_total (#674) sizes the unlocked subset; null when absent.
          setLockConfirmMatchedTotal(err.matchedTotal);
          setLockConfirmOpen(true);
        }
        // Other errors are handled via store.actionError — no throw needed
      }
    },
    [applyBulkDecide, closeActionDialog],
  );

  const handleDeleteConfirmConfirm = useCallback(async () => {
    setDeleteConfirmOpen(false);
    await doApply({});
  }, [doApply]);

  const handleDeleteConfirmCancel = useCallback(() => {
    setDeleteConfirmOpen(false);
  }, []);

  // Unlock & Apply All — re-run mutating the locked rows too.
  const handleLockConfirmForce = useCallback(async () => {
    setLockConfirmOpen(false);
    await doApply({ forceLocked: true });
  }, [doApply]);

  // Apply to Unlocked Only — re-run applying to the unlocked subset; locked
  // rows stay locked + undecided (#674, mirrors Qt APPLY_UNLOCKED_ONLY).
  const handleLockConfirmUnlockedOnly = useCallback(async () => {
    setLockConfirmOpen(false);
    await doApply({ skipLocked: true });
  }, [doApply]);

  const handleLockConfirmCancel = useCallback(() => {
    setLockConfirmOpen(false);
  }, []);

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen && !actionRunning) {
        closeActionDialog();
      }
    },
    [actionRunning, closeActionDialog]
  );

  // ---------------------------------------------------------------------------
  // Field combo handler
  // ---------------------------------------------------------------------------

  const handleFieldChange = useCallback(
    (e: ChangeEvent<HTMLSelectElement>) => {
      setActionField(e.target.value);
      // Pattern resets when field changes (done in store), so clear preview too
    },
    [setActionField]
  );

  // ---------------------------------------------------------------------------
  // Action combo handler — Radix Select pattern
  // ---------------------------------------------------------------------------

  const handleActionChange = useCallback(
    (e: ChangeEvent<HTMLSelectElement>) => {
      const raw = e.target.value;
      setActionAction(raw === KEEP_SENTINEL ? "" : raw);
    },
    [setActionAction]
  );

  // ---------------------------------------------------------------------------
  // Match counter text
  // ---------------------------------------------------------------------------

  let matchCounterText: string;
  if (pattern === "") {
    matchCounterText = t("web.action_dialog.match_counter_invalid", "—");
  } else if (previewMatched < 0) {
    matchCounterText = t("web.action_dialog.match_counter_invalid", "—");
  } else {
    // Check if pattern is top-N
    const topNMatch = /^__top_n__:(\d+):(asc|desc)$/.exec(pattern);
    if (topNMatch !== null) {
      matchCounterText = t(
        "web.action_dialog.match_counter_topn",
        "{matched} matched (≤{n} per group × {group_count} groups)",
        {
          matched: previewMatched,
          n: topNMatch[1],
          group_count: totalGroups,
        }
      );
    } else {
      matchCounterText = t(
        "web.action_dialog.match_counter",
        "{matched} of {total} match",
        { matched: previewMatched, total: totalFiles }
      );
    }
  }

  const actionSelectValue = action === "" ? KEEP_SENTINEL : action;

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <>
      <DeleteConfirmDialog
        open={deleteConfirmOpen}
        deleteCount={previewMatched >= 0 ? previewMatched : 0}
        onConfirm={() => void handleDeleteConfirmConfirm()}
        onCancel={handleDeleteConfirmCancel}
      />

      {/* Inline lock-conflict dialog for Action dialog (separate from execute
          flow). Three verdicts mirror the Qt LockedRowsConfirmDialog (#674):
          Cancel / Apply to Unlocked Only / Unlock & Apply All. "Apply to
          Unlocked Only" is disabled only when the matched total is known AND
          leaves no unlocked rows (fail OPTIMISTIC when the count is absent). */}
      <Dialog open={lockConfirmOpen} onOpenChange={(o) => !o && handleLockConfirmCancel()}>
        <DialogContent data-testid={ACTION_LOCK_CONFIRM_DIALOG}>
          <DialogHeader>
            <DialogTitle>
              {t("web.action_dialog.lock_conflict_title", "Locked rows would be affected")}
            </DialogTitle>
          </DialogHeader>
          <div className="text-sm text-neutral-700 mb-2">
            {t(
              "web.action_dialog.lock_conflict_body",
              "{n} locked row(s) would be affected. Unlock and apply anyway?",
              { n: lockConflictPaths.length }
            )}
          </div>
          {lockConflictPaths.length > 0 && (
            <ul className="max-h-40 overflow-y-auto text-xs text-neutral-600 space-y-0.5 my-2">
              {lockConflictPaths.map((p) => (
                <li key={p} className="truncate font-mono">{p}</li>
              ))}
            </ul>
          )}
          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              data-testid={ACTION_LOCK_CONFIRM_BTN_CANCEL}
              onClick={handleLockConfirmCancel}
            >
              {t("web.action_dialog.cancel", "Cancel")}
            </Button>
            <Button
              variant="outline"
              data-testid={ACTION_LOCK_CONFIRM_BTN_UNLOCKED_ONLY}
              onClick={() => void handleLockConfirmUnlockedOnly()}
              disabled={
                lockConfirmMatchedTotal !== null &&
                lockConfirmMatchedTotal - lockConflictPaths.length <= 0
              }
            >
              {t("web.action_dialog.unlocked_only", "Apply to Unlocked Only")}
            </Button>
            <Button
              variant="destructive"
              data-testid={ACTION_LOCK_CONFIRM_BTN_UNLOCK_APPLY}
              onClick={() => void handleLockConfirmForce()}
            >
              {t("web.action_dialog.unlock_and_apply", "Unlock & Apply")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={actionDialogOpen} onOpenChange={handleOpenChange}>
        <DialogContent
          data-testid={ACTION_DIALOG}
          className="max-w-2xl w-full"
          onInteractOutside={(e) => {
            if (actionRunning) e.preventDefault();
          }}
          onEscapeKeyDown={(e) => {
            if (actionRunning) e.preventDefault();
          }}
        >
          <DialogHeader>
            <DialogTitle>
              {t("web.action_dialog.title", "Set Action by Field")}
            </DialogTitle>
          </DialogHeader>

          {/* Field selector */}
          <div className="flex items-center gap-3 mt-2">
            <label className="text-sm font-medium w-20 flex-shrink-0">
              {t("web.action_dialog.field_label", "Field:")}
            </label>
            <select
              data-testid={ACTION_FIELD_COMBO}
              value={field}
              onChange={handleFieldChange}
              className="rounded border border-neutral-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400 flex-1"
            >
              {FIELDS.map(({ key, label }) => (
                <option key={key} value={label}>
                  {t(`web.column.${key}`, label)}
                </option>
              ))}
            </select>
          </div>

          {/* Input mode */}
          <div className="mt-3">
            {/* key={field} forces a fresh remount on every field change so a
                panel's local input state can't carry a stale value into a
                different field's encoding (e.g. a Score value re-encoded as a
                Date threshold) — adversarial-review cross-field-desync fix. */}
            {isNumeric ? (
              <NumericPanel
                key={field}
                field={field}
                pattern={pattern}
                onPatternChange={setActionPattern}
              />
            ) : (
              <RegexPanel
                key={field}
                pattern={pattern}
                onPatternChange={setActionPattern}
              />
            )}
          </div>

          {/* Action selector */}
          <div className="flex items-center gap-3 mt-3">
            <label className="text-sm font-medium w-20 flex-shrink-0">
              {t("web.action_dialog.set_action_label", "Set action for each match:")}
            </label>
            <select
              data-testid={ACTION_ACTION_COMBO}
              value={actionSelectValue}
              onChange={handleActionChange}
              className="rounded border border-neutral-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400 flex-1"
            >
              {ACTION_OPTIONS.map(({ key, wireValue }) => (
                <option
                  key={key}
                  value={wireValue === "" ? KEEP_SENTINEL : wireValue}
                >
                  {t(`web.decision.${key}`, key)}
                </option>
              ))}
            </select>
          </div>

          {/* Match counter */}
          <div className="flex items-center gap-2 mt-3">
            <span
              data-testid={ACTION_MATCH_COUNTER}
              className="text-sm text-neutral-600"
            >
              {matchCounterText}
            </span>
            {actionRunning && (
              <span className="text-xs text-neutral-400">updating…</span>
            )}
          </div>

          {/* Error */}
          {actionError !== null && (
            <p role="alert" className="text-sm text-red-600 mt-1">
              {actionError}
            </p>
          )}

          {/* Preview list */}
          {previewSample.length > 0 && (
            <div className="mt-2">
              <p className="text-xs font-medium text-neutral-500 mb-1">
                {t("web.action_dialog.preview_label", "Preview")}
              </p>
              <ul
                data-testid={ACTION_PREVIEW_LIST}
                className="max-h-40 overflow-y-auto text-xs text-neutral-600 space-y-0.5 rounded border border-neutral-200 p-2 bg-neutral-50"
              >
                {previewSample.map((p) => (
                  <li key={p} className="truncate font-mono">{p}</li>
                ))}
              </ul>
              {previewTruncated && (
                <p
                  data-testid={ACTION_PREVIEW_TRUNCATED}
                  className="text-xs text-neutral-400 mt-1"
                >
                  {t("web.action_dialog.preview_truncated", "…and {n} more", {
                    n: previewMatched - previewSample.length,
                  })}
                </p>
              )}
            </div>
          )}

          {previewSample.length === 0 && previewMatched === 0 && pattern !== "" && (
            <p className="text-xs text-neutral-400 mt-2">
              {t("web.action_dialog.preview_empty", "No matches")}
            </p>
          )}

          {/* Footer */}
          <DialogFooter className="mt-4 gap-2 sm:gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={closeActionDialog}
              disabled={actionRunning}
            >
              {t("web.action_dialog.cancel", "Cancel")}
            </Button>
            <Button
              type="button"
              data-testid={ACTION_BTN_APPLY}
              onClick={() => void handleApply()}
              disabled={actionRunning || pattern === "" || manifestPath === null}
            >
              {actionRunning
                ? t("web.action_dialog.applying", "Applying…")
                : t("web.action_dialog.apply_button", "Apply")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
