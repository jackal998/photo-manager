// SettingsDialog — minimal dialog for the three server-allowlisted settings.
//
// Controlled by parent (open/onOpenChange). On open it calls store.loadSettings()
// to get current values. Save calls store.saveSettings(updates) then closes.

import { useState, useEffect, useCallback } from "react";

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { useAppStore } from "@/store/useAppStore";
import { normalizePrunePref, type PrunePref } from "@/lib/prune";
import {
  DLGE_SETTINGS_CANCEL,
  DLGE_SETTINGS_DIALOG,
  DLGE_SETTINGS_PRUNE_SELECT,
  DLGE_SETTINGS_SAVE,
} from "@/testids";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SettingsDialog({ open, onOpenChange }: SettingsDialogProps) {
  const settings = useAppStore((s) => s.settings);
  const loadSettings = useAppStore((s) => s.loadSettings);
  const saveSettings = useAppStore((s) => s.saveSettings);

  // Local editable copies of the three keys.
  const [prunePref, setPrunePref] = useState<PrunePref>("ask");
  const [autotuneReadKnee, setAutotuneReadKnee] = useState<boolean>(false);
  // sorting.defaults is unknown shape — edit as a JSON string.
  const [sortingDefaultsRaw, setSortingDefaultsRaw] = useState<string>("");
  const [sortingError, setSortingError] = useState<string | null>(null);

  // Load settings when dialog opens.
  useEffect(() => {
    if (open) {
      void loadSettings();
    }
  }, [open, loadSettings]);

  // Sync local state when settings values arrive from the store.
  useEffect(() => {
    if (!open) return;
    const v = settings.values;
    setPrunePref(normalizePrunePref(v["ui.prune_singletons"]));
    setAutotuneReadKnee(Boolean(v["ui.scan_dialog.autotune_read_knee"]));
    setSortingDefaultsRaw(
      v["sorting.defaults"] !== null
        ? JSON.stringify(v["sorting.defaults"], null, 2)
        : ""
    );
    setSortingError(null);
  }, [open, settings.values]);

  const handleSave = useCallback(() => {
    // Validate sorting.defaults JSON.
    let sortingDefaults: unknown = null;
    if (sortingDefaultsRaw.trim() !== "") {
      try {
        sortingDefaults = JSON.parse(sortingDefaultsRaw) as unknown;
      } catch {
        setSortingError("Invalid JSON for sorting.defaults");
        return;
      }
    }
    setSortingError(null);

    const updates: Partial<{
      "sorting.defaults": unknown;
      "ui.prune_singletons": unknown;
      "ui.scan_dialog.autotune_read_knee": unknown;
    }> = {
      "sorting.defaults": sortingDefaults,
      "ui.prune_singletons": prunePref,
      "ui.scan_dialog.autotune_read_knee": autotuneReadKnee,
    };

    void saveSettings(updates).then(() => {
      onOpenChange(false);
    });
  }, [sortingDefaultsRaw, prunePref, autotuneReadKnee, saveSettings, onOpenChange]);

  const handleCancel = useCallback(() => {
    onOpenChange(false);
  }, [onOpenChange]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid={DLGE_SETTINGS_DIALOG} className="max-w-md">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
        </DialogHeader>

        <div className="mt-4 flex flex-col gap-4">
          {/* ui.prune_singletons — 3-value enum (#686) */}
          <label className="flex items-center gap-2 text-sm">
            <span className="min-w-0">Prune singletons (ui.prune_singletons)</span>
            <select
              data-testid={DLGE_SETTINGS_PRUNE_SELECT}
              value={prunePref}
              onChange={(e) => setPrunePref(normalizePrunePref(e.target.value))}
              className="rounded border border-neutral-300 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400"
            >
              <option value="ask">Ask each time</option>
              <option value="always">Always prune</option>
              <option value="never">Never prune</option>
            </select>
          </label>

          {/* ui.scan_dialog.autotune_read_knee */}
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={autotuneReadKnee}
              onCheckedChange={(checked) =>
                setAutotuneReadKnee(checked === true)
              }
            />
            Auto-tune read knee (ui.scan_dialog.autotune_read_knee)
          </label>

          {/* sorting.defaults — raw JSON textarea */}
          <div className="flex flex-col gap-1">
            <label
              htmlFor="settings-sorting-defaults"
              className="text-sm font-medium text-neutral-700"
            >
              Sorting defaults (sorting.defaults — JSON)
            </label>
            <textarea
              id="settings-sorting-defaults"
              rows={4}
              value={sortingDefaultsRaw}
              onChange={(e) => {
                setSortingDefaultsRaw(e.target.value);
                setSortingError(null);
              }}
              placeholder="null"
              className="rounded border border-neutral-300 px-3 py-1.5 font-mono text-xs focus:outline-none focus:ring-2 focus:ring-neutral-400"
            />
            {sortingError !== null && (
              <p role="alert" className="text-xs text-red-600">
                {sortingError}
              </p>
            )}
          </div>
        </div>

        <DialogFooter className="mt-6">
          <Button
            type="button"
            variant="outline"
            data-testid={DLGE_SETTINGS_CANCEL}
            onClick={handleCancel}
          >
            Cancel
          </Button>
          <Button
            type="button"
            data-testid={DLGE_SETTINGS_SAVE}
            disabled={settings.loading}
            onClick={handleSave}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
