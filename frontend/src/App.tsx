// App — root shell.
//
// SSE subscription lives HERE (single mount point) so the EventSource
// survives ScanDialog open/close. ScanDialog does NOT subscribe — it only
// reads scan state from the store. See ScanDialog.tsx header comment.

import { useState, useCallback, useEffect } from "react";

import { cn } from "./lib/utils";
import { useAppStore } from "./store/useAppStore";
import { useScanSSE } from "./hooks/useScanSSE";
import { useI18nStore } from "./i18n/useI18nStore";
import { useT } from "./i18n/useT";

import { ScanDialog } from "./components/ScanDialog";
import { SettingsDialog } from "./components/SettingsDialog";
import { ResultTree } from "./components/ResultTree";
import type { ContextMenuTarget } from "./components/ResultTree";
import { PreviewPane } from "./components/PreviewPane";
import { FullResViewer } from "./components/FullResViewer";
import { ExecuteDialog } from "./components/execute/ExecuteDialog";
import { ActionDialog } from "./components/action/ActionDialog";
import { ContextMenu } from "./components/ContextMenu";
import { LockConfirmDialog } from "./components/dialogs/LockConfirmDialog";
import { PruneConfirmDialog } from "./components/dialogs/PruneConfirmDialog";
import { MenuBar } from "./components/MenuBar";
import { FsBrowser } from "./components/FsBrowser";

import {
  ACTION_MAIN_BUTTON,
  MAIN_EMPTY_STATE,
  MAIN_EMPTY_SCAN,
  MAIN_EMPTY_OPEN,
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
  targetPaths: string[];
}

const CLOSED_MENU: ContextMenuState = {
  open: false,
  x: 0,
  y: 0,
  filePath: "",
  isLocked: false,
  targetPaths: [],
};

