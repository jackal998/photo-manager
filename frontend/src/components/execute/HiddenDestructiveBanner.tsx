// HiddenDestructiveBanner — warns that the active type filter is HIDING
// pending delete rows. Shown only under the "Skip only" filter
// (filter="ignore"), where delete decisions are staged but invisible — and,
// per the "visible = committed" contract (#676/#502), are NOT executed by a
// filtered Execute. Without this line, switching to the Skip-only view
// would visually erase the destructive-staged state and the user could
// assume nothing is pending. Mirrors Qt's #502 hidden-destructive banner
// line (execute_action_dialog.py:_hidden_pending_delete_count).

import { useT } from "@/i18n/useT";
import { EXECUTE_HIDDEN_DESTRUCTIVE_BANNER } from "@/testids";
import { CAUTION_BANNER } from "./cautionBanner";

// The leading glyph moved OUT of the sentence in layout slice E: the caution
// role owns ▲, and a ⚠ inside the copy would put a second, different warning
// mark on the same strip in every locale that copied it.
const TEMPLATE_EN =
  "{n} pending delete {rowWord} hidden by the current filter — switch to " +
  "All decided or Delete only to see them.";

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
      className={CAUTION_BANNER}
      role="alert"
    >
      {/* Q9's caution glyph, decorative — it is the grayscale separator from
          the danger role, and the sentence beside it carries the meaning. */}
      <span aria-hidden="true" className="shrink-0 text-[12px] leading-[1.5]">
        ▲
      </span>
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
