// Toolbar — the 52px action strip (#878, layout slice TB).
//
// Extracted from App.tsx unchanged in behaviour (every testid, every handler
// and the #673 `manifestPath === null` gating move across verbatim) and then
// given the layout REPLY's §"Toolbar (F5, F6, F8, F9)" spec plus the two new
// controls L5 asked for. Its own file because App.tsx was already the shell for
// the SSE subscription, the preview-resize drag, the context menu and eight
// dialogs, and this strip adds a filter, three bulk verbs and a danger CTA.
//
// Three rules from the REPLY that are easy to undo by accident:
//
//   * **Exactly one primary.** "Scan" is the only `warm`-filled button —
//     «One filled warm button in the toolbar, so "where do I start" has exactly
//     one answer; everything else is secondary». s74 asserts the COUNT, not
//     just the colour, so a second `bg-warm` control here turns it red.
//   * **The danger CTA never moves.** Zero marked → disabled with the label
//     still reading "Delete 0 files…", rather than hidden: «so the button never
//     moves». A control that appears under the cursor is a control that gets
//     pressed by the click that was aimed at something else.
//   * **The count lives in the label** — «it is the difference between a button
//     that deletes something and a button that deletes *four things*, and it is
//     read right before the confirm modal».
//
// Width: the strip is `flex-nowrap` at a fixed 52px, so it must FIT rather than
// wrap. The two text inputs are the shrinkable members (`flex 0 1 <basis>` with
// a floor); every button is `shrink-0`. At 1280px — the review viewport, and
// what s76 runs at — that resolves with the inputs near their floors.

import type { ChangeEvent, KeyboardEvent } from "react";

import { useT } from "@/i18n/useT";
import { useAppStore } from "@/store/useAppStore";
import { deleteTotals } from "@/lib/deleteTotals";
import { DECISION_VOCAB } from "@/components/result/DecisionControl";
import type { DecisionValue } from "@/api/types";
import {
  ACTION_MAIN_BUTTON,
  MAIN_BULK_LABEL,
  MAIN_BULK_VERB_DELETE,
  MAIN_BULK_VERB_KEEP,
  MAIN_BULK_VERB_SKIP,
  MAIN_DELETE_CTA,
  MAIN_EXECUTE_BUTTON,
  MAIN_FILTER_INPUT,
  MAIN_LANG_TOGGLE,
  MAIN_MANIFEST_INPUT,
  MAIN_MANIFEST_OPEN,
  MAIN_SCAN_BUTTON,
  MAIN_SETTINGS_BUTTON,
  MAIN_TOOLBAR,
} from "@/testids";

// REPLY: «button height 34px, radius 8px, font 13px». `leading-none` because a
// 13px line-height inside a 34px box is what keeps the three button variants
// the same height as the two inputs beside them.
const BTN_BASE =
  "inline-flex h-[34px] shrink-0 items-center justify-center whitespace-nowrap " +
  "rounded-[8px] text-[13px] leading-none focus:outline-none " +
  "focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-warm " +
  "disabled:cursor-not-allowed";

// «primary padding 0 16px · 700 · bg #a85a2c · border 1px #a85a2c · text
// #ffffff · hover #8f4b23 · active #7a3f1d · no shadow, no gradient».
const BTN_PRIMARY =
  `${BTN_BASE} px-4 font-bold border border-warm bg-warm text-white ` +
  "hover:bg-warm-hover hover:border-warm-hover active:bg-warm-active active:border-warm-active";

// «secondary padding 0 14px · 600 · bg #fffdf9 · border 1px #ddd2bf · text
// #2c2823 · hover bg #f7f1e7, border #a89f8f».
const BTN_SECONDARY =
  `${BTN_BASE} px-[14px] font-semibold border border-hairline-input bg-panel text-ink ` +
  "hover:bg-panel-hover hover:border-ink-hairline disabled:opacity-50";

// «height 34px · radius 8px · padding 0 16px · font 13px/700 · background
// #c4503f · border 1px #c4503f · text #ffffff · no shadow · hover #ab4435 ·
// disabled bg #f1ece2, border #ddd5c8, text #a89f8f».
//
// The hover is `brightness-90` rather than the REPLY's literal #ab4435: the
// owner's standing rule for this slice is that no new red enters the palette,
// and #ab4435 would be a fifth one. `danger-warm` darkened by a filter lands in
// the same place without a token nobody can name the role of.
const BTN_DANGER =
  `${BTN_BASE} px-4 font-bold border border-danger-warm bg-danger-warm text-white ` +
  "hover:brightness-90 disabled:border-dec-remove-line disabled:bg-dec-remove-bg " +
  "disabled:text-ink-hairline disabled:brightness-100";

