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
import { OverlayResizeHandle } from "@/components/ui/overlay-resize-handle";

import { useAppStore } from "@/store/useAppStore";
import { useT } from "@/i18n/useT";
import { useOverlayGeometry } from "@/hooks/useOverlayGeometry";
import { ApiConflictError, getSettings, patchSettings } from "@/api/client";
import {
  parseRecentPatterns,
  recordRecentPattern,
  visibleRecentPatterns,
  RECENT_PATTERNS_KEY,
  type RecentPatternEntry,
} from "@/lib/recentPatterns";
import { buildPatternSummary } from "@/lib/patternSummary";
import {
  ACTION_DIALOG,
  ACTION_TITLE_BAR,
  ACTION_RESIZE_HANDLE,
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
  ACTION_RECENT_SELECT,
  ACTION_RECENT_CLEAR,
} from "@/testids";

import { RegexPanel } from "./RegexPanel";
import { NumericPanel } from "./NumericPanel";
import { DeleteConfirmDialog } from "@/components/dialogs/DeleteConfirmDialog";

// Sentinel for the Recent <select> — a jump-menu control, not a persistent
// value: picking a real entry fires onChange then the select is reset back
// to this placeholder. Distinct from any real pattern (recordRecentPattern
// never stores an empty/whitespace-only pattern).
const RECENT_SENTINEL = "__recent_none__";

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

  // Movable/resizable window geometry (#739, divergence D7) — the SAME
  // mechanism the full-res viewer and the Execute dialog use. The centered
  // `max-w-2xl` layout below stays the default until the user moves/resizes.
  const overlay = useOverlayGeometry("action", actionDialogOpen);

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

  // Recent patterns (#741 sub-item B) — local dialog state (mirrors how
  // RegexPanel keeps its own Simple-mode local state), loaded fresh on every
  // dialog open and written after a successful non-numeric Apply in doApply
  // below. Kept out of the Zustand store since it's ephemeral UI state for
  // this one dialog, not app-wide data other components need to read.
  const [recentPatterns, setRecentPatterns] = useState<RecentPatternEntry[]>([]);

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
  // Recent patterns — read on dialog open (#741 sub-item B)
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (!actionDialogOpen) return;
    let cancelled = false;
    void getSettings()
      .then((settings) => {
        if (cancelled) return;
        setRecentPatterns(parseRecentPatterns(settings[RECENT_PATTERNS_KEY]));
      })
      .catch(() => {
        // Settings unreadable — Recent starts empty; the dialog is fully usable.
      });
    return () => {
      cancelled = true;
    };
  }, [actionDialogOpen]);

  // ---------------------------------------------------------------------------
  // Apply handler
  // ---------------------------------------------------------------------------

  const doApply = useCallback(
    async (opts: { forceLocked?: boolean; skipLocked?: boolean }) => {
      try {
        await applyBulkDecide(opts);
        // Recent patterns (#741 sub-item B): record AFTER a successful apply
        // (mirrors Qt's A9/#347 ordering — a pattern that never actually
        // applied should never enter Recent). Numeric fields are excluded:
        // their __cmp__:/__top_n__: pseudo-patterns would be meaningless in
        // the Recent list, which only ever re-fills the regex/simple input
        // (mirrors Qt's `if not self._field_panel_is_numeric()` gate).
        if (!isNumeric) {
          setRecentPatterns((prev) => {
            const next = recordRecentPattern(prev, field, pattern);
            if (next !== prev) {
              void patchSettings({ [RECENT_PATTERNS_KEY]: next });
            }
            return next;
          });
        }
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
    [applyBulkDecide, closeActionDialog, isNumeric, field, pattern],
  );

  // Apply button: "delete" opens the confirm gate; every other action applies
  // directly. Depends on doApply (not just action) so it never closes over a
  // stale pattern — doApply's identity changes with field/pattern, so an
  // [action]-only dep list recorded a stale/empty Recent entry (#741 fix).
  const handleApply = useCallback(async () => {
    if (action === "delete") {
      setDeleteConfirmOpen(true);
      return;
    }
    await doApply({});
  }, [action, doApply]);

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
  // Recent patterns handlers (#741 sub-item B)
  // ---------------------------------------------------------------------------

  const visibleRecent = visibleRecentPatterns(recentPatterns, field);

  const handleRecentSelect = useCallback(
    (e: ChangeEvent<HTMLSelectElement>) => {
      const value = e.target.value;
      if (value === RECENT_SENTINEL) return;
      setActionPattern(value);
    },
    [setActionPattern]
  );

  const handleClearRecent = useCallback(() => {
    setRecentPatterns([]);
    void patchSettings({ [RECENT_PATTERNS_KEY]: [] });
  }, []);

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

  // Localised field label + pattern-aware summary for the delete-confirm
  // dialog (#741 sub-item C). Pure function of (fieldDisplay, pattern) — see
  // lib/patternSummary.ts doc comment for why this can't go stale.
  const fieldEntry = FIELDS.find((f) => f.label === field);
  const fieldDisplay = t(
    `web.column.${fieldEntry?.key ?? field}`,
    fieldEntry?.label ?? field
  );
  const patternSummary = buildPatternSummary(fieldDisplay, pattern, t);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <>
      <DeleteConfirmDialog
        open={deleteConfirmOpen}
        deleteCount={previewMatched >= 0 ? previewMatched : 0}
        patternSummary={patternSummary}
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
          ref={overlay.containerRef}
          data-testid={ACTION_DIALOG}
          // Classes are the DEFAULT layout (unchanged first open); once moved
          // or resized, overlay.style pins explicit left/top/width/height.
          className="max-w-2xl w-full"
          style={overlay.style}
          onInteractOutside={(e) => {
            // #764 (twin of #721) — never dismiss the Action dialog via an
            // interaction OUTSIDE it. The inline lock-confirm (rendered just
            // above) is a Radix modal portaled at App level, so it is a DOM
            // SIBLING of this dialog, not a child; cancelling it fires a
            // focus-return `focusin` and a post-unmount `pointerdown` on this
            // layer that an `actionRunning`-only guard let through, cascade-
            // closing the Action dialog and discarding the user's pattern.
            // Unconditional preventDefault is race-free (a lockConfirmOpen/state
            // guard loses the race against the child's synchronous
            // setLockConfirmOpen(false)). Escape is left guarding only
            // actionRunning below: Radix routes Escape to the topmost layer
            // only, so an idle Action dialog still closes on Escape (s50).
            e.preventDefault();
          }}
          onEscapeKeyDown={(e) => {
            if (actionRunning) e.preventDefault();
          }}
        >
          {/* Header doubles as the window drag bar (#739) */}
          <DialogHeader
            data-testid={ACTION_TITLE_BAR}
            className="cursor-move select-none"
            onMouseDown={overlay.onMoveStart}
          >
            <DialogTitle>
              {t("web.action_dialog.title", "Set Action by Field")}
            </DialogTitle>
          </DialogHeader>

          {/* Recent patterns (#741 sub-item B) — mirrors Qt's Recent row
              positioned above the Field combo. A jump-menu <select>: picking
              a real entry fills the pattern input then resets back to the
              placeholder option. Filtered to entries recorded for the
              current field (plus legacy null-field entries, which apply to
              any field) via visibleRecentPatterns. */}
          <div className="flex items-center gap-2 mt-2">
            <label className="text-sm font-medium w-20 flex-shrink-0">
              {t("web.action_dialog.recent_button", "Recent:")}
            </label>
            <select
              data-testid={ACTION_RECENT_SELECT}
              value={RECENT_SENTINEL}
              onChange={handleRecentSelect}
              className="rounded border border-neutral-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400 flex-1"
            >
              <option value={RECENT_SENTINEL} disabled>
                {visibleRecent.length === 0
                  ? t("web.action_dialog.recent_empty", "(no recent patterns)")
                  : t("web.action_dialog.recent_button", "Recent:")}
              </option>
              {visibleRecent.map(([, pat], i) => (
                <option key={`${pat}-${i}`} value={pat}>
                  {pat}
                </option>
              ))}
            </select>
            {visibleRecent.length > 0 && (
              <button
                type="button"
                data-testid={ACTION_RECENT_CLEAR}
                onClick={handleClearRecent}
                className="text-xs text-neutral-500 underline hover:text-neutral-700 flex-shrink-0"
              >
                {t("web.action_dialog.recent_clear", "Clear")}
              </button>
            )}
          </div>

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
                hasGroups={totalGroups > 0}
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
              disabled={actionRunning || manifestPath === null}
            >
              {actionRunning
                ? t("web.action_dialog.applying", "Applying…")
                : t("web.action_dialog.apply_button", "Apply")}
            </Button>
          </DialogFooter>

          {/* Resize grip (#739) */}
          <OverlayResizeHandle
            data-testid={ACTION_RESIZE_HANDLE}
            onMouseDown={overlay.onResizeStart}
            className="text-neutral-500"
            label={t("web.overlay.resize", "Resize window")}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}
