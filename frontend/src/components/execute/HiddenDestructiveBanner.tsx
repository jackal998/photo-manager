// HiddenDestructiveBanner — warns that the active type filter is HIDING
// pending delete rows. Shown only under the "Remove from list only" filter
// (filter="ignore"), where delete decisions are staged but invisible — and,
// per the "visible = committed" contract (#676/#502), are NOT executed by a
// filtered Execute. Without this line, switching to the Remove-only view
// would visually erase the destructive-staged state and the user could
// assume nothing is pending. Mirrors Qt's #502 hidden-destructive banner
// line (execute_action_dialog.py:_hidden_pending_delete_count).

import { EXECUTE_HIDDEN_DESTRUCTIVE_BANNER } from "@/testids";

interface HiddenDestructiveBannerProps {
  /** Number of pending delete rows currently hidden by the active filter. */
  count: number;
}

export function HiddenDestructiveBanner({ count }: HiddenDestructiveBannerProps) {
  if (count <= 0) return null;

  return (
    <div
      data-testid={EXECUTE_HIDDEN_DESTRUCTIVE_BANNER}
      className="flex items-start gap-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
      role="alert"
    >
      {/* Wording matches the Qt parity string (en.yml execute_dialog.
          warning_hidden_destructive) verbatim. */}
      <span>
        ⚠ {count} pending delete row(s) hidden by the current filter — switch
        to All or Delete only to see them.
      </span>
    </div>
  );
}
