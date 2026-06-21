// LockConfirmDialog — shown when POST /api/execute or /api/remove returns
// a 409 locked_paths conflict.
//
// Driven entirely by store.execute.lockConflict:
//   - null  → dialog hidden (not mounted)
//   - set   → dialog open; op tells us whether the original call was "execute"
//             or "remove".
//
// Three outcomes:
//   Unlock & Apply  → re-run the original op with forceLocked:true
//   Unlocked Only   → re-run the original op with the locked paths excluded
//   Cancel          → clear lockConflict, do nothing
//
// Body copy is context-sensitive:
//   op === "execute" → "About to DELETE N locked files"  (IMMEDIATE danger)
//   op === "remove"  → "N locked files will be skipped or force-unlocked"
//                      (DEFERRED — remove doesn't delete)

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
  const set = useAppStore.setState;

  const isOpen = lockConflict !== null;
  const lockedPaths = lockConflict?.paths ?? [];
  const op = lockConflict?.op ?? "execute";
  const n = lockedPaths.length;

  function clearConflict() {
    set((s) => ({
      ...s,
      execute: { ...s.execute, lockConflict: null },
    }));
  }

  function handleUnlockApply() {
    // Capture the ORIGINAL scope before clearConflict() nulls the store field.
    // "Unlock & Apply" must re-run the SAME scope the user originally acted on
    // (force-unlocking the locked rows), NOT broaden or narrow it:
    //   - execute: re-running unscoped would execute the WHOLE manifest, deleting
    //     files outside a "Execute selected" scope (over-deletion).
    //   - remove: re-running with only lockedPaths would drop the unlocked files
    //     the user asked to remove (silent under-action).
    const original = lockConflict?.originalPaths ?? [];
    clearConflict();
    if (op === "execute") {
      void executeDecisions({ scopePaths: original, forceLocked: true });
    } else {
      void removeFromList(original, true);
    }
  }

  function handleUnlockedOnly() {
    // Capture before clearConflict() nulls the store field.
    const lockedSet = new Set(lockedPaths);
    const original = lockConflict?.originalPaths ?? [];
    // Filter the original scope to exclude locked paths, then re-run.
    const unlockedPaths = original.filter((p) => !lockedSet.has(p));
    clearConflict();
    if (op === "execute") {
      // Re-run execute with only the unlocked subset in scope.
      void executeDecisions({ scopePaths: unlockedPaths });
    } else {
      // Re-run remove with only the unlocked subset.
      void removeFromList(unlockedPaths, false);
    }
  }

  function handleCancel() {
    clearConflict();
  }

  const isExecuteOp = op === "execute";
  const title = isExecuteOp
    ? "Locked files in delete scope"
    : "Locked files in remove scope";
  const description = isExecuteOp
    ? `${n} locked ${n === 1 ? "file" : "files"} ${n === 1 ? "is" : "are"} about to be permanently DELETED. Unlock and proceed, or skip locked files only.`
    : `${n} locked ${n === 1 ? "file" : "files"} ${n === 1 ? "is" : "are"} in the remove list. Unlock and remove, or remove unlocked files only.`;

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
            Cancel
          </Button>
          <Button
            variant="outline"
            data-testid={LOCK_CONFIRM_BTN_UNLOCKED_ONLY}
            onClick={handleUnlockedOnly}
          >
            Unlocked only
          </Button>
          <Button
            variant="destructive"
            data-testid={LOCK_CONFIRM_BTN_UNLOCK_APPLY}
            onClick={handleUnlockApply}
          >
            Unlock &amp; Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
