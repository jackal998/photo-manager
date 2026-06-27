// ScanDialog tests — real behaviour only, no metric-gaming padding.
//
// Covers:
//   1. Dialog renders with the correct testid when open.
//   2. Add-source button appends a source row.
//   3. Start button is disabled when fields are empty; enabled when filled.
//   4. Filling fields + clicking Start calls store.startScan with correct payload.
//   5. While scan.status === "running" the running-state UI (progress bar,
//      status text, log area, cancel button) is visible and Start button is gone.
//   6. Cancel button calls store.cancelScan.

import { render, screen, act, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { useAppStore } from "@/store/useAppStore";
import { getSettings, patchSettings } from "@/api/client";
import { ScanDialog } from "./ScanDialog";

// ScanDialog now loads persisted sources/output from GET /api/settings on open
// and persists them via PATCH on Start Scan (#678-E / s23a/s23b). Mock just
// those two client fns (spread the rest so the store's other client imports
// stay real); default to "no persisted sources" so the existing tests behave
// exactly as before (the `loaded.length > 0` guard skips any async setState).
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    getSettings: vi.fn().mockResolvedValue({}),
    patchSettings: vi.fn().mockResolvedValue({ updated: 0 }),
  };
});

const mockGetSettings = vi.mocked(getSettings);
const mockPatchSettings = vi.mocked(patchSettings);
import {
  SCAN_ADD_SOURCE,
  SCAN_AUTO_SELECT,
  SCAN_CANCEL_BUTTON,
  SCAN_DIALOG,
  SCAN_OUTPUT_PATH,
  SCAN_PROGRESS_BAR,
  SCAN_PROGRESS_LOG,
  SCAN_RESCAN_CONFIRM_CANCEL,
  SCAN_RESCAN_CONFIRM_DIALOG,
  SCAN_RESCAN_CONFIRM_DISCARD,
  SCAN_START_BUTTON,
  SCAN_STATUS_TEXT,
} from "@/testids";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderDialog(open = true) {
  const onOpenChange = vi.fn();
  const result = render(
    <ScanDialog open={open} onOpenChange={onOpenChange} />
  );
  return { ...result, onOpenChange };
}

/** Seed the Zustand store's scan slice directly. */
function seedScanState(patch: Partial<ReturnType<typeof useAppStore.getState>["scan"]>) {
  useAppStore.setState((s) => ({
    scan: { ...s.scan, ...patch },
  }));
}

/**
 * Seed the loaded manifest with rows carrying the given decisions, so the
 * rescan-confirm gate (Qt s27 / #142) has pending decisions to discard. Only
 * `user_decision` is read by the gate selector; the rest of FileRow is filled
 * with throwaway values and cast.
 */
function seedManifestDecisions(decisions: Array<"" | "delete" | "ignore">) {
  const items = decisions.map((d, i) => ({
    file_path: `C:/p/file${i}.jpg`,
    basename: `file${i}.jpg`,
    user_decision: d,
  }));
  useAppStore.setState((s) => ({
    manifest: {
      ...s.manifest,
      groups: [{ group_number: 1, member_count: items.length, items }],
    },
  }) as never);
}

