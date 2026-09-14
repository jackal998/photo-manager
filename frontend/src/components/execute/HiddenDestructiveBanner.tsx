// HiddenDestructiveBanner — warns that the active type filter is HIDING
// pending delete rows. Shown only under the "Remove from list only" filter
// (filter="ignore"), where delete decisions are staged but invisible — and,
// per the "visible = committed" contract (#676/#502), are NOT executed by a
// filtered Execute. Without this line, switching to the Remove-only view
// would visually erase the destructive-staged state and the user could
// assume nothing is pending. Mirrors Qt's #502 hidden-destructive banner
// line (execute_action_dialog.py:_hidden_pending_delete_count).

import { useT } from "@/i18n/useT";
import { EXECUTE_HIDDEN_DESTRUCTIVE_BANNER } from "@/testids";

const TEMPLATE_EN =
  "⚠ {n} pending delete {rowWord} hidden by the current filter — switch to " +
  "All or Delete only to see them.";

interface HiddenDestructiveBannerProps {
  /** Number of pending delete rows currently hidden by the active filter. */
  count: number;
}

export function HiddenDestructiveBanner({ count }: HiddenDestructiveBannerProps) {
  const t = useT();
  if (count <= 0) return null;

  return (
    <div
      data-testid={EXECUTE_HIDDEN_DESTRUCTIVE_BANNER}
      className="flex items-start gap-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
      role="alert"
    >
      {/* Wording matches the Qt parity string (execute_dialog.
          warning_hidden_destructive) — but through the catalog now, and with
          "row(s)" split into singular/plural (copy audit E3): it was
          hardcoded English here, so zh_TW never saw a translation, and the
          flattened "(s)" is the shape this catalog's own comments call a
          review finding. */}
      <span>
        {t("web.execute_dialog.warning_hidden_destructive", TEMPLATE_EN, {
          n: count,
          rowWord:
            count === 1
              ? t("web.execute_dialog.row_singular", "row")
              : t("web.execute_dialog.row_plural", "rows"),
        })}
      </span>
    </div>
  );
}
