// AllDeleteBanner — warns when all members of a group are marked delete.
// Renders a dismissible banner listing the group numbers, each with a
// jump-anchor so the user can scroll the ExecuteTree to that group.
// Hidden when no full-delete group exists.

import {
  EXECUTE_ALL_DELETE_BANNER,
  executeAllDeleteJumpTestid,
} from "@/testids";

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
  if (allDeleteGroupIds.length === 0) return null;

  return (
    <div
      data-testid={EXECUTE_ALL_DELETE_BANNER}
      className="flex items-start gap-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
      role="alert"
    >
      <span className="font-semibold shrink-0">Warning:</span>
      <span>
        All files in{" "}
        {allDeleteGroupIds.length === 1 ? "group" : "groups"}{" "}
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
        ))}{" "}
        are marked for deletion. Review before executing.
      </span>
    </div>
  );
}
