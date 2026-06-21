// Tests for useAppStore — real behaviour, no branch-coverage padding.
//
// Each test exercises a state transition that a real user can trigger:
//  - ingestScanEvent: stage / finished / failed-error / failed-clean-cancel /
//    completed_empty
//  - setDecision: optimistic apply + revert on PATCH failure
//  - loadManifest: happy path

import { beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Mock api/client so no real network calls are made.
// ---------------------------------------------------------------------------

vi.mock("../api/client", () => ({
  startScan: vi.fn(),
  cancelScan: vi.fn(),
  getManifest: vi.fn(),
  patchDecisions: vi.fn(),
  patchLocks: vi.fn(),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
  browseFs: vi.fn(),
  scanEventsUrl: vi.fn((id: string) => `/api/scan/${id}/events`),
}));

import * as client from "../api/client";
import { useAppStore } from "./useAppStore";
import type { Group } from "../api/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeGroup(groupNumber: number, filePaths: string[]): Group {
  return {
    group_number: groupNumber,
    member_count: filePaths.length,
    items: filePaths.map((fp, i) => ({
      file_path: fp,
      basename: fp.split("/").at(-1) ?? fp,
      folder: fp.split("/").slice(0, -1).join("/"),
      action: "",
      user_decision: "" as const,
      is_locked: false,
      is_ref_winner: i === 0,
      similarity: { kind: "ref" as const, percent: null },
      score: null,
      file_size_bytes: 1024,
      pixel_width: 800,
      pixel_height: 600,
      shot_date: null,
      creation_date: null,
      phash: null,
      hamming_distance: null,
      thumbnail_url: `/api/image?path=${encodeURIComponent(fp)}&size=512`,
    })),
  };
}

// ---------------------------------------------------------------------------
// Reset store before each test so state doesn't bleed.
// ---------------------------------------------------------------------------

beforeEach(() => {
  useAppStore.setState({
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
    manifest: {
      path: null,
      groups: [],
      totalGroups: 0,
      totalFiles: 0,
      loading: false,
      error: null,
    },
    settings: {
      values: {
        "sorting.defaults": null,
        "ui.prune_singletons": null,
        "ui.scan_dialog.autotune_read_knee": null,
      },
      loading: false,
    },
  });
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// ingestScanEvent
// ---------------------------------------------------------------------------

describe("ingestScanEvent – stage", () => {
  it("updates stageName, completed, total, filesPerSec", () => {
    useAppStore.getState().ingestScanEvent("stage", {
      event: "stage",
      stage_name: "HASH",
      completed: 42,
      total: 100,
      files_per_sec: 7.5,
    });
    const { scan } = useAppStore.getState();
    expect(scan.stageName).toBe("HASH");
    expect(scan.completed).toBe(42);
    expect(scan.total).toBe(100);
    expect(scan.filesPerSec).toBe(7.5);
    // Status stays running (we didn't start a scan, but stage is not terminal)
    expect(scan.status).toBe("idle");
  });
});

describe("ingestScanEvent – finished", () => {
  it("sets status=finished and appends output_path to log", () => {
    useAppStore.getState().ingestScanEvent("finished", {
      event: "finished",
      output_path: "/tmp/scan.db",
    });
    const { scan } = useAppStore.getState();
    expect(scan.status).toBe("finished");
    expect(scan.log.some((l) => l.includes("/tmp/scan.db"))).toBe(true);
  });
});

describe("ingestScanEvent – failed (real error)", () => {
  it("sets status=failed, records error, appends to log", () => {
    useAppStore.getState().ingestScanEvent("failed", {
      event: "failed",
      msg: "disk full",
    });
    const { scan } = useAppStore.getState();
    expect(scan.status).toBe("failed");
    expect(scan.error).toBe("disk full");
    expect(scan.log.some((l) => l.includes("disk full"))).toBe(true);
  });
});

describe("ingestScanEvent – failed (clean cancel)", () => {
  it("sets status=cancelled, error stays null", () => {
    useAppStore.getState().ingestScanEvent("failed", {
      event: "failed",
      msg: "Scan cancelled.",
    });
    const { scan } = useAppStore.getState();
    expect(scan.status).toBe("cancelled");
    expect(scan.error).toBeNull();
    expect(scan.log.some((l) => l.includes("cancelled"))).toBe(true);
  });
});

describe("ingestScanEvent – completed_empty", () => {
  it("sets status=finished and notes nothing was found", () => {
    useAppStore.getState().ingestScanEvent("completed_empty", {
      event: "completed_empty",
    });
    const { scan } = useAppStore.getState();
    expect(scan.status).toBe("finished");
    expect(scan.log.some((l) => l.toLowerCase().includes("no files"))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// setDecision – optimistic apply
// ---------------------------------------------------------------------------

describe("setDecision – optimistic apply", () => {
  it("updates user_decision before the PATCH resolves", async () => {
    // Arrange: seed manifest with a group.
    const group = makeGroup(1, ["/photos/a.jpg"]);
    useAppStore.setState((s) => ({
      ...s,
      manifest: {
        path: "/data/scan.db",
        groups: [group],
        totalGroups: 1,
        totalFiles: 1,
        loading: false,
        error: null,
      },
    }));

    let resolvePatched!: () => void;
    const patchedPromise = new Promise<void>((res) => {
      resolvePatched = res;
    });
    vi.mocked(client.patchDecisions).mockImplementation(async () => {
      await patchedPromise;
      return { updated: 1 };
    });

    // Act: call setDecision but don't await yet.
    const pendingSet = useAppStore.getState().setDecision("/photos/a.jpg", "delete");

    // Assert: optimistic update is visible before PATCH resolves.
    const row = useAppStore.getState().manifest.groups[0].items[0];
    expect(row.user_decision).toBe("delete");

    // Cleanup: let PATCH resolve.
    resolvePatched();
    await pendingSet;
  });
});

describe("setDecision – reverts on PATCH failure", () => {
  it("restores original user_decision and sets manifest.error on PATCH failure", async () => {
    const group = makeGroup(1, ["/photos/b.jpg"]);
    // original decision is ""
    useAppStore.setState((s) => ({
      ...s,
      manifest: {
        path: "/data/scan.db",
        groups: [group],
        totalGroups: 1,
        totalFiles: 1,
        loading: false,
        error: null,
      },
    }));

    vi.mocked(client.patchDecisions).mockRejectedValue(
      new Error("server error")
    );

    await useAppStore.getState().setDecision("/photos/b.jpg", "ignore");

    const row = useAppStore.getState().manifest.groups[0].items[0];
    // Should have reverted to ""
    expect(row.user_decision).toBe("");
    expect(useAppStore.getState().manifest.error).toBe("server error");
  });
});

// ---------------------------------------------------------------------------
// loadManifest – happy path
// ---------------------------------------------------------------------------

describe("loadManifest – happy path", () => {
  it("sets manifest state from server response", async () => {
    const group = makeGroup(3, ["/p/img1.jpg", "/p/img2.jpg"]);
    vi.mocked(client.getManifest).mockResolvedValue({
      manifest_path: "/data/out.db",
      groups: [group],
      total_groups: 1,
      total_files: 2,
    });

    await useAppStore.getState().loadManifest("/data/out.db");

    const { manifest } = useAppStore.getState();
    expect(manifest.path).toBe("/data/out.db");
    expect(manifest.groups).toHaveLength(1);
    expect(manifest.totalGroups).toBe(1);
    expect(manifest.totalFiles).toBe(2);
    expect(manifest.loading).toBe(false);
    expect(manifest.error).toBeNull();
  });
});
