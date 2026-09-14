// AllDeleteBanner — warns when all members of a group are marked delete.
// Renders a dismissible banner listing the group numbers, each with a
// jump-anchor so the user can scroll the ExecuteTree to that group.
// Hidden when no full-delete group exists.
//
// Copy audit E2: the sentence used to be five hardcoded English JSX
// fragments ("All files in" + group/groups + the ids + "are marked for
// deletion…"). No locale could reorder them, and zh_TW puts the group list
// in a different position, so the banner is now ONE translated template
// (web.execute_dialog.warning_complete_groups) split on its `{groups}`
// placeholder — the clickable jump anchors are injected at the split point,
// so every testid and handler is unchanged.

import { useT } from "@/i18n/useT";
import {
  EXECUTE_ALL_DELETE_BANNER,
  executeAllDeleteJumpTestid,
} from "@/testids";

const TEMPLATE_EN =
  "{groupWord} {groups} will have ALL files deleted. Review decisions below " +
  "before clicking Execute.";

interface AllDeleteBannerProps {
  /** Group IDs (as strings) where every member has user_decision === "delete". */
  allDeleteGroupIds: string[];
  /** Called when the user clicks a group-id jump anchor. */
  onJumpToGroup: (groupId: string) => void;
}

export function AllDeleteBanner({
  allDeleteGroupIds,
  onJumpToGroup,
}: AllDeleteBannerProps) {
  const t = useT();
  if (allDeleteGroupIds.length === 0) return null;

  const groupWord =
    allDeleteGroupIds.length === 1
      ? t("web.execute_dialog.group_singular", "Group")
      : t("web.execute_dialog.group_plural", "Groups");

  // `{groups}` is deliberately NOT passed as a param: interpolate() leaves an
  // unmatched placeholder verbatim, so it survives as the split marker for
  // the anchor list below. A translation that drops it still renders the
  // whole sentence — the anchors simply land at the end.
  const sentence = t(
    "web.execute_dialog.warning_complete_groups",
    TEMPLATE_EN,
    { groupWord }
  );
  const marker = sentence.indexOf("{groups}");
  const before = marker >= 0 ? sentence.slice(0, marker) : sentence;
  const after = marker >= 0 ? sentence.slice(marker + "{groups}".length) : "";

  return (
    <div
      data-testid={EXECUTE_ALL_DELETE_BANNER}
      className="flex items-start gap-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
      role="alert"
    >
      <span className="font-semibold shrink-0">
        {t("web.execute_dialog.warning_prefix", "Warning:")}
      </span>
      <span>
        {before}
        {allDeleteGroupIds.map((gid, i) => (
          <span key={gid}>
            {i > 0 && ", "}
            <button
              type="button"
              data-testid={executeAllDeleteJumpTestid(gid)}
              onClick={() => onJumpToGroup(gid)}
              className="underline font-semibold hover:text-amber-700 focus:outline-none focus:ring-1 focus:ring-amber-500 rounded"
            >
              {gid}
            </button>
          </span>
        ))}
        {after}
      </span>
    </div>
  );
}