/** Fill the source label/path + output so the Start button is enabled. */
async function fillStartFields(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByRole("textbox", { name: /source label/i }), "Pics");
  await user.type(screen.getByRole("textbox", { name: /source path/i }), "D:/pics");
  await user.type(screen.getByTestId(SCAN_OUTPUT_PATH), "D:/out.db");
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ScanDialog", () => {
  beforeEach(() => {
    // Default: no persisted sources (existing tests see the blank-row dialog).
    // GET always returns the allowlisted keys (null when unset); sources.* are
    // optional and omitted here so the `loaded.length > 0` guard skips override.
    mockGetSettings.mockReset().mockResolvedValue({
      "sorting.defaults": null,
      "ui.prune_singletons": null,
      "ui.scan_dialog.autotune_read_knee": null,
    });
    mockPatchSettings.mockReset().mockResolvedValue({ updated: 0 } as never);
    // Reset store to idle before each test.
    useAppStore.setState((s) => ({
      scan: {
        taskId: null,
        status: "idle",
        stageName: "",
        completed: 0,
        total: 0,
        filesPerSec: 0,
        log: [],
        error: null,
        outputPath: null,
      },
      // Clear any pending decisions seeded by the rescan-gate tests so the
      // default-path tests (no gate) start clean regardless of test order.
      manifest: { ...s.manifest, groups: [] },
      settings: s.settings,
    }));
  });

  it("renders the dialog with the SCAN_DIALOG testid when open", () => {
    renderDialog(true);
    expect(screen.getByTestId(SCAN_DIALOG)).toBeInTheDocument();
  });

  it("does not render the dialog content when closed", () => {
    renderDialog(false);
    expect(screen.queryByTestId(SCAN_DIALOG)).not.toBeInTheDocument();
  });

  it("renders one source row and an add-source button on first open", () => {
    renderDialog();
    // There should be exactly one label input and one path input by default.
    const labelInputs = screen.getAllByRole("textbox", { name: /source label/i });
    expect(labelInputs).toHaveLength(1);
    expect(screen.getByTestId(SCAN_ADD_SOURCE)).toBeInTheDocument();
  });

  it("adds a source row when Add source is clicked", async () => {
    const user = userEvent.setup();
    renderDialog();
    const addBtn = screen.getByTestId(SCAN_ADD_SOURCE);
    await user.click(addBtn);
    const labelInputs = screen.getAllByRole("textbox", { name: /source label/i });
    expect(labelInputs).toHaveLength(2);
  });

  it("Start button is disabled when source fields are empty", () => {
    renderDialog();
    expect(screen.getByTestId(SCAN_START_BUTTON)).toBeDisabled();
  });

  it("Start button is enabled when label, path, and output are filled", async () => {
    const user = userEvent.setup();
    renderDialog();
    const labelInput = screen.getByRole("textbox", { name: /source label/i });
    const pathInput = screen.getByRole("textbox", { name: /source path/i });
    const outputInput = screen.getByTestId(SCAN_OUTPUT_PATH);

    await user.type(labelInput, "Photos");
    await user.type(pathInput, "C:/photos");
    await user.type(outputInput, "C:/scan.db");

    expect(screen.getByTestId(SCAN_START_BUTTON)).not.toBeDisabled();
  });

  it("calls store.startScan with correct payload when Start is clicked", async () => {
    const user = userEvent.setup();

    // Spy on the store action — replace with a no-op so we don't hit real API.
    const startScanMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({ startScan: startScanMock } as never);

    renderDialog();
    await user.type(screen.getByRole("textbox", { name: /source label/i }), "MyPhotos");
    await user.type(screen.getByRole("textbox", { name: /source path/i }), "D:/pics");
    await user.type(screen.getByTestId(SCAN_OUTPUT_PATH), "D:/out.db");

    await user.click(screen.getByTestId(SCAN_START_BUTTON));

    expect(startScanMock).toHaveBeenCalledOnce();
    const [req] = startScanMock.mock.calls[0] as [{ sources: Record<string, string>; output_path: string; recursive_map: Record<string, boolean> }];
    expect(req.sources).toEqual({ MyPhotos: "D:/pics" });
    expect(req.output_path).toBe("D:/out.db");
    expect(req.recursive_map).toEqual({ MyPhotos: false });
  });

  it("shows running-state UI when scan.status is running", () => {
    // Seed running state before render so the component sees it immediately.
    act(() => {
      seedScanState({
        status: "running",
        stageName: "HASH",
        completed: 10,
        total: 100,
        log: ["Starting scan…", "Hashing files…"],
      });
    });

    renderDialog();

    expect(screen.getByTestId(SCAN_PROGRESS_BAR)).toBeInTheDocument();
    expect(screen.getByTestId(SCAN_STATUS_TEXT)).toHaveTextContent("HASH");
    expect(screen.getByTestId(SCAN_PROGRESS_LOG)).toBeInTheDocument();
    expect(screen.getByTestId(SCAN_CANCEL_BUTTON)).toBeInTheDocument();
    // Start button should not be present while running.
    expect(screen.queryByTestId(SCAN_START_BUTTON)).not.toBeInTheDocument();
  });

  it("log area shows all log lines while running", () => {
    act(() => {
      seedScanState({
        status: "running",
        stageName: "WALK",
        completed: 5,
        total: 0,
        log: ["Line 1", "Line 2", "Line 3"],
      });
    });

    renderDialog();
    const log = screen.getByTestId(SCAN_PROGRESS_LOG);
    expect(log).toHaveTextContent("Line 1");
    expect(log).toHaveTextContent("Line 2");
    expect(log).toHaveTextContent("Line 3");
  });

  it("indeterminate bar shows file count when total is 0", () => {
    act(() => {
      seedScanState({
        status: "running",
        stageName: "WALK",
        completed: 42,
        total: 0,
        log: [],
      });
    });

    renderDialog();
    // The "42 files found" text appears inside the progress bar container.
    expect(screen.getByTestId(SCAN_PROGRESS_BAR)).toHaveTextContent("42 files found");
  });

  it("calls store.cancelScan when Cancel is clicked", async () => {
    const user = userEvent.setup();
    const cancelMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({ cancelScan: cancelMock } as never);

    act(() => {
      seedScanState({ status: "running", stageName: "HASH", completed: 0, total: 0, log: [] });
    });

    renderDialog();
    await user.click(screen.getByTestId(SCAN_CANCEL_BUTTON));
    expect(cancelMock).toHaveBeenCalledOnce();
  });

  it("shows error message when scan status is failed", () => {
    act(() => {
      seedScanState({ status: "failed", error: "Connection refused" });
    });

    renderDialog();
    expect(screen.getByRole("alert")).toHaveTextContent("Connection refused");
  });

  it("auto-closes dialog when completed_empty arrives (outputPath stays null)", async () => {
    // Ship-blocker fix: when scan.status becomes "finished" with outputPath=null
    // (i.e. the completed_empty terminal event), the dialog must still close.
    // Previously the effect was gated on outputPath !== null, leaving the user
    // stranded with the dialog open after an empty scan.
    const { onOpenChange } = renderDialog();

    act(() => {
      // Simulate the store state left by the completed_empty SSE handler:
      // status = "finished", outputPath still null.
      seedScanState({ status: "finished", outputPath: null });
    });

    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("auto-closes dialog and loads manifest when scan finishes with an output path", () => {
    // Complementary case: finished with real output_path must also close + loadManifest.
    // Auto-select defaults OFF, so the post-scan load opts out of keeper selection.
    const loadManifestMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({ loadManifest: loadManifestMock } as never);

    const { onOpenChange } = renderDialog();

    act(() => {
      seedScanState({ status: "finished", outputPath: "C:/scan.db" });
    });

    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(loadManifestMock).toHaveBeenCalledWith("C:/scan.db", {
      selectKeepers: false,
    });
  });

  it("passes selectKeepers:true to loadManifest when auto-select is enabled (#692)", () => {
    // When the user turned on "Auto select after scan", the post-scan load must
    // select + scroll to the auto-selected keepers (Qt #239 parity). The flag is
    // read at the scan-finish call site from the (frozen) checkbox state.
    const loadManifestMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({ loadManifest: loadManifestMock } as never);

    renderDialog();

    // Turn the checkbox on (it lives in the always-mounted <details> section).
    act(() => {
      fireEvent.click(screen.getByTestId(SCAN_AUTO_SELECT));
    });

    act(() => {
      seedScanState({ status: "finished", outputPath: "C:/scan.db" });
    });

    expect(loadManifestMock).toHaveBeenCalledWith("C:/scan.db", {
      selectKeepers: true,
    });
  });

  // -------------------------------------------------------------------------
  // Source persistence (#678-E / s23a/s23b)
  // -------------------------------------------------------------------------

  it("loads persisted sources and output from settings on open", async () => {
    mockGetSettings.mockResolvedValue({
      "sources.list": [
        { path: "C:/photos/alpha", recursive: true },
        { path: "C:/photos/beta", recursive: false },
      ],
      "sources.output": "C:/out/scan.db",
    } as never);

    renderDialog();

    // Both persisted rows load in (labels derived from basename), replacing the
    // initial blank row; the output field shows the persisted path.
    expect(await screen.findByDisplayValue("C:/photos/alpha")).toBeInTheDocument();
    expect(screen.getByDisplayValue("C:/photos/beta")).toBeInTheDocument();
    expect(screen.getByDisplayValue("alpha")).toBeInTheDocument();
    expect(screen.getByDisplayValue("beta")).toBeInTheDocument();
    expect(screen.getByTestId(SCAN_OUTPUT_PATH)).toHaveValue("C:/out/scan.db");
    // The first persisted row's recursive flag round-trips (checked).
    const recursiveBoxes = screen.getAllByRole("checkbox", { name: /recursive/i });
    expect(recursiveBoxes[0]).toBeChecked();
    expect(recursiveBoxes[1]).not.toBeChecked();
  });

  it("persists sources and output via patchSettings when Start is clicked", async () => {
    const user = userEvent.setup();
    useAppStore.setState({ startScan: vi.fn().mockResolvedValue(undefined) } as never);

    renderDialog();
    await user.type(screen.getByRole("textbox", { name: /source label/i }), "Pics");
    await user.type(screen.getByRole("textbox", { name: /source path/i }), "D:/pics");
    await user.type(screen.getByTestId(SCAN_OUTPUT_PATH), "D:/out.db");

    await user.click(screen.getByTestId(SCAN_START_BUTTON));

    expect(mockPatchSettings).toHaveBeenCalledWith({
      "sources.list": [{ path: "D:/pics", recursive: false }],
      "sources.output": "D:/out.db",
    });
  });

  it("does not clobber user-typed input when a non-empty settings load resolves late", async () => {
    // Race-safety by design: if the user (or a test driver) starts editing the
    // pristine blank row before the async settings load resolves, the load must
    // NOT overwrite their input. This is the s03-reopen interleaving (a dialog
    // reopened after a Start Scan persisted a prior source list) — the load
    // fills only a still-pristine dialog.
    let resolveSettings!: (v: unknown) => void;
    mockGetSettings.mockReturnValue(
      new Promise((r) => {
        resolveSettings = r;
      }) as never
    );
    const user = userEvent.setup();
    useAppStore.setState({ startScan: vi.fn().mockResolvedValue(undefined) } as never);

    renderDialog();
    // User edits the pristine row BEFORE the settings load resolves.
    await user.type(screen.getByRole("textbox", { name: /source path/i }), "D:/mine");
    await user.type(screen.getByTestId(SCAN_OUTPUT_PATH), "D:/mine.db");

    // The (late) load resolves with DIFFERENT persisted values.
    await act(async () => {
      resolveSettings({
        "sources.list": [{ path: "C:/stale", recursive: true }],
        "sources.output": "C:/stale.db",
      });
    });

    // The user's in-progress input survives — the load filled nothing.
    expect(screen.getByRole("textbox", { name: /source path/i })).toHaveValue("D:/mine");
    expect(screen.getByTestId(SCAN_OUTPUT_PATH)).toHaveValue("D:/mine.db");
  });

  it("falls back to a single blank row when persisted sources are malformed", async () => {
    // Malformed settings.json (hand-edited / partial write) must not crash the
    // dialog or render broken rows — mirrors the Qt _load_from_settings guard.
    mockGetSettings.mockResolvedValue({
      "sources.list": [{ recursive: true }, "nope", null, { path: "" }],
    } as never);

    renderDialog();

    // No valid entries → the initial blank row remains, nothing extra rendered.
    await waitFor(() => {
      const labels = screen.getAllByRole("textbox", { name: /source label/i });
      expect(labels).toHaveLength(1);
    });
    expect(screen.getByRole("textbox", { name: /source label/i })).toHaveValue("");
  });

  // -------------------------------------------------------------------------
  // Re-scan confirmation gate (Qt s27 / #142)
  // -------------------------------------------------------------------------

  it("opens the rescan-confirm gate instead of scanning when decisions are pending", async () => {
    const user = userEvent.setup();
    const startScanMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({ startScan: startScanMock } as never);
    seedManifestDecisions(["delete"]);

    renderDialog();
    await fillStartFields(user);
    await user.click(screen.getByTestId(SCAN_START_BUTTON));

    // The confirm appears; the scan has NOT started.
    expect(screen.getByTestId(SCAN_RESCAN_CONFIRM_DIALOG)).toBeInTheDocument();
    expect(startScanMock).not.toHaveBeenCalled();
    // Body carries the pluralized count so the user (and QA) can see how many
    // decisions would be discarded.
    expect(screen.getByTestId(SCAN_RESCAN_CONFIRM_DIALOG)).toHaveTextContent(
      "1 pending decision"
    );
  });

  it("proceeds with the scan when the gate's Discard & Rescan is clicked", async () => {
    const user = userEvent.setup();
    const startScanMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({ startScan: startScanMock } as never);
    seedManifestDecisions(["delete", "ignore"]);

    renderDialog();
    await fillStartFields(user);
    await user.click(screen.getByTestId(SCAN_START_BUTTON));
    // Plural body for two pending decisions.
    expect(screen.getByTestId(SCAN_RESCAN_CONFIRM_DIALOG)).toHaveTextContent(
      "2 pending decisions"
    );

    await user.click(screen.getByTestId(SCAN_RESCAN_CONFIRM_DISCARD));

    expect(startScanMock).toHaveBeenCalledOnce();
  });

  it("does not scan and keeps the dialog open when the gate is cancelled", async () => {
    const user = userEvent.setup();
    const startScanMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({ startScan: startScanMock } as never);
    seedManifestDecisions(["delete"]);

    renderDialog();
    await fillStartFields(user);
    await user.click(screen.getByTestId(SCAN_START_BUTTON));
    await user.click(screen.getByTestId(SCAN_RESCAN_CONFIRM_CANCEL));

    expect(startScanMock).not.toHaveBeenCalled();
    // The scan dialog stays open (true Qt parity — the nested-modal guard keeps
    // it from closing on the confirm's dismiss).
    expect(screen.getByTestId(SCAN_DIALOG)).toBeInTheDocument();
    expect(screen.queryByTestId(SCAN_RESCAN_CONFIRM_DIALOG)).not.toBeInTheDocument();
  });
});
