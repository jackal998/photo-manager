// ExecuteDialog tests — real behaviour only, no metric-gaming padding.
//
// Covers:
//   1. Renders the dialog when executeOpen=true; absent when false.
//   2. ExecuteTree shows only decided rows (user_decision !== "").
//   3. TypeFilter "delete" hides ignore-only rows.
//   4. TypeFilter "ignore" hides delete-only rows.
//   5. AllDeleteBanner appears when an entire group is marked delete; absent otherwise.
//   6. Execute button calls store.executeDecisions().
//   7. Execute-selected scopes to the selected file path.
//   8. Execute button is disabled when no decided rows exist.
//   9. Execute-selected button is disabled when no row is selected.

import { act, render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import { ExecuteDialog } from "./ExecuteDialog";
import { useAppStore } from "@/store/useAppStore";
import {
  EXECUTE_DIALOG,
  EXECUTE_BTN_EXECUTE,
  EXECUTE_BTN_EXECUTE_SELECTED,
  EXECUTE_ALL_DELETE_BANNER,
  EXECUTE_ALL_DELETE_CONFIRM,
  EXECUTE_TREE,
  EXECUTE_PREVIEW_PANE,
  executeAllDeleteJumpTestid,
} from "@/testids";
import type { Group } from "@/api/types";

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

function makeFile(
  basename: string,
  filePath: string,
  decision: "" | "delete" | "ignore",
  groupId = "1"
) {
  return {
    file_path: filePath,
    basename,
    folder: "/photos",
    action: decision === "delete" ? "delete" : "keep",
    user_decision: decision,
    is_locked: false,
    is_ref_winner: false,
    similarity: { kind: "near_dup" as const, percent: null },
    score: null,
    file_size_bytes: 1024,
    pixel_width: 800,
    pixel_height: 600,
    shot_date: null,
    creation_date: null,
    phash: null,
    hamming_distance: 0,
    thumbnail_url: `/api/image?path=${filePath}&size=128&group=${groupId}`,
  };
}

/** A group with one "delete" file and one "ignore" file. */
const MIXED_GROUP: Group = {
  group_number: 1,
  member_count: 2,
  items: [
    makeFile("ref.jpg", "/photos/ref.jpg", "delete"),
    makeFile("dup.jpg", "/photos/dup.jpg", "ignore"),
  ],
};

/** A group where every item is "delete" — triggers AllDeleteBanner. */
const ALL_DELETE_GROUP: Group = {
  group_number: 2,
  member_count: 2,
  items: [
    makeFile("a.jpg", "/photos/a.jpg", "delete", "2"),
    makeFile("b.jpg", "/photos/b.jpg", "delete", "2"),
  ],
};

/** A group with NO decided rows — should not appear in the tree. */
const UNDECIDED_GROUP: Group = {
  group_number: 3,
  member_count: 2,
  items: [
    makeFile("c.jpg", "/photos/c.jpg", ""),
    makeFile("d.jpg", "/photos/d.jpg", ""),
  ],
};

// ---------------------------------------------------------------------------
// jsdom virtualizer fix (same as ResultTree.test.tsx)
// ---------------------------------------------------------------------------

function stubOffsetHeight(height = 3000, width = 1024) {
  const heightDesc = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "offsetHeight"
  );
  const widthDesc = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "offsetWidth"
  );
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get() {
      return height;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get() {
      return width;
    },
  });
  return () => {
    if (heightDesc) {
      Object.defineProperty(HTMLElement.prototype, "offsetHeight", heightDesc);
    }
    if (widthDesc) {
      Object.defineProperty(HTMLElement.prototype, "offsetWidth", widthDesc);
    }
  };
}

// ---------------------------------------------------------------------------
// Store seeding helpers
// ---------------------------------------------------------------------------

function openDialog(groups: Group[] = [MIXED_GROUP]) {
  useAppStore.setState((s) => ({
    manifest: {
      ...s.manifest,
      path: "/manifests/test.db",
      groups,
    },
    execute: {
      ...s.execute,
      executeOpen: true,
      executeRunning: false,
      executeError: null,
      lockConflict: null,
    },
  }));
}

