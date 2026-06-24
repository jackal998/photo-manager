// PruneConfirmDialog (#686) — offered on the "ask" path after a destructive op
// (execute / finalizing remove) leaves singleton groups. The web port of Qt's
// SingletonPruneConfirmDialog.
//
// Driven by store.execute.prunePrompt:
//   - null  → hidden
//   - set   → open; .plain / .actioned are the two unlocked buckets, and
//             .lockedToPrune are lock-confirmed locked singletons that prune
//             REGARDLESS of the verdict (the Qt to_prune.extend(prunable_locked) tail).
//
// Three layouts mirror the desktop:
//   - mixed (plain>0 AND actioned>0): the actioned bucket is opt-in via a checkbox.
//   - actioned-only (plain==0): Remove itself opts the actioned bucket in.
//   - plain-only (actioned==0): the classic remove-singletons confirm.
//
// "Don't ask again" flips ui.prune_singletons → "always" (on Remove) or
// "never" (on Keep all). The actioned opt-in is per-event, NOT remembered.

import { useEffect, useRef, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { useAppStore } from "@/store/useAppStore";
import { computePruneSet } from "@/lib/prune";
import {
  PRUNE_BTN_KEEP,
  PRUNE_BTN_REMOVE,
  PRUNE_CONFIRM_DIALOG,
  PRUNE_INCLUDE_ACTIONED,
  PRUNE_REMEMBER,
} from "@/testids";

export function PruneConfirmDialog() {
  const prunePrompt = useAppStore((s) => s.execute.prunePrompt);
  const applyPrune = useAppStore((s) => s.applyPrune);
  const saveSettings = useAppStore((s) => s.saveSettings);

  const isOpen = prunePrompt !== null;
  const plain = prunePrompt?.plain ?? [];
  const actioned = prunePrompt?.actioned ?? [];
  const lockedToPrune = prunePrompt?.lockedToPrune ?? [];

  const isMixed = plain.length > 0 && actioned.length > 0;
  // Remove-button count reflects the dominant bucket (plain if any, else actioned),
  // matching Qt's `remove_count = count_plain if count_plain > 0 else count_actioned`.
  const removeCount = plain.length > 0 ? plain.length : actioned.length;

  // Local, per-open state. Reset each time the dialog opens so a prior event's
  // opt-in / remember choice never leaks into the next (Qt builds a fresh dialog).
  const [includeActioned, setIncludeActioned] = useState(false);
  const [remember, setRemember] = useState(false);
  // Guards the Radix onOpenChange re-entry: handleRemove/handleKeep call
  // applyPrune, which nulls prunePrompt → the dialog closes → onOpenChange(false)
  // would re-fire handleKeep (a redundant second applyPrune, only benign because
  // the explicit-paths prune is idempotent on outcome=''). The flag suppresses
  // that, so the only onOpenChange-driven Keep is a genuine Esc/overlay dismiss.
  const actedRef = useRef(false);
  useEffect(() => {
    if (isOpen) {
      setIncludeActioned(false);
      setRemember(false);
      actedRef.current = false;
    }
  }, [isOpen]);

  function handleRemove() {
    actedRef.current = true;
    const prunePlain = plain.length > 0;
    // Mixed: the checkbox decides. Actioned-only: Remove itself opts in.
    // Plain-only: actioned is empty so this is false regardless.
    const pruneActioned = isMixed ? includeActioned : actioned.length > 0;
    const toPrune = computePruneSet(
      { plain, actioned },
      { prunePlain, pruneActioned, lockedToPrune }
    );
    if (remember) {
      void saveSettings({ "ui.prune_singletons": "always" });
    }
    void applyPrune(toPrune);
  }

  function handleKeep() {
    actedRef.current = true;
    if (remember) {
      void saveSettings({ "ui.prune_singletons": "never" });
    }
    // Keep all still prunes the lock-confirmed locked singletons (Qt tail);
    // applyPrune([]) just closes the dialog when there are none.
    void applyPrune(lockedToPrune);
  }

  const description = isMixed
    ? `${plain.length} singleton group${plain.length === 1 ? "" : "s"} ` +
      `${plain.length === 1 ? "has" : "have"} only one file left, and ` +
      `${actioned.length} more carr${actioned.length === 1 ? "ies" : "y"} an ` +
      `un-executed delete/ignore action. Remove the plain singleton` +
      `${plain.length === 1 ? "" : "s"} from the list?`
    : actioned.length > 0
      ? `${actioned.length} singleton group${actioned.length === 1 ? "" : "s"} ` +
        `with an un-executed delete/ignore action remain. Remove ` +
        `${actioned.length === 1 ? "it" : "them"} from the list?`
      : `${plain.length} singleton group${plain.length === 1 ? "" : "s"} now ` +
        `${plain.length === 1 ? "has" : "have"} only one file remaining. Remove ` +
        `${plain.length === 1 ? "this singleton group" : "these singleton groups"} ` +
        `from the list?`;

  const candidates = [...plain, ...actioned];

  return (
    <Dialog
      open={isOpen}
      onOpenChange={(open) => {
        // Only a genuine dismiss (Esc / overlay) maps to Keep; a close caused by
        // our own applyPrune (actedRef set) must NOT re-fire it.
        if (!open && !actedRef.current) handleKeep();
      }}
    >
      <DialogContent data-testid={PRUNE_CONFIRM_DIALOG}>
        <DialogHeader>
          <DialogTitle>Prune singleton groups?</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
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

        {/* Actioned-bucket opt-in — mixed layout only (Qt parity). */}
        {isMixed && (
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              data-testid={PRUNE_INCLUDE_ACTIONED}
              checked={includeActioned}
              onCheckedChange={(checked) => setIncludeActioned(checked === true)}
            />
            Also remove {actioned.length} actioned singleton
            {actioned.length === 1 ? "" : "s"}
          </label>
        )}

        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            data-testid={PRUNE_REMEMBER}
            checked={remember}
            onCheckedChange={(checked) => setRemember(checked === true)}
          />
          Don&apos;t ask again
        </label>

        <DialogFooter className="gap-2">
          <Button
            variant="outline"
            data-testid={PRUNE_BTN_KEEP}
            onClick={handleKeep}
          >
            Keep all
          </Button>
          <Button
            variant="default"
            data-testid={PRUNE_BTN_REMOVE}
            onClick={handleRemove}
          >
            Remove {removeCount}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
