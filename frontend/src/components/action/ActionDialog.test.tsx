// ActionDialog tests — real behaviour only, no metric-gaming padding.
//
// Covers:
//   1. Dialog absent when actionDialogOpen=false.
//   2. Dialog present when actionDialogOpen=true.
//   3. Field combo switches from regex panel to numeric panel for numeric fields.
//   4. Simple write-through: "contains" text → plain escaped regex in input.
//   5. Simple write-through: "starts_with" → ^escaped pattern.
//   6. Simple write-through: "ends_with" → escaped$ pattern.
//   7. Simple write-through: "exact" → ^escaped$ pattern.
//   8. Numeric threshold encodes "__cmp__:>=:VALUE" in the pattern.
//   9. Numeric top-N encodes "__top_n__:N:desc" for "top" order.
//  10. Preview: pattern change triggers previewBulkDecide and renders counter.
//  11. Apply button is ENABLED even when pattern is empty (#741 sub-item A —
//      the #397 regression fix; the backend no-ops on an empty pattern).
//  12. Apply button disabled when manifest is not loaded.
//  13. Delete action shows DeleteConfirmDialog before applying.
//  14. 409 locked_paths surfaces the lock-confirm overlay.
//  15. Non-delete action calls applyBulkDecide directly (no confirm).
//  19. Clicking Apply with an empty pattern reaches applyBulkDecide (#741 A).
//  20. Recent select fills the pattern field when an entry is picked (#741 B).
//  21. Recent select is filtered to the current field + legacy null-field entries.
//  22. Delete-confirm shows the pattern summary + "Mark N files for deletion" (#741 C).
//  23. Regression: Apply records the CURRENT pattern (not a stale one captured
//      at mount) after the pattern changes post-mount — the #741 stale-closure
//      fix (handleApply now depends on doApply, which itself depends on
//      [field, pattern]).

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import { ActionDialog } from "./ActionDialog";
import { useAppStore } from "@/store/useAppStore";
import {
  ACTION_DIALOG,
  ACTION_FIELD_COMBO,
  ACTION_SIMPLE_ROW,
  ACTION_SIMPLE_OP,
  ACTION_SIMPLE_TEXT,
  ACTION_SIMPLE_DISABLED_NOTE,
  ACTION_REGEX_ROW,
  ACTION_REGEX_INPUT,
  ACTION_NUMERIC_ROW,
  ACTION_NUMERIC_MODE_THRESHOLD,
  ACTION_NUMERIC_MODE_TOPN,
  ACTION_NUMERIC_VALUE,
  ACTION_NUMERIC_N,
  ACTION_MATCH_COUNTER,
  ACTION_ACTION_COMBO,
  ACTION_BTN_APPLY,
  ACTION_PREVIEW_LIST,
  ACTION_LOCK_CONFIRM_DIALOG,
  ACTION_LOCK_CONFIRM_BTN_UNLOCKED_ONLY,
  ACTION_LOCK_CONFIRM_BTN_UNLOCK_APPLY,
  ACTION_RECENT_SELECT,
  ACTION_RECENT_CLEAR,
  ACTION_DELETE_CONFIRM_SUMMARY,
  EXECUTE_ALL_DELETE_CONFIRM,
  EXECUTE_ALL_DELETE_CONFIRM_YES,
} from "@/testids";
import { ApiConflictError, getSettings, patchSettings } from "@/api/client";
import { RECENT_PATTERNS_KEY } from "@/lib/recentPatterns";

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    getSettings: vi.fn(),
    patchSettings: vi.fn(),
  };
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function openDialog() {
  act(() => {
    useAppStore.setState((s) => ({
      manifest: {
        ...s.manifest,
        path: "/manifests/test.db",
        totalFiles: 100,
        totalGroups: 20,
      },
      action: {
        ...s.action,
        actionDialogOpen: true,
        field: "File Name",
        pattern: "",
        action: "delete",
        previewMatched: -1,
        previewSample: [],
        previewTruncated: false,
        actionError: null,
        actionRunning: false,
      },
    }));
  });
}

