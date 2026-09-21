// Status bar — the 30px footer strip (#878 layout slice TB; closes #906).
//
// Extracted from App.tsx and given the layout REPLY's §"Status bar (F11, F12)"
// spec. Two structural rules the plan called out as the trap here:
//
//   * **The 30px is the STRIP, not the <footer>.** Three error lines already
//     stack in this footer (#712 manifest error, the #430 reveal error), and a
//     fixed-height footer would clip them. So the height sits on the strip
//     `<div>` and the error rows are siblings BELOW it, inside the same footer
//     surface. s74 reads the footer's background through
//     `[data-testid=main-status-bar].closest('footer')`, which still resolves.
//   * **`<p data-testid={MAIN_STATUS_BAR}>` keeps its element identity** — it
//     is the summary text and nothing else, so the new figures beside it are
//     their own elements rather than extra words inside it.
//
// The two figures are #906: «the reclaim total and the delete count are the two
// numbers that make the status bar worth its 30px — they are the running answer
// to "is this session worth continuing"». They count the WHOLE manifest, not
// the filtered view (lib/deleteTotals.ts explains why).

import { useT } from "@/i18n/useT";
import { useAppStore } from "@/store/useAppStore";
import { describeApiError } from "@/lib/apiErrorText";
import { formatBytes } from "@/lib/format";
import { deleteTotals } from "@/lib/deleteTotals";
import type { Density } from "@/lib/density";
import {
  MAIN_DENSITY_COMFORTABLE,
  MAIN_DENSITY_COMPACT,
  MAIN_DENSITY_TOGGLE,
  MAIN_EXECUTE_ERROR,
  MAIN_STATUS_BAR,
  MAIN_STATUS_ERROR,
  MAIN_STATUS_DELETE_COUNT,
  MAIN_STATUS_RECLAIM,
  MAIN_STATUS_STRIP,
} from "@/testids";

type Translate = (
  key: string,
  fallback: string,
  params?: Record<string, string | number>
) => string;

/**
 * Secondary technical line under a status-bar error (copy audit S2).
 *
 * Rendered INSIDE the existing alert paragraph (as a block span, not a new
 * paragraph) so `main-status-error` / `main-execute-error` still carry the
 * whole message and no testid or layout box is added. Renders nothing when
 * the failure is fully described by its sentence.
 */
function ApiErrorDetail({ raw, t }: { raw: string; t: Translate }) {
  const { detail } = describeApiError(raw, t);
  if (detail === null) return null;
  return (
    <span className="block text-xs text-ink-muted">
      {t("web.error.technical_detail", "Details: {detail}", { detail })}
    </span>
  );
}

// «track bg #fffdf9, border 1px #e7ddcd, radius 7px, padding 2px; buttons
// height 22px, radius 5px, padding 0 10px, font 11px/600; selected bg #a85a2c
// text #ffffff, unselected transparent text #6b6358».
const DENSITY_BTN =
  "h-[22px] rounded-[5px] px-[10px] text-[11px] font-semibold leading-none " +
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-warm";

function DensityToggle() {
  const t = useT();
  const density = useAppStore((s) => s.resultView.density);
  const setDensity = useAppStore((s) => s.setDensity);

  const options: { value: Density; testid: string; key: string; fallback: string }[] = [
    {
      value: "comfortable",
      testid: MAIN_DENSITY_COMFORTABLE,
      key: "web.density.comfortable",
      fallback: "Comfortable",
    },
    {
      value: "compact",
      testid: MAIN_DENSITY_COMPACT,
      key: "web.density.compact",
      fallback: "Compact",
    },
  ];

  return (
    <div
      data-testid={MAIN_DENSITY_TOGGLE}
      role="group"
      aria-label={t("web.density.aria", "Row density")}
      className="flex items-center gap-0.5 rounded-[7px] border border-hairline bg-panel p-[2px]"
    >
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          data-testid={opt.testid}
          aria-pressed={density === opt.value}
          onClick={() => setDensity(opt.value)}
          className={`${DENSITY_BTN} ${
            density === opt.value
              ? "bg-warm text-white"
              : "bg-transparent text-ink-muted hover:text-ink"
          }`}
        >
          {t(opt.key, opt.fallback)}
        </button>
      ))}
    </div>
  );
}

