// MenuBar — top menu bar mirroring Qt's MenuController (File / Action / View).
//
// Additive: it sits above the existing quick-access toolbar (which the QA
// scenarios target by testid). The menu is the canonical Qt-parity surface and
// the *second* entry point into the ActionDialog (Action → Set Action by Regex).
//
// Only the menu items with a settled web behaviour are wired here. The
// remaining Qt menus are tracked as a follow-up (see the PR's deferred issue)
// rather than shipped as no-op or semantically-ambiguous items:
//   - Save Manifest: backend IS plumbed (POST /api/save + store.saveManifest),
//     but the web persists decisions live via PATCH /api/decision, so whether
//     an explicit "save snapshot" is meaningful needs a product decision.
//   - Log menu: depends on the web OS-integration (reveal/open) story.
//   - File → Exit: N/A for a browser tab.
// List → Remove from List shipped in #678-D: acts on the current main-tree
// selection via the same store.removeFromList path as the context menu.
// Web variant of Qt's no-selection QMessageBox: the item is disabled when
// nothing is selected (same idiom as Execute (only selected)).

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useMemo } from "react";

import { useT } from "@/i18n/useT";
import { useAppStore } from "@/store/useAppStore";
import { dateLocaleFor } from "@/lib/format";
import { scanSourceName } from "@/lib/scanSummary";
import {
  MAIN_MENU_BAR,
  MAIN_SCAN_SUMMARY,
  MENU_FILE,
  MENU_FILE_SCAN,
  MENU_FILE_OPEN,
  MENU_ACTION,
  MENU_ACTION_SET,
  MENU_ACTION_EXECUTE,
  MENU_ACTION_EXECUTE_SELECTED,
  MENU_LIST,
  MENU_LIST_REMOVE_FROM_LIST,
  MENU_VIEW,
  MENU_VIEW_LANG_EN,
  MENU_VIEW_LANG_ZH,
} from "@/testids";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface MenuBarProps {
  manifestLoaded: boolean;
  /** True when ≥1 row is selected in the main result tree (gates the
   *  "Execute (only selected)" entry). */
  hasSelection: boolean;
  locale: string;
  onScan: () => void;
  onOpenManifest: () => void;
  onSetAction: () => void;
  onExecute: () => void;
  /** Open the execute dialog scoped to the current main-tree selection. */
  onExecuteSelected: () => void;
  /** Remove the current main-tree selection from the review list (#678-D). */
  onRemoveFromList: () => void;
  onSetLocale: (locale: string) => void;
}

// ---------------------------------------------------------------------------
// Styling
// ---------------------------------------------------------------------------

const TRIGGER_CLASS =
  "px-3 py-1 text-sm rounded hover:bg-subtle data-[state=open]:bg-subtle focus:outline-none";
const CONTENT_CLASS =
  "min-w-[12rem] rounded border border-hairline bg-panel py-1 shadow-md z-50";
