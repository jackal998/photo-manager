// PruneConfirmDialog — offered after execute leaves singleton groups
// (groups where all files have been actioned and only one remains).
//
// Driven by store.execute.prunePrompt:
//   - null  → hidden
//   - set   → open; .candidates lists the singleton paths
//
// Confirm → store.pruneSingletons()
// Cancel  → clear prunePrompt only (keep singletons in the list)

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
import { PRUNE_CONFIRM_DIALOG } from "@/testids";

export function PruneConfirmDialog() {
  const prunePrompt = useAppStore((s) => s.execute.prunePrompt);
  const pruneSingletons = useAppStore((s) => s.pruneSingletons);
  const set = useAppStore.setState;

  const isOpen = prunePrompt !== null;
  const candidates = prunePrompt?.candidates ?? [];
  const n = candidates.length;

  function clearPrompt() {
    set((s) => ({
      ...s,
      execute: { ...s.execute, prunePrompt: null },
    }));
  }

  function handleConfirm() {
    clearPrompt();
    void pruneSingletons();
  }

  function handleCancel() {
    clearPrompt();
  }

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && handleCancel()}>
      <DialogContent data-testid={PRUNE_CONFIRM_DIALOG}>
        <DialogHeader>
          <DialogTitle>Remove singleton groups?</DialogTitle>
          <DialogDescription>
            {n} {n === 1 ? "group" : "groups"} now{" "}
            {n === 1 ? "has" : "have"} only one file remaining after the
            execute. Remove{" "}
            {n === 1 ? "this singleton group" : "these singleton groups"} from
            the list?
          </DialogDescription>
        </DialogHeader>
        {candidates.length > 0 && (
          <ul className="max-h-40 overflow-y-auto text-xs text-neutral-600 space-y-0.5 my-2">
            {candidates.map((p) => (
              <li key={p} className="truncate font-mono">
                {p}
              </li>
            ))}
          </ul>
        )}
        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={handleCancel}>
            Keep them
          </Button>
          <Button variant="default" onClick={handleConfirm}>
            Remove singletons
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