// «height 34px · radius 8px · padding 0 12px · min-width 180px · flex 0 1 260px
// · background #fffdf9 · border 1px solid #e7ddcd · font 13px · focus border
// #a85a2c + 2px inset ring #a85a2c at 35%».  `pl-[30px]` instead of the flat
// 12px because the ⌕ glyph sits inside the box; the 180px floor is relaxed to
// 104px here so the strip still fits at 1280 beside the web-only manifest
// input, which the prototype's toolbar never had to carry.
//
// The floor is a MEASUREMENT, not a guess. At 1280 under the shipped stack the
// strip came to exactly 1280 with both inputs at 140/110 — no headroom — and
// under a deliberately wider fallback (no Segoe UI on the Linux CI runner, so
// it falls back to DejaVu/Liberation) it overflowed by 64px and s76 went red.
// 104 + 56 buys 90px back, leaving ~26px of margin on that stack.
const FILTER_INPUT =
  "h-[34px] w-full rounded-[8px] border border-hairline bg-panel pl-[30px] pr-3 " +
  "text-[13px] text-ink placeholder:text-ink-muted focus:border-warm " +
  "focus:outline-none focus:ring-2 focus:ring-inset focus:ring-warm/35";

export interface ToolbarProps {
  manifestPath: string | null;
  locale: string;
  manifestInputValue: string;
  onManifestInputChange: (value: string) => void;
  onManifestOpen: () => void;
  onScan: () => void;
  onExecute: () => void;
  onSetAction: () => void;
  onSettings: () => void;
  onSetLocale: (locale: string) => void;
}