export interface StatusBarProps {
  /** The existing summary sentence ("3 groups · 9 files", "Scanning…", …). */
  statusText: string;
}

export function StatusBar({ statusText }: StatusBarProps) {
  const t = useT();
  const manifest = useAppStore((s) => s.manifest);
  const executeError = useAppStore((s) => s.execute.executeError);
  const executeOpen = useAppStore((s) => s.execute.executeOpen);
  const { count: deleteCount, bytes: deleteBytes } = deleteTotals(manifest.groups);

  // Both figures render together, at zero as well, whenever a manifest is
  // loaded — the same "never moves" reasoning as the danger CTA. A counter that
  // appears only once it is non-zero cannot be checked for "did that write land".
  const showTotals = manifest.path !== null;

  return (
    <footer className="shrink-0 border-t border-hairline bg-titlebar">
      {/* «height 30px, padding 0 16px, gap 14px, font 11.5px, colour #6b6358» */}
      <div
        data-testid={MAIN_STATUS_STRIP}
        className="flex h-[30px] items-center gap-[14px] px-4 text-[11.5px] text-ink-muted"
      >
        <p data-testid={MAIN_STATUS_BAR} className="truncate">
          {statusText}
        </p>
        {showTotals && (
          <>
            <span
              data-testid={MAIN_STATUS_DELETE_COUNT}
              className="whitespace-nowrap font-semibold text-danger-warm"
            >
              {t("web.status.marked_to_delete", "⊗ {count} marked to delete", {
                count: deleteCount,
              })}
            </span>
            <span data-testid={MAIN_STATUS_RECLAIM} className="whitespace-nowrap">
              {t("web.status.reclaims", "↺ reclaims {size}", {
                size: formatBytes(deleteBytes),
              })}
            </span>
          </>
        )}
        <span className="min-w-0 flex-1" />
        {/* «Right-aligned, because it is a view preference and belongs
            furthest from the data.» */}
        <span className="whitespace-nowrap text-ink-hairline">
          {t("web.density.label", "Density")}
        </span>
        <DensityToggle />
      </div>

      {/* Additive error line (#712): manifest.error is written by ~9 store
          actions (failed load / decision / lock / prune / save) but was
          rendered nowhere. Shown BELOW the 30px strip — without masking the
          summary — so the failure is visible and the strip keeps its height. */}
      {manifest.error !== null && (
        <p
          data-testid={MAIN_STATUS_ERROR}
          role="alert"
          className="px-4 py-1 text-sm text-danger-warm"
        >
          {t("web.status.manifest_failed", "Manifest error: {error}", {
            error: describeApiError(manifest.error, t).message,
          })}
          <ApiErrorDetail raw={manifest.error} t={t} />
        </p>
      )}
      {/* Additive error line: execute.executeError is written by
          revealInExplorer ("Open folder" in the main-tree context menu) on a
          failed reveal, but the ONLY other consumer was ExecuteDialog — which
          isn't mounted/visible unless the user already has it open. Shown here
          so a reveal failure from the main tree is visible. Suppressed while
          the Execute dialog IS open to avoid showing the same message twice. */}
      {executeError !== null && !executeOpen && (
        <p
          data-testid={MAIN_EXECUTE_ERROR}
          role="alert"
          className="px-4 py-1 text-sm text-danger-warm"
        >
          {t("web.status.execute_failed", "Action error: {error}", {
            error: describeApiError(executeError, t).message,
          })}
          <ApiErrorDetail raw={executeError} t={t} />
        </p>
      )}
    </footer>
  );
}
