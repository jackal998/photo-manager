// App — root shell.
//
// SSE subscription lives HERE (single mount point) so the EventSource
// survives ScanDialog open/close. ScanDialog does NOT subscribe — it only
// reads scan state from the store. See ScanDialog.tsx header comment.

import { useState, useCallback } from "react";

import { cn } from "./lib/utils";
import { useAppStore } from "./store/useAppStore";
import { useScanSSE } from "./hooks/useScanSSE";

import { ScanDialog } from "./components/ScanDialog";
import { SettingsDialog } from "./components/SettingsDialog";
import { ResultTree } from "./components/ResultTree";
import type { ContextMenuTarget } from "./components/ResultTree";
import { PreviewPane } from "./components/PreviewPane";
import { FullResViewer } from "./components/FullResViewer";
import { ExecuteDialog } from "./components/execute/ExecuteDialog";
import { ContextMenu } from "./components/ContextMenu";
import { LockConfirmDialog } from "./components/dialogs/LockConfirmDialog";
import { PruneConfirmDialog } from "./components/dialogs/PruneConfirmDialog";

import {
  MAIN_EXECUTE_BUTTON,
  MAIN_LANG_TOGGLE,
  MAIN_MANIFEST_INPUT,
  MAIN_MANIFEST_OPEN,
  MAIN_SCAN_BUTTON,
  MAIN_SETTINGS_BUTTON,
  MAIN_STATUS_BAR,
} from "./testids";

// ---------------------------------------------------------------------------
// Context menu state
// ---------------------------------------------------------------------------

interface ContextMenuState {
  open: boolean;
  x: number;
  y: number;
  filePath: string;
  isLocked: boolean;
}

const CLOSED_MENU: ContextMenuState = {
  open: false,
  x: 0,
  y: 0,
  filePath: "",
  isLocked: false,
};

export default function App() {
  // ---------------------------------------------------------------------------
  // Dialog open states
  // ---------------------------------------------------------------------------

  const [scanOpen, setScanOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  // ---------------------------------------------------------------------------
  // Context menu
  // ---------------------------------------------------------------------------

  const [contextMenu, setContextMenu] = useState<ContextMenuState>(CLOSED_MENU);

  const handleContextMenu = useCallback((target: ContextMenuTarget) => {
    setContextMenu({
      open: true,
      x: target.x,
      y: target.y,
      filePath: target.filePath,
      isLocked: target.isLocked,
    });
  }, []);

  const handleContextMenuClose = useCallback(() => {
    setContextMenu(CLOSED_MENU);
  }, []);

  // ---------------------------------------------------------------------------
  // Manifest open control
  // ---------------------------------------------------------------------------

  const [manifestInputValue, setManifestInputValue] = useState("");
  const loadManifest = useAppStore((s) => s.loadManifest);
  const openExecuteDialog = useAppStore((s) => s.openExecuteDialog);

  const handleManifestOpen = useCallback(() => {
    const path = manifestInputValue.trim();
    if (path === "") return;
    void loadManifest(path);
  }, [manifestInputValue, loadManifest]);

  // ---------------------------------------------------------------------------
  // SSE subscription — single mount at App level
  // ---------------------------------------------------------------------------

  const taskId = useAppStore((s) => s.scan.taskId);
  useScanSSE(taskId);

  // ---------------------------------------------------------------------------
  // Status bar text
  // ---------------------------------------------------------------------------

  const scan = useAppStore((s) => s.scan);
  const manifest = useAppStore((s) => s.manifest);

  let statusText: string;
  if (manifest.path !== null) {
    statusText = `${manifest.totalGroups} groups · ${manifest.totalFiles} files`;
  } else if (manifest.loading) {
    statusText = "Loading manifest…";
  } else if (scan.status === "running") {
    const stage = scan.stageName !== "" ? ` — ${scan.stageName}` : "";
    statusText = `Scanning${stage}`;
  } else if (scan.status === "failed" && scan.error !== null) {
    statusText = `Scan failed: ${scan.error}`;
  } else {
    statusText = "Ready";
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className={cn("min-h-screen flex flex-col")}>
      {/* ------------------------------------------------------------------ */}
      {/* Header toolbar                                                       */}
      {/* ------------------------------------------------------------------ */}
      <header className="flex items-center gap-2 border-b px-4 py-2 flex-wrap">
        <button
          data-testid={MAIN_SCAN_BUTTON}
          className="px-3 py-1 rounded border text-sm hover:bg-neutral-100"
          onClick={() => setScanOpen(true)}
        >
          Scan
        </button>

        <button
          data-testid={MAIN_EXECUTE_BUTTON}
          className="px-3 py-1 rounded border text-sm hover:bg-neutral-100"
          onClick={openExecuteDialog}
        >
          Execute
        </button>

        <button
          data-testid={MAIN_LANG_TOGGLE}
          className="px-3 py-1 rounded border text-sm hover:bg-neutral-100"
        >
          EN
        </button>

        <button
          data-testid={MAIN_SETTINGS_BUTTON}
          className="px-3 py-1 rounded border text-sm hover:bg-neutral-100"
          onClick={() => setSettingsOpen(true)}
        >
          Settings
        </button>

        {/* Manifest open control */}
        <span className="flex-1" />
        <input
          data-testid={MAIN_MANIFEST_INPUT}
          type="text"
          value={manifestInputValue}
          onChange={(e) => setManifestInputValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleManifestOpen();
          }}
          placeholder="Path to manifest .db…"
          className="rounded border border-neutral-300 px-3 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400 w-64"
          aria-label="Manifest path"
        />
        <button
          data-testid={MAIN_MANIFEST_OPEN}
          className="px-3 py-1 rounded border text-sm hover:bg-neutral-100"
          onClick={handleManifestOpen}
        >
          Open
        </button>
      </header>

      {/* ------------------------------------------------------------------ */}
      {/* Main — result tree + preview pane side by side                       */}
      {/* ------------------------------------------------------------------ */}
      <main className="flex-1 overflow-hidden flex flex-row">
        {/* Result tree takes remaining width */}
        <div className="flex-1 min-w-0 overflow-hidden">
          <ResultTree onContextMenu={handleContextMenu} />
        </div>
        {/* Preview pane — fixed 280px right column */}
        <div className="w-72 flex-shrink-0 overflow-hidden border-l border-neutral-200">
          <PreviewPane />
        </div>
      </main>

      {/* ------------------------------------------------------------------ */}
      {/* Footer status bar                                                    */}
      {/* ------------------------------------------------------------------ */}
      <footer className="border-t">
        <p
          data-testid={MAIN_STATUS_BAR}
          className="px-4 py-1 text-sm text-neutral-500"
        >
          {statusText}
        </p>
      </footer>

      {/* ------------------------------------------------------------------ */}
      {/* Dialogs                                                              */}
      {/* ------------------------------------------------------------------ */}
      <ScanDialog open={scanOpen} onOpenChange={setScanOpen} />
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
      <ExecuteDialog />
      <FullResViewer />
      <LockConfirmDialog />
      <PruneConfirmDialog />

      {/* ------------------------------------------------------------------ */}
      {/* Context menu — rendered at App level so it escapes scroll containers */}
      {/* ------------------------------------------------------------------ */}
      {contextMenu.open && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          filePath={contextMenu.filePath}
          isLocked={contextMenu.isLocked}
          onClose={handleContextMenuClose}
        />
      )}
    </div>
  );
}
