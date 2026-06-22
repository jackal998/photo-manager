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
//   - Execute Selected Only: needs tree-selection → scoped-execute wiring.
//   - List → Remove from List: needs a remove-by-regex / selection entry point.
//   - Log menu: depends on the web OS-integration (reveal/open) story.
//   - File → Exit: N/A for a browser tab.

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";

import { useT } from "@/i18n/useT";
import {
  MAIN_MENU_BAR,
  MENU_FILE,
  MENU_FILE_SCAN,
  MENU_FILE_OPEN,
  MENU_ACTION,
  MENU_ACTION_SET,
  MENU_ACTION_EXECUTE,
  MENU_VIEW,
  MENU_VIEW_LANG_EN,
  MENU_VIEW_LANG_ZH,
} from "@/testids";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface MenuBarProps {
  manifestLoaded: boolean;
  locale: string;
  onScan: () => void;
  onOpenManifest: () => void;
  onSetAction: () => void;
  onExecute: () => void;
  onSetLocale: (locale: string) => void;
}

// ---------------------------------------------------------------------------
// Styling
// ---------------------------------------------------------------------------

const TRIGGER_CLASS =
  "px-3 py-1 text-sm rounded hover:bg-neutral-100 data-[state=open]:bg-neutral-100 focus:outline-none";
const CONTENT_CLASS =
  "min-w-[12rem] rounded border border-neutral-200 bg-white py-1 shadow-md z-50";
const ITEM_CLASS =
  "flex items-center justify-between px-3 py-1.5 text-sm cursor-pointer outline-none data-[highlighted]:bg-neutral-100 data-[disabled]:opacity-40 data-[disabled]:cursor-not-allowed";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function MenuBar({
  manifestLoaded,
  locale,
  onScan,
  onOpenManifest,
  onSetAction,
  onExecute,
  onSetLocale,
}: MenuBarProps) {
  const t = useT();

  return (
    <nav
      data-testid={MAIN_MENU_BAR}
      className="flex items-center gap-1 border-b bg-neutral-50 px-2 py-0.5"
      aria-label="Main menu"
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
            <DropdownMenu.Separator className="my-1 h-px bg-neutral-200" />
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
              {t("web.menu.action_set", "Set Action by Regex")}
            </DropdownMenu.Item>
            <DropdownMenu.Separator className="my-1 h-px bg-neutral-200" />
            <DropdownMenu.Item
              data-testid={MENU_ACTION_EXECUTE}
              className={ITEM_CLASS}
              disabled={!manifestLoaded}
              onSelect={onExecute}
            >
              {t("web.menu.action_execute", "Execute…")}
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
            <DropdownMenu.Label className="px-3 py-1 text-xs uppercase tracking-wide text-neutral-400">
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
    </nav>
  );
}