export function Toolbar({
  manifestPath,
  locale,
  manifestInputValue,
  onManifestInputChange,
  onManifestOpen,
  onScan,
  onExecute,
  onSetAction,
  onSettings,
  onSetLocale,
}: ToolbarProps) {
  const t = useT();
  const filterText = useAppStore((s) => s.resultView.filterText);
  const setFilterText = useAppStore((s) => s.setFilterText);
  const selectedCount = useAppStore((s) => s.selection.selectedPaths.length);
  const applyBulkDecision = useAppStore((s) => s.applyBulkDecision);
  // #906 — read off the whole manifest, never the filtered view: this is the
  // number Execute acts on (see lib/deleteTotals.ts).
  const groups = useAppStore((s) => s.manifest.groups);
  const { count: deleteCount } = deleteTotals(groups);

  const bulkLabel =
    selectedCount > 0
      ? t("web.toolbar.bulk_set_n", "Set {count} selected:", {
          count: selectedCount,
        })
      : t("web.toolbar.bulk_set_none", "Set selected:");

  // L5: «Add a Shift-range hint in the toolbar's tooltip» — the verbs act on a
  // ctrl/shift selection and there is deliberately no per-row checkbox and no
  // select-all, so the tooltip is where the selection model is explained.
  const bulkHint = t(
    "web.toolbar.bulk_hint",
    "Acts on the rows selected in the list — Ctrl-click to add one, Shift-click for a range. Locked rows are left unchanged."
  );

  // Singular/plural split per count, the copy-audit S1 shape — a single
  // template would render "Delete 1 files…" on the one row that matters most.
  const deleteCtaLabel = t("web.toolbar.delete_n", "⊗  Delete {count} {fileWord}…", {
    count: deleteCount,
    fileWord:
      deleteCount === 1
        ? t("web.status.file_singular", "file")
        : t("web.status.file_plural", "files"),
  });

  function verb(
    testid: string,
    decision: DecisionValue
  ): { "data-testid": string; decision: DecisionValue; label: string } {
    return {
      "data-testid": testid,
      decision,
      label: t(DECISION_VOCAB[decision].key, DECISION_VOCAB[decision].fallback),
    };
  }

  const verbs = [
    verb(MAIN_BULK_VERB_KEEP, ""),
    verb(MAIN_BULK_VERB_DELETE, "delete"),
    verb(MAIN_BULK_VERB_SKIP, "ignore"),
  ];

  return (
    <header
      data-testid={MAIN_TOOLBAR}
      // `overflow-x-auto`, not `hidden`: the floors above are sized from a
      // measurement on the widest stack we could reproduce, but a font we have
      // not seen — or a longer label in a third locale — must degrade to a
      // scrollable strip rather than to a clipped one, because the thing at the
      // clipped end is the only destructive control on the screen.
      className="flex h-[52px] shrink-0 items-center gap-2 overflow-x-auto overflow-y-hidden border-b border-hairline bg-toolbar px-4"
    >
      {/* The one primary. */}
      <button data-testid={MAIN_SCAN_BUTTON} className={BTN_PRIMARY} onClick={onScan}>
        {t("web.toolbar.scan", "Scan")}
      </button>

      <button
        data-testid={MAIN_EXECUTE_BUTTON}
        className={BTN_SECONDARY}
        onClick={onExecute}
        // #673 — gate Execute on a loaded manifest, matching the menu
        // Action → Execute, the Set-Action button, and Qt. Without it the
        // toolbar opens the destructive-execute dialog over empty groups.
        disabled={manifestPath === null}
      >
        {t("web.toolbar.execute", "Execute")}
      </button>

      <button
        data-testid={ACTION_MAIN_BUTTON}
        className={BTN_SECONDARY}
        // #735: openActionDialog takes an optional initialField, so it can no
        // longer be wired directly as a MouseEventHandler — wrap it argless.
        onClick={onSetAction}
        disabled={manifestPath === null}
      >
        {/* Own key, not the dialog title's (copy audit T1): a button wants a
            shorter label than the dialog heading. */}
        {t("web.toolbar.set_action", "Set Action…")}
      </button>

      {/* Filter (new, slice TB). View-only substring match over basename +
          folder — see lib/rowFilter.ts. */}
      <div className="relative flex min-w-[104px] flex-[0_1_260px] items-center">
        <span
          aria-hidden="true"
          className="pointer-events-none absolute left-3 text-[13px] leading-none text-ink-hairline"
        >
          ⌕
        </span>
        <input
          data-testid={MAIN_FILTER_INPUT}
          type="text"
          value={filterText}
          onChange={(e: ChangeEvent<HTMLInputElement>) => setFilterText(e.target.value)}
          placeholder={t("web.toolbar.filter_placeholder", "Filter photos…")}
          aria-label={t("web.toolbar.filter_aria", "Filter photos")}
          className={FILTER_INPUT}
        />
      </div>

      {/* Counted bulk verbs (L5). No select-all and no per-row checkbox —
          «at 40,000 rows "all" is a trap» — so the label carries the live
          selection count and the group is disabled at zero. */}
      <div className="flex shrink-0 items-center gap-1.5" title={bulkHint}>
        <span
          data-testid={MAIN_BULK_LABEL}
          className="whitespace-nowrap text-[13px] font-semibold text-ink-muted"
        >
          {bulkLabel}
        </span>
        {verbs.map((v) => (
          <button
            key={v["data-testid"]}
            data-testid={v["data-testid"]}
            className={BTN_SECONDARY}
            disabled={selectedCount === 0 || manifestPath === null}
            onClick={() => void applyBulkDecision(v.decision)}
          >
            {v.label}
          </button>
        ))}
      </div>

      <span className="min-w-0 flex-1" />

      {/* Web-only manifest open control (F10) — secondary, kept by owner
          decision. Shrinkable: a path never fits anyway and the filesystem
          picker behind File → Open Manifest… is the real entry point. */}
      <input
        data-testid={MAIN_MANIFEST_INPUT}
        type="text"
        value={manifestInputValue}
        onChange={(e: ChangeEvent<HTMLInputElement>) =>
          onManifestInputChange(e.target.value)
        }
        onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
          if (e.key === "Enter") onManifestOpen();
        }}
        placeholder={t("web.manifest.input_placeholder", "Path to manifest .db…")}
        className="h-[34px] min-w-[56px] flex-[0_1_200px] rounded-[8px] border border-hairline-input bg-panel px-3 text-[13px] text-ink placeholder:text-ink-muted focus:border-warm focus:outline-none focus:ring-2 focus:ring-inset focus:ring-warm/35"
        aria-label={t("web.manifest.input_aria", "Manifest path")}
      />
      <button
        data-testid={MAIN_MANIFEST_OPEN}
        className={BTN_SECONDARY}
        onClick={onManifestOpen}
      >
        {t("web.toolbar.open", "Open")}
      </button>

      <button
        data-testid={MAIN_LANG_TOGGLE}
        className={BTN_SECONDARY}
        onClick={() => onSetLocale(locale === "en" ? "zh_TW" : "en")}
      >
        {locale === "zh_TW" ? "中" : "EN"}
      </button>

      <button
        data-testid={MAIN_SETTINGS_BUTTON}
        className={BTN_SECONDARY}
        onClick={onSettings}
      >
        {t("web.toolbar.settings", "Settings")}
      </button>

      {/* «Divider before it because it is the only destructive control in the
          toolbar and it should not sit flush against the filter.» */}
      <span aria-hidden="true" className="ml-1 h-[22px] w-px shrink-0 bg-hairline" />

      <button
        data-testid={MAIN_DELETE_CTA}
        className={`${BTN_DANGER} ml-1`}
        disabled={deleteCount === 0 || manifestPath === null}
        onClick={onExecute}
      >
        {deleteCtaLabel}
      </button>
    </header>
  );
}