export default function App() {
  // ---------------------------------------------------------------------------
  // i18n — initialise once on boot
  // ---------------------------------------------------------------------------

  const initI18n = useI18nStore((s) => s.initI18n);
  const setLocale = useI18nStore((s) => s.setLocale);
  const locale = useI18nStore((s) => s.locale);
  const t = useT();

  useEffect(() => {
    void initI18n();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    // Target-resolution rule (file-manager standard): when the right-clicked
    // row is part of the current multi-selection, the menu verbs act on the
    // whole selection; otherwise they act on just that row, and the selection
    // resets to it so the highlight matches what will be touched. This keeps
    // single-row scenarios (s15/s25/s35/s53/s60) single-target.
    const store = useAppStore.getState();
    const selected = store.selection.selectedPaths;
    const inSelection = selected.includes(target.filePath);
    const targetPaths = inSelection ? selected : [target.filePath];
    if (!inSelection) {
      store.setSelection([target.filePath]);
    }
    setContextMenu({
      open: true,
      x: target.x,
      y: target.y,
      filePath: target.filePath,
      isLocked: target.isLocked,
      targetPaths,
    });
  }, []);

  const handleContextMenuClose = useCallback(() => {
    setContextMenu(CLOSED_MENU);
  }, []);

  // ---------------------------------------------------------------------------
  // Manifest open control
  // ---------------------------------------------------------------------------

  const [manifestInputValue, setManifestInputValue] = useState("");
  const [manifestBrowseOpen, setManifestBrowseOpen] = useState(false);
  const loadManifest = useAppStore((s) => s.loadManifest);
  const openExecuteDialog = useAppStore((s) => s.openExecuteDialog);
  const openActionDialog = useAppStore((s) => s.openActionDialog);
  const manifestPath = useAppStore((s) => s.manifest.path);
  const hasSelection = useAppStore((s) => s.selection.selectedPaths.length > 0);

  // "Execute (only selected)": snapshot the current main-tree selection and
  // open the execute dialog scoped to those rows' groups (#430 group-pull).
  // Reads the live selection via getState (the gating subscription above keeps
  // the menu item enabled/disabled; the click itself needs the latest set).
  const handleExecuteSelectedOnly = useCallback(() => {
    const selected = useAppStore.getState().selection.selectedPaths;
    if (selected.length === 0) return;
    openExecuteDialog(selected);
  }, [openExecuteDialog]);

  const handleManifestOpen = useCallback(() => {
    const path = manifestInputValue.trim();
    if (path === "") return;
    void loadManifest(path);
  }, [manifestInputValue, loadManifest]);

  // Filesystem-picker path for Open Manifest (menu + empty-state affordances).
  const handleManifestPicked = useCallback(
    (path: string) => {
      setManifestInputValue(path);
      void loadManifest(path);
    },
    [loadManifest]
  );

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
  const noManifest = manifest.path === null && !manifest.loading;

  let statusText: string;
  if (manifest.path !== null) {
    statusText = t(
      "web.status.summary",
      "{groups} groups · {files} files",
      { groups: manifest.totalGroups, files: manifest.totalFiles }
    );
  } else if (manifest.loading) {
    statusText = t("web.status.loading_manifest", "Loading manifest…");
  } else if (scan.status === "running") {
    const stage = scan.stageName !== "" ? ` — ${scan.stageName}` : "";
    statusText = t("web.status.scanning", "Scanning{stage}", { stage });
  } else if (scan.status === "failed" && scan.error !== null) {
    statusText = t("web.status.scan_failed", "Scan failed: {error}", { error: scan.error });
  } else {
    statusText = t("web.status.ready", "Ready");
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <div className={cn("min-h-screen flex flex-col")}>
      {/* ------------------------------------------------------------------ */}
      {/* Menu bar (Qt MenuController parity) — canonical, above the toolbar   */}
      {/* ------------------------------------------------------------------ */}
      <MenuBar
        manifestLoaded={manifestPath !== null}
        hasSelection={hasSelection}
        locale={locale}
        onScan={() => setScanOpen(true)}
        onOpenManifest={() => setManifestBrowseOpen(true)}
        onSetAction={openActionDialog}
        onExecute={() => openExecuteDialog()}
        onExecuteSelected={handleExecuteSelectedOnly}
        onSetLocale={(loc) => void setLocale(loc)}
      />

      {/* ------------------------------------------------------------------ */}
      {/* Header toolbar                                                       */}
      {/* ------------------------------------------------------------------ */}
      <header className="flex items-center gap-2 border-b px-4 py-2 flex-wrap">
        <button
          data-testid={MAIN_SCAN_BUTTON}
          className="px-3 py-1 rounded border text-sm hover:bg-neutral-100"
          onClick={() => setScanOpen(true)}
        >
          {t("web.toolbar.scan", "Scan")}
        </button>

        <button
          data-testid={MAIN_EXECUTE_BUTTON}
          className="px-3 py-1 rounded border text-sm hover:bg-neutral-100"
          onClick={() => openExecuteDialog()}
        >
          {t("web.toolbar.execute", "Execute")}
        </button>

        <button
          data-testid={ACTION_MAIN_BUTTON}
          className="px-3 py-1 rounded border text-sm hover:bg-neutral-100 disabled:opacity-50 disabled:cursor-not-allowed"
          onClick={openActionDialog}
          disabled={manifestPath === null}
        >
          {t("web.action_dialog.title", "Set Action")}
        </button>

        <button
          data-testid={MAIN_LANG_TOGGLE}
          className="px-3 py-1 rounded border text-sm hover:bg-neutral-100"
          onClick={() => void setLocale(locale === "en" ? "zh_TW" : "en")}
        >
          {locale === "zh_TW" ? "中" : "EN"}
        </button>

        <button
          data-testid={MAIN_SETTINGS_BUTTON}
          className="px-3 py-1 rounded border text-sm hover:bg-neutral-100"
          onClick={() => setSettingsOpen(true)}
        >
          {t("web.toolbar.settings", "Settings")}
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
          placeholder={t("web.manifest.input_placeholder", "Path to manifest .db…")}
          className="rounded border border-neutral-300 px-3 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-400 w-64"
          aria-label={t("web.manifest.input_aria", "Manifest path")}
        />
        <button
          data-testid={MAIN_MANIFEST_OPEN}
          className="px-3 py-1 rounded border text-sm hover:bg-neutral-100"
          onClick={handleManifestOpen}
        >
          {t("web.toolbar.open", "Open")}
        </button>
      </header>

      {/* ------------------------------------------------------------------ */}
      {/* Main — result tree + preview pane side by side                       */}
      {/* ------------------------------------------------------------------ */}
      <main className="flex-1 overflow-hidden flex flex-row">
        {/* Result tree takes remaining width */}
        <div className="flex-1 min-w-0 overflow-hidden">
          {noManifest ? (
            <div
              data-testid={MAIN_EMPTY_STATE}
              className="flex h-full flex-col items-center justify-center gap-4 text-sm text-neutral-400"
            >
              <p>
                {t(
                  "web.empty_state.no_manifest",
                  "No manifest loaded — Scan or Open a manifest to begin."
                )}
              </p>
              <div className="flex items-center gap-3">
                <button
                  data-testid={MAIN_EMPTY_SCAN}
                  className="rounded border px-4 py-1.5 text-sm text-neutral-700 hover:bg-neutral-100"
                  onClick={() => setScanOpen(true)}
                >
                  {t("web.empty_state.scan", "Scan…")}
                </button>
                <button
                  data-testid={MAIN_EMPTY_OPEN}
                  className="rounded border px-4 py-1.5 text-sm text-neutral-700 hover:bg-neutral-100"
                  onClick={() => setManifestBrowseOpen(true)}
                >
                  {t("web.empty_state.open", "Open Manifest…")}
                </button>
              </div>
            </div>
          ) : (
            <ResultTree onContextMenu={handleContextMenu} />
          )}
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
      <FsBrowser
        open={manifestBrowseOpen}
        mode="file"
        title={t("web.browse.title_manifest", "Open manifest")}
        initialPath={manifestInputValue.trim() || undefined}
        onConfirm={handleManifestPicked}
        onOpenChange={setManifestBrowseOpen}
      />
      <ExecuteDialog />
      <ActionDialog />
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
          targetPaths={contextMenu.targetPaths}
          onClose={handleContextMenuClose}
        />
      )}
    </div>
  );
}
