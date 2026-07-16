// LockConfirmDialog — shown when POST /api/execute, POST /api/remove,
// PATCH /api/decision (#733), or POST /api/action/apply-best-copy (#744)
// returns a 409 locked_paths conflict.
//
// Driven entirely by store.execute.lockConflict:
//   - null  → dialog hidden (not mounted)
//   - set   → dialog open; op tells us whether the original call was
//             "execute", "remove", "prune", "decision", or "apply-best-copy".
//
// Three outcomes:
//   Unlock & Apply  → re-run the original op with forceLocked:true
//   Unlocked Only   → re-run the original op with the locked paths excluded
//   Cancel          → clear lockConflict, do nothing
//
// Body copy is context-sensitive:
//   op === "execute"  → "About to DELETE N locked files (Recycle Bin)"
//                       (IMMEDIATE danger — deletion is via send2trash, not
//                       permanent; #wording-audit corrected the earlier
//                       "permanently DELETED" claim to match
//                       DeleteConfirmDialog's accurate recycle-bin wording)
//   op === "remove"   → "N locked files will be skipped or force-unlocked"
//                       (DEFERRED — remove doesn't delete)
//   op === "prune"    → "N locked singleton groups would be pruned" (#686 D6
//                       gate; verdicts route to resolvePruneLock, NOT a
//                       re-run of an op)
//   op === "decision" → "N locked rows would be affected" (DEFERRED — Qt
//                       #417 parity: setting a decision doesn't delete
//                       anything, it only queues it for a later Execute).
//                       Unlock & Apply is NOT destructive for this op.
//   op === "apply-best-copy" → "N locked rows would be affected" (DEFERRED,
//                       same reasoning as "decision" — the write only queues
//                       keep/delete decisions). Re-runs applyBestCopy(groupNumber).

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useAppStore } from "@/store/useAppStore";
import { useT } from "@/i18n/useT";
import {
  LOCK_CONFIRM_BTN_CANCEL,
  LOCK_CONFIRM_BTN_UNLOCK_APPLY,
  LOCK_CONFIRM_BTN_UNLOCKED_ONLY,
  LOCK_CONFIRM_DIALOG,
} from "@/testids";