const ITEM_CLASS =
  "flex items-center justify-between px-3 py-1.5 text-sm cursor-pointer outline-none data-[highlighted]:bg-subtle data-[disabled]:opacity-40 data-[disabled]:cursor-not-allowed";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MenuBar({
  manifestLoaded,
  hasSelection,
  locale,
  onScan,
  onOpenManifest,
  onSetAction,
  onExecute,
  onExecuteSelected,
  onRemoveFromList,
  onSetLocale,
}: MenuBarProps) {
  const t = useT();

  // Scan summary (#878 slice TB, F3/F4) — read from the store rather than
  // drilled through props: it is derived from the manifest the store already
  // holds, and every caller of this component would otherwise have to pass
  // three fields it does not use for anything else.
  const groups = useAppStore((s) => s.manifest.groups);
  const manifestPath = useAppStore((s) => s.manifest.path);
  const totalGroups = useAppStore((s) => s.manifest.totalGroups);
  const totalFiles = useAppStore((s) => s.manifest.totalFiles);
  const sourceName = useMemo(
    () => scanSourceName(groups, manifestPath),
    [groups, manifestPath]
  );
  const numberLocale = dateLocaleFor(locale);

  return (
    <nav
      data-testid={MAIN_MENU_BAR}
      // REPLY: «menu bar height 33px (as shipped) · bg #f3ede3 · bottom rule
      // 1px solid #e7ddcd · triggers left, as shipped». The height was already
      // 33px from py-0.5 + the trigger box; it is written down now because the
      // summary on the right must not be what changes it.
      className="flex h-[33px] shrink-0 items-center gap-1 border-b border-hairline bg-titlebar px-2"
      aria-label={t("web.menu.aria_main", "Main menu")}
    >
      {/* File */}
      <DropdownMenu.Root>
        <DropdownMenu.Trigger data-testid={MENU_FILE} className={TRIGGER_CLASS}>
          {t("web.menu.file", "File")}
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content className={CONTENT_CLASS} align="start" sideOffset={2}>
            <DropdownMenu.Item
              data-testid={MENU_FILE_SCAN}
              className={ITEM_CLASS}
              onSelect={onScan}
            >
              {t("web.menu.file_scan", "Scan Sources…")}
            </DropdownMenu.Item>
            <DropdownMenu.Separator className="my-1 h-px bg-hairline" />
            <DropdownMenu.Item
              data-testid={MENU_FILE_OPEN}
              className={ITEM_CLASS}
              onSelect={onOpenManifest}
            >
              {t("web.menu.file_open", "Open Manifest…")}
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>

      {/* Action */}
      <DropdownMenu.Root>
        <DropdownMenu.Trigger data-testid={MENU_ACTION} className={TRIGGER_CLASS}>
          {t("web.menu.action", "Action")}
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content className={CONTENT_CLASS} align="start" sideOffset={2}>
            <DropdownMenu.Item
              data-testid={MENU_ACTION_SET}
              className={ITEM_CLASS}
              disabled={!manifestLoaded}
              onSelect={onSetAction}
            >
              {t("web.menu.action_set", "Set Action by Field…")}
            </DropdownMenu.Item>
            <DropdownMenu.Separator className="my-1 h-px bg-hairline" />
            <DropdownMenu.Item
              data-testid={MENU_ACTION_EXECUTE}
              className={ITEM_CLASS}
              disabled={!manifestLoaded}
              onSelect={onExecute}
            >
              {t("web.menu.action_execute", "Execute Action…")}
            </DropdownMenu.Item>
            <DropdownMenu.Item
              data-testid={MENU_ACTION_EXECUTE_SELECTED}
              className={ITEM_CLASS}
              disabled={!manifestLoaded || !hasSelection}
              onSelect={onExecuteSelected}
            >
              {t("web.menu.action_execute_selected", "Execute Action (only selected)…")}
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>

      {/* List (#678-D — Qt parity; item order mirrors Qt: File/Action/List/View) */}
      <DropdownMenu.Root>
        <DropdownMenu.Trigger data-testid={MENU_LIST} className={TRIGGER_CLASS}>
          {t("web.menu.list", "List")}
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content className={CONTENT_CLASS} align="start" sideOffset={2}>
            <DropdownMenu.Item
              data-testid={MENU_LIST_REMOVE_FROM_LIST}
              className={ITEM_CLASS}
              disabled={!manifestLoaded || !hasSelection}
              onSelect={onRemoveFromList}
            >
              {t("web.menu.list_remove", "Skip Selected now")}
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>

      {/* View */}
      <DropdownMenu.Root>
        <DropdownMenu.Trigger data-testid={MENU_VIEW} className={TRIGGER_CLASS}>
          {t("web.menu.view", "View")}
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content className={CONTENT_CLASS} align="start" sideOffset={2}>
            <DropdownMenu.Label className="px-3 py-1 text-xs uppercase tracking-wide text-ink-muted">
              {t("web.menu.view_language", "Language")}
            </DropdownMenu.Label>
            <DropdownMenu.Item
              data-testid={MENU_VIEW_LANG_EN}
              className={ITEM_CLASS}
              onSelect={() => onSetLocale("en")}
            >
              <span>{t("web.menu.lang_en", "English")}</span>
              {locale === "en" && <span aria-hidden="true">✓</span>}
            </DropdownMenu.Item>
            <DropdownMenu.Item
              data-testid={MENU_VIEW_LANG_ZH}
              className={ITEM_CLASS}
              onSelect={() => onSetLocale("zh_TW")}
            >
              <span>{t("web.menu.lang_zh", "中文 (繁體)")}</span>
              {locale === "zh_TW" && <span aria-hidden="true">✓</span>}
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>

      {/* «scan summary right-aligned in the same bar, 11.5px #6b6358, 16px
          from the right edge … truncate the source name first; never wrap».
          The nav's own padding is 8px, so the extra 8px here makes 16px. */}
      <span className="min-w-0 flex-1" />
      {manifestPath !== null && (
        <span
          data-testid={MAIN_SCAN_SUMMARY}
          className="flex min-w-0 items-center gap-1 pr-2 text-[11.5px] whitespace-nowrap text-ink-muted"
        >
          <span className="shrink-0">
            {t("web.menu.scan_summary", "{photos} {photoWord} · {groups} {groupWord}", {
              photos: totalFiles.toLocaleString(numberLocale),
              photoWord:
                totalFiles === 1
                  ? t("web.status.file_singular", "file")
                  : t("web.status.file_plural", "files"),
              groups: totalGroups.toLocaleString(numberLocale),
              groupWord:
                totalGroups === 1
                  ? t("web.status.group_singular", "group")
                  : t("web.status.group_plural", "groups"),
            })}
          </span>
          {sourceName !== null && (
            <>
              <span aria-hidden="true" className="shrink-0">
                ·
              </span>
              {/* The ONLY shrinkable member, so the counts survive a narrow
                  window and the name is what gives way — «truncate the source
                  name first». */}
              <span className="truncate" title={sourceName}>
                {sourceName}
              </span>
            </>
          )}
        </span>
      )}
    </nav>
  );
}
