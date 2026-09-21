// Consumer hook for GroupMediaProvider — in its own file so ESLint's
// react-refresh/only-export-components rule is satisfied (hooks are not
// components; exporting a hook alongside a component confuses HMR).

import { useContext } from "react";
import {
  GroupMediaContext,
  type GroupMediaContextValue,
} from "../components/GroupMediaContext";

export type { GroupMediaContextValue };

/**
 * Return the GroupMediaContext value. Must be used inside a
 * <GroupMediaProvider>. Throws if called outside the tree (fail-fast).
 */
export function useGroupMedia(): GroupMediaContextValue {
  const ctx = useContext(GroupMediaContext);
  if (ctx === null) {
    throw new Error("useGroupMedia must be used inside <GroupMediaProvider>");
  }
  return ctx;
}