export function LockConfirmDialog() {
  const lockConflict = useAppStore((s) => s.execute.lockConflict);
  const executeDecisions = useAppStore((s) => s.executeDecisions);
  const removeFromList = useAppStore((s) => s.removeFromList);
  const setDecisions = useAppStore((s) => s.setDecisions);
  const resolvePruneLock = useAppStore((s) => s.resolvePruneLock);
  const applyBestCopy = useAppStore((s) => s.applyBestCopy);
  const set = useAppStore.setState;
  const t = useT();

  const isOpen = lockConflict !== null;
  const lockedPaths = lockConflict?.paths ?? [];
  const op = lockConflict?.op ?? "execute";
  const n = lockedPaths.length;
  const originalPathsLen = lockConflict?.originalPaths?.length ?? 0;

  function clearConflict() {
    set((s) => ({
      ...s,
      execute: { ...s.execute, lockConflict: null },
    }));
  }

  function handleUnlockApply() {
    // Prune-context gate (#686): resolvePruneLock reads lockConflict.paths then
    // clears it — must NOT pre-clear here, or it would see an empty locked set.
    if (op === "prune") {
      void resolvePruneLock("unlock-apply");
      return;
    }
    // Capture the ORIGINAL scope before clearConflict() nulls the store field.
    // "Unlock & Apply" must re-run the SAME scope the user originally acted on
    // (force-unlocking the locked rows), NOT broaden or narrow it:
    //   - execute: re-running unscoped would execute the WHOLE manifest, deleting
    //     files outside a "Execute selected" scope (over-deletion).
    //   - remove: re-running with only lockedPaths would drop the unlocked files
    //     the user asked to remove (silent under-action).
    //   - decision: re-running with only lockedPaths would leave the unlocked
    //     rows' decision unset (silent under-action), same reasoning as remove.
    //   - apply-best-copy: re-runs by groupNumber (not a path list) — the
    //     service re-derives the keeper/delete-target write-set itself.
    const original = lockConflict?.originalPaths ?? [];
    const pendingDecision = lockConflict?.pendingDecision ?? "";
    const groupNumber = lockConflict?.groupNumber;
    clearConflict();
    if (op === "execute") {
      void executeDecisions({ scopePaths: original, forceLocked: true });
    } else if (op === "decision") {
      void setDecisions(original, pendingDecision, { forceLocked: true });
    } else if (op === "apply-best-copy") {
      if (groupNumber !== undefined) {
        void applyBestCopy(groupNumber, { forceLocked: true });
      }
    } else {
      void removeFromList(original, true);
    }
  }

  function handleUnlockedOnly() {
    if (op === "prune") {
      // Hold the locked singletons; continue pruning the unlocked buckets.
      void resolvePruneLock("unlocked-only");
      return;
    }
    // Capture before clearConflict() nulls the store field.
    const lockedSet = new Set(lockedPaths);
    const original = lockConflict?.originalPaths ?? [];
    const pendingDecision = lockConflict?.pendingDecision ?? "";
    const groupNumber = lockConflict?.groupNumber;
    // Filter the original scope to exclude locked paths, then re-run.
    const unlockedPaths = original.filter((p) => !lockedSet.has(p));
    clearConflict();
    if (op === "execute") {
      // Re-run execute with only the unlocked subset in scope.
      void executeDecisions({ scopePaths: unlockedPaths });
    } else if (op === "decision") {
      // Re-run setDecisions with only the unlocked subset; nothing to do if
      // every original path was locked.
      if (unlockedPaths.length > 0) {
        void setDecisions(unlockedPaths, pendingDecision);
      }
    } else if (op === "apply-best-copy") {
      // Re-run by groupNumber with skipLocked:true — the service narrows the
      // write to the unlocked subset itself.
      if (groupNumber !== undefined) {
        void applyBestCopy(groupNumber, { skipLocked: true });
      }
    } else {
      // Re-run remove with only the unlocked subset.
      void removeFromList(unlockedPaths, false);
    }
  }

  function handleCancel() {
    if (op === "prune") {
      // Cancel does NOT abort the prune (Qt parity) — it holds the locked
      // singletons and still offers/sweeps the unlocked buckets.
      void resolvePruneLock("cancel");
      return;
    }
    clearConflict();
  }

  const title =
    op === "execute"
      ? t("web.lock_confirm.title_execute", "Locked files in delete scope")
      : op === "remove"
        ? t("web.lock_confirm.title_remove", "Locked files in remove scope")
        : op === "decision"
          ? t("web.lock_confirm.title_decision", "Locked rows affected")
          : op === "apply-best-copy"
            ? t("web.lock_confirm.title_apply_best_copy", "Locked rows in this group")
            : t("web.lock_confirm.title_prune", "Locked singleton groups");
  const description =
    op === "execute"
      ? t(
          "web.lock_confirm.body_execute",
          "{n} locked file(s) are about to be deleted (sent to the Recycle Bin). Unlock and proceed, or skip locked files only.",
          { n }
        )
      : op === "remove"
        ? t(
            "web.lock_confirm.body_remove",
            "{n} locked file(s) are in the remove list. Unlock and remove, or remove unlocked files only.",
            { n }
          )
        : op === "decision"
          ? t(
              "web.lock_confirm.body_decision",
              "Setting this decision would change {originalPathsLen} row(s); {n} are locked. Nothing is deleted yet — this only queues the decision. Unlock and set all, set only the unlocked rows, or cancel.",
              { originalPathsLen, n }
            )
          : op === "apply-best-copy"
            ? t(
                "web.lock_confirm.body_apply_best_copy",
                "Applying best-copy to this group would affect rows that include {n} locked row(s). Nothing is deleted yet — this only queues keep/delete decisions. Unlock and apply to all, apply to the unlocked rows only, or cancel.",
                { n }
              )
            : t(
                "web.lock_confirm.body_prune",
                "{n} locked singleton group(s) would be pruned. Unlock and prune them, or keep them in the list.",
                { n }
              );

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && handleCancel()}>
      <DialogContent data-testid={LOCK_CONFIRM_DIALOG}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {lockedPaths.length > 0 && (
          <ul className="max-h-40 overflow-y-auto text-xs text-neutral-600 space-y-0.5 my-2">
            {lockedPaths.map((p) => (
              <li key={p} className="truncate font-mono">
                {p}
              </li>
            ))}
          </ul>
        )}
        <DialogFooter className="gap-2">
          <Button
            variant="outline"
            data-testid={LOCK_CONFIRM_BTN_CANCEL}
            onClick={handleCancel}
          >
            {t("web.lock_confirm.cancel", "Cancel")}
          </Button>
          <Button
            variant="outline"
            data-testid={LOCK_CONFIRM_BTN_UNLOCKED_ONLY}
            onClick={handleUnlockedOnly}
          >
            {t("web.lock_confirm.unlocked_only", "Unlocked only")}
          </Button>
          <Button
            // "decision" / "apply-best-copy" are not destructive — nothing is
            // deleted, they only queue decisions (Qt #417 parity). Every
            // other op is.
            variant={
              op === "decision" || op === "apply-best-copy" ? "default" : "destructive"
            }
            data-testid={LOCK_CONFIRM_BTN_UNLOCK_APPLY}
            onClick={handleUnlockApply}
          >
            {t("web.lock_confirm.unlock_apply", "Unlock & Apply")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
