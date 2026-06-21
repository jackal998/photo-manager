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

import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { useAppStore } from "@/store/useAppStore";
import { ScanDialog } from "./ScanDialog";
import {
  SCAN_ADD_SOURCE,
  SCAN_CANCEL_BUTTON,
  SCAN_DIALOG,
  SCAN_OUTPUT_PATH,
  SCAN_PROGRESS_BAR,
  SCAN_PROGRESS_LOG,
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

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ScanDialog", () => {
  beforeEach(() => {
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
      manifest: s.manifest,
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
    const loadManifestMock = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({ loadManifest: loadManifestMock } as never);

    const { onOpenChange } = renderDialog();

    act(() => {
      seedScanState({ status: "finished", outputPath: "C:/scan.db" });
    });

    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(loadManifestMock).toHaveBeenCalledWith("C:/scan.db");
  });
});