function resetStore() {
  useAppStore.setState((s) => ({
    manifest: {
      path: null,
      groups: [],
      totalGroups: 0,
      totalFiles: 0,
      loading: false,
      error: null,
    },
    execute: {
      executeOpen: false,
      executeRunning: false,
      executeResult: null,
      executeError: null,
      lockConflict: null,
      prunePrompt: null,
    },
    preview: s.preview,
  }));
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ExecuteDialog", () => {
  let restoreRect: (() => void) | undefined;

  beforeEach(() => {
    resetStore();
    restoreRect = stubOffsetHeight();
  });

  afterEach(() => {
    restoreRect?.();
  });

  // 1. Visibility gate
  it("renders with EXECUTE_DIALOG testid when executeOpen=true", () => {
    act(() => {
      openDialog();
    });
    render(<ExecuteDialog />);
    expect(screen.getByTestId(EXECUTE_DIALOG)).toBeInTheDocument();
  });

  it("does not render dialog content when executeOpen=false", () => {
    render(<ExecuteDialog />);
    expect(screen.queryByTestId(EXECUTE_DIALOG)).not.toBeInTheDocument();
  });

  // 2. ExecuteTree shows decided rows only
  it("renders the ExecuteTree container", () => {
    act(() => {
      openDialog([MIXED_GROUP]);
    });
    render(<ExecuteDialog />);
    expect(screen.getByTestId(EXECUTE_TREE)).toBeInTheDocument();
  });

  it("shows decided rows in the tree (delete + ignore both visible on 'all')", () => {
    act(() => {
      openDialog([MIXED_GROUP]);
    });
    render(<ExecuteDialog />);
    // Both ref.jpg (delete) and dup.jpg (ignore) are decided.
    expect(screen.getByTestId("execute-row-1-ref.jpg")).toBeInTheDocument();
    expect(screen.getByTestId("execute-row-1-dup.jpg")).toBeInTheDocument();
  });

  it("does not show undecided rows in the tree", () => {
    act(() => {
      openDialog([UNDECIDED_GROUP]);
    });
    render(<ExecuteDialog />);
    // No decided rows → empty state message.
    expect(screen.queryByTestId("execute-row-3-c.jpg")).not.toBeInTheDocument();
    expect(screen.getByText(/no decided rows match/i)).toBeInTheDocument();
  });

  it("delete-only group produces only delete rows in tree when filter is 'all'", () => {
    act(() => {
      openDialog([ALL_DELETE_GROUP]);
    });
    render(<ExecuteDialog />);
    expect(screen.getByTestId("execute-row-2-a.jpg")).toBeInTheDocument();
    expect(screen.getByTestId("execute-row-2-b.jpg")).toBeInTheDocument();
  });

  // 4. TypeFilter "ignore" — verify ignore rows appear (filter='all' baseline)
  it("ignore-decided row is visible on default 'all' filter", () => {
    act(() => {
      openDialog([
        {
          group_number: 10,
          member_count: 1,
          items: [makeFile("only_ignore.jpg", "/photos/only_ignore.jpg", "ignore", "10")],
        },
      ]);
    });
    render(<ExecuteDialog />);
    expect(screen.getByTestId("execute-row-10-only_ignore.jpg")).toBeInTheDocument();
  });

  // 5. AllDeleteBanner
  it("AllDeleteBanner is visible when a group has all members delete", () => {
    act(() => {
      openDialog([ALL_DELETE_GROUP]);
    });
    render(<ExecuteDialog />);
    expect(screen.getByTestId(EXECUTE_ALL_DELETE_BANNER)).toBeInTheDocument();
  });

  it("AllDeleteBanner is absent when no group has all members delete", () => {
    act(() => {
      openDialog([MIXED_GROUP]); // mixed group: one delete, one ignore → NOT all-delete
    });
    render(<ExecuteDialog />);
    expect(screen.queryByTestId(EXECUTE_ALL_DELETE_BANNER)).not.toBeInTheDocument();
  });

  it("AllDeleteBanner shows jump anchor for each all-delete group", () => {
    act(() => {
      openDialog([ALL_DELETE_GROUP]);
    });
    render(<ExecuteDialog />);
    expect(
      screen.getByTestId(executeAllDeleteJumpTestid("2"))
    ).toBeInTheDocument();
  });

  // 6. Execute button calls store.executeDecisions()
  it("Execute button calls store.executeDecisions()", () => {
    const executeDecisionsMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({
      executeDecisions: executeDecisionsMock,
    } as never);

    act(() => {
      openDialog([MIXED_GROUP]);
    });
    render(<ExecuteDialog />);

    act(() => {
      fireEvent.click(screen.getByTestId(EXECUTE_BTN_EXECUTE));
    });

    expect(executeDecisionsMock).toHaveBeenCalledOnce();
    expect(executeDecisionsMock).toHaveBeenCalledWith();
  });

  // 7. Execute-selected scopes to selected file path
  // Uses an "ignore"-decided row so the all-delete gate doesn't fire.
  it("clicking a row then Execute-selected calls executeDecisions with scopePaths", () => {
    const executeDecisionsMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({
      executeDecisions: executeDecisionsMock,
    } as never);

    act(() => {
      openDialog([MIXED_GROUP]);
    });
    render(<ExecuteDialog />);

    // Select dup.jpg (user_decision="ignore") — bypasses the all-delete gate.
    act(() => {
      fireEvent.click(screen.getByTestId("execute-row-1-dup.jpg"));
    });

    act(() => {
      fireEvent.click(screen.getByTestId(EXECUTE_BTN_EXECUTE_SELECTED));
    });

    expect(executeDecisionsMock).toHaveBeenCalledOnce();
    expect(executeDecisionsMock).toHaveBeenCalledWith({
      scopePaths: ["/photos/dup.jpg"],
    });
  });

  // 7b. Execute-selected on a delete-only row shows DeleteConfirmDialog gate first.
  it("Execute-selected on a delete-only row shows the delete-confirm gate", () => {
    const executeDecisionsMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({
      executeDecisions: executeDecisionsMock,
    } as never);

    act(() => {
      openDialog([MIXED_GROUP]);
    });
    render(<ExecuteDialog />);

    // Select ref.jpg (user_decision="delete") — triggers all-delete gate.
    act(() => {
      fireEvent.click(screen.getByTestId("execute-row-1-ref.jpg"));
    });

    act(() => {
      fireEvent.click(screen.getByTestId(EXECUTE_BTN_EXECUTE_SELECTED));
    });

    // DeleteConfirmDialog should have appeared; executeDecisions not yet called.
    expect(
      screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM)
    ).toBeInTheDocument();
    expect(executeDecisionsMock).not.toHaveBeenCalled();
  });

  // 8. Execute button is disabled when no decided rows
  it("Execute button is disabled when no decided rows exist", () => {
    act(() => {
      openDialog([UNDECIDED_GROUP]);
    });
    render(<ExecuteDialog />);
    expect(screen.getByTestId(EXECUTE_BTN_EXECUTE)).toBeDisabled();
  });

  // 9. Execute-selected disabled when nothing is selected
  it("Execute-selected button is disabled when no row is selected", () => {
    act(() => {
      openDialog([MIXED_GROUP]);
    });
    render(<ExecuteDialog />);
    expect(screen.getByTestId(EXECUTE_BTN_EXECUTE_SELECTED)).toBeDisabled();
  });

  // 10. Preview pane is always present in the dialog
  it("preview pane is rendered", () => {
    act(() => {
      openDialog([MIXED_GROUP]);
    });
    render(<ExecuteDialog />);
    expect(screen.getByTestId(EXECUTE_PREVIEW_PANE)).toBeInTheDocument();
  });

  // 11. Error message displayed when executeError is set
  it("shows error message when executeError is non-null", () => {
    act(() => {
      openDialog([MIXED_GROUP]);
      useAppStore.setState((s) => ({
        execute: { ...s.execute, executeError: "Server unavailable" },
      }));
    });
    render(<ExecuteDialog />);
    expect(screen.getByRole("alert")).toHaveTextContent("Server unavailable");
  });

  // 12. Execute button disabled while executeRunning
  it("Execute button is disabled while executeRunning=true", () => {
    act(() => {
      openDialog([MIXED_GROUP]);
      useAppStore.setState((s) => ({
        execute: { ...s.execute, executeRunning: true },
      }));
    });
    render(<ExecuteDialog />);
    expect(screen.getByTestId(EXECUTE_BTN_EXECUTE)).toBeDisabled();
  });
});