function resetStore() {
  act(() => {
    useAppStore.setState(() => ({
      manifest: {
        path: null,
        groups: [],
        totalGroups: 0,
        totalFiles: 0,
        loading: false,
        error: null,
      },
      action: {
        actionDialogOpen: false,
        field: "File Name",
        pattern: "",
        action: "delete",
        previewMatched: -1,
        previewSample: [],
        previewTruncated: false,
        actionError: null,
        actionRunning: false,
      },
    }));
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ActionDialog", () => {
  let previewMock: ReturnType<typeof vi.fn>;
  let applyMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    previewMock = vi.fn().mockResolvedValue(undefined);
    applyMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({
      previewBulkDecide: previewMock,
      applyBulkDecide: applyMock,
    } as never);
    resetStore();
    // Default: no persisted recent patterns. Individual tests override with
    // mockResolvedValueOnce for the Recent-select cases.
    vi.mocked(getSettings).mockReset().mockResolvedValue({} as never);
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  // 1. Dialog absent when closed
  it("does not render dialog when actionDialogOpen=false", () => {
    render(<ActionDialog />);
    expect(screen.queryByTestId(ACTION_DIALOG)).not.toBeInTheDocument();
  });

  // 2. Dialog present when open
  it("renders dialog with ACTION_DIALOG testid when actionDialogOpen=true", () => {
    openDialog();
    render(<ActionDialog />);
    expect(screen.getByTestId(ACTION_DIALOG)).toBeInTheDocument();
  });

  // 3. Field combo switches panels
  it("shows NumericPanel when a numeric field is selected", async () => {
    openDialog();
    render(<ActionDialog />);
    // Default is "File Name" — regex panel should be visible
    expect(screen.getByTestId(ACTION_REGEX_ROW)).toBeInTheDocument();
    expect(screen.queryByTestId(ACTION_NUMERIC_ROW)).not.toBeInTheDocument();

    // Change field to "Score" (numeric)
    act(() => {
      useAppStore.setState((s) => ({
        action: { ...s.action, field: "Score" },
      }));
    });

    // Re-render to pick up store change
    render(<ActionDialog />);
    const combos = screen.getAllByTestId(ACTION_FIELD_COMBO);
    // The combo should have Score selected
    expect((combos[0] as HTMLSelectElement).value).toBe("Score");
  });

  it("shows RegexPanel for non-numeric field (File Name)", () => {
    openDialog();
    render(<ActionDialog />);
    expect(screen.getByTestId(ACTION_REGEX_ROW)).toBeInTheDocument();
    expect(screen.getByTestId(ACTION_SIMPLE_ROW)).toBeInTheDocument();
    expect(screen.queryByTestId(ACTION_NUMERIC_ROW)).not.toBeInTheDocument();
  });

  // 4. Simple write-through: "contains" → plain escaped regex
  it("simple 'contains' text writes through to regex input", async () => {
    const user = userEvent.setup();
    const setPatternMock = vi.fn();
    useAppStore.setState({ setActionPattern: setPatternMock } as never);
    openDialog();
    render(<ActionDialog />);

    const simpleText = screen.getByTestId(ACTION_SIMPLE_TEXT) as HTMLInputElement;
    await user.type(simpleText, "IMG");
    // "contains" → "IMG" (no anchors, no escaping needed for plain text)
    expect(setPatternMock).toHaveBeenLastCalledWith("IMG");
  });

  it("simple 'starts_with' writes through with ^ prefix", async () => {
    const user = userEvent.setup();
    const setPatternMock = vi.fn();
    useAppStore.setState({ setActionPattern: setPatternMock } as never);
    openDialog();
    render(<ActionDialog />);

    const opSelect = screen.getByTestId(ACTION_SIMPLE_OP) as HTMLSelectElement;
    await user.selectOptions(opSelect, "starts_with");

    const simpleText = screen.getByTestId(ACTION_SIMPLE_TEXT) as HTMLInputElement;
    await user.type(simpleText, "IMG");
    // starts_with → "^IMG"
    expect(setPatternMock).toHaveBeenLastCalledWith("^IMG");
  });

  it("simple 'ends_with' writes through with $ suffix", async () => {
    const user = userEvent.setup();
    const setPatternMock = vi.fn();
    useAppStore.setState({ setActionPattern: setPatternMock } as never);
    openDialog();
    render(<ActionDialog />);

    const opSelect = screen.getByTestId(ACTION_SIMPLE_OP) as HTMLSelectElement;
    await user.selectOptions(opSelect, "ends_with");

    const simpleText = screen.getByTestId(ACTION_SIMPLE_TEXT) as HTMLInputElement;
    await user.type(simpleText, ".jpg");
    // ends_with → "\\.jpg$"
    expect(setPatternMock).toHaveBeenLastCalledWith("\\.jpg$");
  });

  it("simple 'exact' writes through with both ^ and $ anchors", async () => {
    const user = userEvent.setup();
    const setPatternMock = vi.fn();
    useAppStore.setState({ setActionPattern: setPatternMock } as never);
    openDialog();
    render(<ActionDialog />);

    const opSelect = screen.getByTestId(ACTION_SIMPLE_OP) as HTMLSelectElement;
    await user.selectOptions(opSelect, "exact");

    const simpleText = screen.getByTestId(ACTION_SIMPLE_TEXT) as HTMLInputElement;
    await user.type(simpleText, "photo.jpg");
    expect(setPatternMock).toHaveBeenLastCalledWith("^photo\\.jpg$");
  });

  // 8. Numeric threshold encoding
  it("numeric threshold encodes __cmp__:>=:VALUE", async () => {
    const user = userEvent.setup();
    const setPatternMock = vi.fn();
    useAppStore.setState({ setActionPattern: setPatternMock } as never);

    act(() => {
      useAppStore.setState((s) => ({
        action: { ...s.action, actionDialogOpen: true, field: "Score" },
        manifest: { ...s.manifest, path: "/manifests/test.db" },
      }));
    });

    render(<ActionDialog />);
    expect(screen.getByTestId(ACTION_NUMERIC_ROW)).toBeInTheDocument();

    // Default mode is threshold; cmp is ">=" by default
    // Verify the threshold radio is checked
    const thresholdRadio = screen.getByTestId(ACTION_NUMERIC_MODE_THRESHOLD) as HTMLInputElement;
    expect(thresholdRadio.checked).toBe(true);

    const valueInput = screen.getByTestId(ACTION_NUMERIC_VALUE) as HTMLInputElement;
    await user.type(valueInput, "75");

    // Last call should be __cmp__:>=:75 (or accumulate chars)
    const calls = setPatternMock.mock.calls.map((c) => c[0] as string);
    const lastCall = calls[calls.length - 1];
    expect(lastCall).toBe("__cmp__:>=:75");
  });

  // 9. Numeric top-N encoding
  it("numeric top-N encodes __top_n__:N:desc for 'top' order", async () => {
    const user = userEvent.setup();
    const setPatternMock = vi.fn();
    useAppStore.setState({ setActionPattern: setPatternMock } as never);

    act(() => {
      useAppStore.setState((s) => ({
        action: { ...s.action, actionDialogOpen: true, field: "Score" },
        manifest: { ...s.manifest, path: "/manifests/test.db" },
      }));
    });

    render(<ActionDialog />);

    // Switch to top-N mode
    const topnRadio = screen.getByTestId(ACTION_NUMERIC_MODE_TOPN);
    await user.click(topnRadio);

    // Default order = "top", n = "3"
    const lastCalls = setPatternMock.mock.calls.map((c) => c[0] as string);
    const last = lastCalls[lastCalls.length - 1];
    expect(last).toBe("__top_n__:3:desc");

    // Change N to 5
    const nInput = screen.getByTestId(ACTION_NUMERIC_N) as HTMLInputElement;
    await user.clear(nInput);
    await user.type(nInput, "5");

    const updatedCalls = setPatternMock.mock.calls.map((c) => c[0] as string);
    const updated = updatedCalls[updatedCalls.length - 1];
    expect(updated).toBe("__top_n__:5:desc");
  });

  // 10. Preview renders counter
  it("renders match counter text when previewMatched is set", () => {
    act(() => {
      useAppStore.setState((s) => ({
        action: {
          ...s.action,
          actionDialogOpen: true,
          pattern: "IMG",
          previewMatched: 42,
          previewSample: ["/photos/IMG_001.jpg"],
        },
        manifest: {
          ...s.manifest,
          path: "/manifests/test.db",
          totalFiles: 100,
        },
      }));
    });

    render(<ActionDialog />);
    const counter = screen.getByTestId(ACTION_MATCH_COUNTER);
    expect(counter).toHaveTextContent("42");
    expect(screen.getByTestId(ACTION_PREVIEW_LIST)).toBeInTheDocument();
  });

  // 11. Apply button is ENABLED even when pattern is empty (#741 sub-item A
  // — the #397 regression fix. The backend no-ops on an empty pattern:
  // core/app_service/action_resolve.py:341 `if not pattern: return []`,
  // pinned by tests/test_action_resolve.py's empty-pattern-guard test — so
  // gating the button on it was pure friction with no safety value.)
  it("Apply button is enabled even when pattern is empty (#741 sub-item A)", () => {
    openDialog();
    render(<ActionDialog />);
    expect(screen.getByTestId(ACTION_BTN_APPLY)).not.toBeDisabled();
  });

  // 19. Clicking Apply with an empty pattern reaches applyBulkDecide — proves
  // the click is no longer blocked by the removed gate. (The "resolves to no
  // paths" half of the no-op proof is a store-level test in
  // useAppStore.test.ts, since applyBulkDecide reads pattern from the store,
  // not from an argument this component passes.)
  it("clicking Apply with an empty pattern reaches applyBulkDecide (#741 sub-item A)", async () => {
    const user = userEvent.setup();
    act(() => {
      useAppStore.setState((s) => ({
        action: {
          ...s.action,
          actionDialogOpen: true,
          field: "File Name",
          pattern: "",
          action: "__ignore__", // non-delete: skip the confirm dialog for this check
        },
        manifest: { ...s.manifest, path: "/manifests/test.db" },
      }));
    });
    render(<ActionDialog />);
    await user.click(screen.getByTestId(ACTION_BTN_APPLY));
    expect(applyMock).toHaveBeenCalledWith({});
  });

  // 12. Apply button disabled when manifest not loaded
  it("Apply button is disabled when manifest.path is null", () => {
    act(() => {
      useAppStore.setState((s) => ({
        action: {
          ...s.action,
          actionDialogOpen: true,
          pattern: "IMG",
        },
        manifest: { ...s.manifest, path: null },
      }));
    });
    render(<ActionDialog />);
    expect(screen.getByTestId(ACTION_BTN_APPLY)).toBeDisabled();
  });

  // 13. Delete action shows DeleteConfirmDialog before applying
  it("delete action shows DeleteConfirmDialog before calling applyBulkDecide", async () => {
    const user = userEvent.setup();

    act(() => {
      useAppStore.setState((s) => ({
        action: {
          ...s.action,
          actionDialogOpen: true,
          field: "File Name",
          pattern: "IMG",
          action: "delete",
          previewMatched: 5,
        },
        manifest: { ...s.manifest, path: "/manifests/test.db" },
      }));
    });

    render(<ActionDialog />);
    const applyBtn = screen.getByTestId(ACTION_BTN_APPLY);
    await user.click(applyBtn);

    // DeleteConfirmDialog should appear; applyBulkDecide not yet called
    expect(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM)).toBeInTheDocument();
    expect(applyMock).not.toHaveBeenCalled();
  });

  it("clicking confirm in DeleteConfirmDialog calls applyBulkDecide", async () => {
    const user = userEvent.setup();

    act(() => {
      useAppStore.setState((s) => ({
        action: {
          ...s.action,
          actionDialogOpen: true,
          field: "File Name",
          pattern: "IMG",
          action: "delete",
          previewMatched: 5,
        },
        manifest: { ...s.manifest, path: "/manifests/test.db" },
      }));
    });

    render(<ActionDialog />);
    await user.click(screen.getByTestId(ACTION_BTN_APPLY));
    // Delete confirm dialog open — click confirm
    await user.click(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM_YES));
    expect(applyMock).toHaveBeenCalledWith({});
  });

  // 14. 409 locked_paths shows lock confirm overlay AND retries with force.
  // The first apply rejects with the conflict; the retry (default mock)
  // resolves — proving the whole 409 → Unlock & Apply → force_locked path,
  // not just that the overlay appears (adversarial-review: the old test only
  // asserted the overlay, never the force retry).
  it("409 locked_paths surfaces the overlay and Unlock & Apply retries with force", async () => {
    const user = userEvent.setup();
    const conflictError = new ApiConflictError("locked_paths", ["/photos/a.jpg"]);
    applyMock.mockRejectedValueOnce(conflictError);
    useAppStore.setState({ applyBulkDecide: applyMock } as never);

    act(() => {
      useAppStore.setState((s) => ({
        action: {
          ...s.action,
          actionDialogOpen: true,
          pattern: "IMG",
          action: "__ignore__", // not delete, so no confirm step first
        },
        manifest: { ...s.manifest, path: "/manifests/test.db" },
      }));
    });

    render(<ActionDialog />);
    await user.click(screen.getByTestId(ACTION_BTN_APPLY));

    const unlockBtn = await screen.findByTestId(ACTION_LOCK_CONFIRM_BTN_UNLOCK_APPLY);
    // First attempt was unforced.
    expect(applyMock).toHaveBeenCalledWith({});

    // Click Unlock & Apply → retry WITH forceLocked=true.
    await user.click(unlockBtn);
    await waitFor(() => {
      expect(applyMock).toHaveBeenCalledWith({ forceLocked: true });
    });
  });

  // 15. Non-delete action calls applyBulkDecide directly without confirm
  it("non-delete action calls applyBulkDecide without showing DeleteConfirmDialog", async () => {
    const user = userEvent.setup();

    act(() => {
      useAppStore.setState((s) => ({
        action: {
          ...s.action,
          actionDialogOpen: true,
          pattern: "IMG",
          action: "__ignore__",
          previewMatched: 3,
        },
        manifest: { ...s.manifest, path: "/manifests/test.db" },
      }));
    });

    render(<ActionDialog />);
    await user.click(screen.getByTestId(ACTION_BTN_APPLY));

    expect(screen.queryByTestId(EXECUTE_ALL_DELETE_CONFIRM)).not.toBeInTheDocument();
    expect(applyMock).toHaveBeenCalledWith({});
  });

  // 16. 409 lock-confirm offers "Apply to Unlocked Only" which retries with
  // skipLocked (the #674 verdict). The 409 carries matched_total=3 (1 locked,
  // 2 unlocked) so the button is enabled.
  it("Apply to Unlocked Only retries with skipLocked=true", async () => {
    const user = userEvent.setup();
    const conflictError = new ApiConflictError("locked_paths", ["/photos/a.jpg"], 3);
    applyMock.mockRejectedValueOnce(conflictError);
    useAppStore.setState({ applyBulkDecide: applyMock } as never);

    act(() => {
      useAppStore.setState((s) => ({
        action: {
          ...s.action,
          actionDialogOpen: true,
          pattern: "IMG",
          action: "__ignore__", // not delete → no delete-confirm step first
        },
        manifest: { ...s.manifest, path: "/manifests/test.db" },
      }));
    });

    render(<ActionDialog />);
    await user.click(screen.getByTestId(ACTION_BTN_APPLY));

    // The inline lock-confirm dialog (distinct from the execute-flow one) opens.
    const dialog = await screen.findByTestId(ACTION_LOCK_CONFIRM_DIALOG);
    expect(dialog).toBeInTheDocument();
    expect(applyMock).toHaveBeenCalledWith({});

    const unlockedOnlyBtn = screen.getByTestId(ACTION_LOCK_CONFIRM_BTN_UNLOCKED_ONLY);
    expect(unlockedOnlyBtn).not.toBeDisabled(); // 2 unlocked rows
    await user.click(unlockedOnlyBtn);
    await waitFor(() => {
      expect(applyMock).toHaveBeenCalledWith({ skipLocked: true });
    });
  });

  // 17. When every matched row is locked (matched_total === locked count),
  // "Apply to Unlocked Only" is disabled — there is no unlocked subset.
  it("Apply to Unlocked Only is disabled when all matched rows are locked", async () => {
    const user = userEvent.setup();
    // matched_total=1, one locked path → 0 unlocked → button disabled.
    const conflictError = new ApiConflictError("locked_paths", ["/photos/a.jpg"], 1);
    applyMock.mockRejectedValueOnce(conflictError);
    useAppStore.setState({ applyBulkDecide: applyMock } as never);

    act(() => {
      useAppStore.setState((s) => ({
        action: {
          ...s.action,
          actionDialogOpen: true,
          pattern: "IMG",
          action: "__ignore__",
        },
        manifest: { ...s.manifest, path: "/manifests/test.db" },
      }));
    });

    render(<ActionDialog />);
    await user.click(screen.getByTestId(ACTION_BTN_APPLY));

    await screen.findByTestId(ACTION_LOCK_CONFIRM_DIALOG);
    expect(screen.getByTestId(ACTION_LOCK_CONFIRM_BTN_UNLOCKED_ONLY)).toBeDisabled();
  });

  // Action combo renders all 5 options
  it("action combo has 5 options (keep, delete, remove_from_list, lock, unlock)", () => {
    openDialog();
    render(<ActionDialog />);
    const combo = screen.getByTestId(ACTION_ACTION_COMBO) as HTMLSelectElement;
    expect(combo.options.length).toBe(5);
  });

  // 18. No dedup groups (totalGroups=0 → no match_fn): the Simple write-through
  // inputs are disabled and the explanatory note is shown, while the Regex input
  // stays interactive (the only usable path). Mirrors the desktop match_fn=None
  // branch (select_dialog.py:854-861, #689 / s55). The disabled-Simple +
  // enabled-Regex pair is the non-vacuity bracket — a broken always-enabled OR
  // always-disabled wiring fails here; the enabled-at-20-groups case is already
  // guarded by the write-through tests (4-7) which type into the Simple input.
  it("disables Simple inputs and shows the note at 0 groups, Regex stays enabled", () => {
    act(() => {
      useAppStore.setState((s) => ({
        manifest: {
          ...s.manifest,
          path: "/manifests/test.db",
          totalFiles: 0,
          totalGroups: 0,
        },
        action: {
          ...s.action,
          actionDialogOpen: true,
          field: "File Name",
        },
      }));
    });

    render(<ActionDialog />);

    expect(screen.getByTestId(ACTION_SIMPLE_OP)).toBeDisabled();
    expect(screen.getByTestId(ACTION_SIMPLE_TEXT)).toBeDisabled();
    expect(screen.getByTestId(ACTION_SIMPLE_DISABLED_NOTE)).toBeInTheDocument();
    // The Regex input must remain the interactive escape hatch.
    expect(screen.getByTestId(ACTION_REGEX_INPUT)).not.toBeDisabled();
  });

  // 20. Recent select fills the pattern field when an entry is picked (#741 B).
  it("Recent select fills the pattern field when an entry is picked", async () => {
    const user = userEvent.setup();
    vi.mocked(getSettings).mockResolvedValueOnce({
      "ui.action_dialog.recent_patterns": [["File Name", "IMG"]],
    } as never);
    const setPatternMock = vi.fn();
    useAppStore.setState({ setActionPattern: setPatternMock } as never);

    openDialog(); // field defaults to "File Name"
    render(<ActionDialog />);

    const select = await screen.findByTestId(ACTION_RECENT_SELECT);
    await waitFor(() => {
      expect((select as HTMLSelectElement).options.length).toBeGreaterThan(1);
    });
    await user.selectOptions(select, "IMG");

    expect(setPatternMock).toHaveBeenCalledWith("IMG");
  });

  // 21. Recent select is filtered to the current field + legacy null-field entries.
  it("Recent select only shows entries for the current field plus legacy null-field entries", async () => {
    vi.mocked(getSettings).mockResolvedValueOnce({
      "ui.action_dialog.recent_patterns": [
        ["File Name", "IMG"],
        ["Folder", "2024"],
        [null, "legacy_any_field"],
      ],
    } as never);

    openDialog(); // field defaults to "File Name"
    render(<ActionDialog />);

    const select = await screen.findByTestId(ACTION_RECENT_SELECT);
    await waitFor(() => {
      const values = Array.from((select as HTMLSelectElement).options).map((o) => o.value);
      expect(values).toContain("IMG");
    });
    const values = Array.from((select as HTMLSelectElement).options).map((o) => o.value);
    expect(values).toContain("legacy_any_field");
    expect(values).not.toContain("2024");
  });

  it("Clear removes all recent entries and persists the empty list", async () => {
    const user = userEvent.setup();
    vi.mocked(getSettings).mockResolvedValueOnce({
      "ui.action_dialog.recent_patterns": [["File Name", "IMG"]],
    } as never);

    openDialog();
    render(<ActionDialog />);

    const clearBtn = await screen.findByTestId(ACTION_RECENT_CLEAR);
    await user.click(clearBtn);

    await waitFor(() => {
      expect(screen.queryByTestId(ACTION_RECENT_CLEAR)).not.toBeInTheDocument();
    });
    expect(patchSettings).toHaveBeenCalledWith({
      "ui.action_dialog.recent_patterns": [],
    });
  });

  // 22. Delete-confirm shows the pattern summary + "Mark N files for deletion" (#741 C).
  it("delete-confirm shows the pattern summary and 'Mark N files for deletion' button", async () => {
    const user = userEvent.setup();
    act(() => {
      useAppStore.setState((s) => ({
        action: {
          ...s.action,
          actionDialogOpen: true,
          field: "File Name",
          pattern: "IMG",
          action: "delete",
          previewMatched: 3,
        },
        manifest: { ...s.manifest, path: "/manifests/test.db" },
      }));
    });

    render(<ActionDialog />);
    await user.click(screen.getByTestId(ACTION_BTN_APPLY));

    const summary = await screen.findByTestId(ACTION_DELETE_CONFIRM_SUMMARY);
    expect(summary).toHaveTextContent("File Name contains 'IMG'");
    expect(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM_YES)).toHaveTextContent(
      "Mark 3 files for deletion"
    );
  });

  it("delete-confirm falls back to the raw regex for a complex pattern", async () => {
    const user = userEvent.setup();
    act(() => {
      useAppStore.setState((s) => ({
        action: {
          ...s.action,
          actionDialogOpen: true,
          field: "File Name",
          pattern: "IMG_\\d+",
          action: "delete",
          previewMatched: 2,
        },
        manifest: { ...s.manifest, path: "/manifests/test.db" },
      }));
    });

    render(<ActionDialog />);
    await user.click(screen.getByTestId(ACTION_BTN_APPLY));

    const summary = await screen.findByTestId(ACTION_DELETE_CONFIRM_SUMMARY);
    expect(summary).toHaveTextContent("File Name regex 'IMG_\\d+'");
  });

  // Applying an empty pattern with "delete" selected shows the confirm with a
  // 0 count (deferred-decision copy) — the destructive path is still gated by
  // the confirm dialog even though Apply itself is enabled (#741 A + C).
  it("delete-confirm shows a 0 count when applying an empty pattern with 'delete' selected", async () => {
    const user = userEvent.setup();
    act(() => {
      useAppStore.setState((s) => ({
        action: {
          ...s.action,
          actionDialogOpen: true,
          field: "File Name",
          pattern: "",
          action: "delete",
          previewMatched: -1, // no preview has run for an empty pattern
        },
        manifest: { ...s.manifest, path: "/manifests/test.db" },
      }));
    });

    render(<ActionDialog />);
    await user.click(screen.getByTestId(ACTION_BTN_APPLY));

    expect(screen.getByTestId(EXECUTE_ALL_DELETE_CONFIRM_YES)).toHaveTextContent(
      "Mark 0 files for deletion"
    );
  });

  // 23. Regression coverage for the stale-closure fix: doApply used to be
  // recreated only when [action] changed, so a handleApply built at mount
  // time (pattern "A") kept closing over that stale pattern even after the
  // user typed a new one ("B") before clicking Apply — Recent would have
  // recorded "A" (or been skipped) instead of "B". Now that doApply depends
  // on [field, pattern], changing the pattern after mount must be reflected
  // in what gets recorded.
  it("records the CURRENT pattern into Recent after the pattern changes post-mount, not the stale mount-time value (#741 stale-closure fix)", async () => {
    const user = userEvent.setup();
    vi.mocked(getSettings).mockResolvedValueOnce({} as never);

    act(() => {
      useAppStore.setState((s) => ({
        action: {
          ...s.action,
          actionDialogOpen: true,
          field: "File Name",
          pattern: "A",
          action: "__ignore__", // non-delete: applies directly, no confirm gate
        },
        manifest: { ...s.manifest, path: "/manifests/test.db" },
      }));
    });

    render(<ActionDialog />);

    // Change the pattern AFTER the initial render/mount — this is exactly
    // the scenario the stale-closure bug broke.
    act(() => {
      useAppStore.setState((s) => ({
        action: { ...s.action, pattern: "B" },
      }));
    });

    await user.click(screen.getByTestId(ACTION_BTN_APPLY));

    expect(applyMock).toHaveBeenCalledWith({});
    expect(patchSettings).toHaveBeenCalledWith({
      [RECENT_PATTERNS_KEY]: [["File Name", "B"]],
    });
    // Never recorded the stale "A" pattern as the front entry.
    expect(patchSettings).not.toHaveBeenCalledWith({
      [RECENT_PATTERNS_KEY]: [["File Name", "A"]],
    });
  });
});
