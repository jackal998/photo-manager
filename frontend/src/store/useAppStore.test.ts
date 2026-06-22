// Tests for useAppStore — real behaviour, no branch-coverage padding.
//
// Each test exercises a state transition that a real user can trigger:
//  - ingestScanEvent: stage / finished / failed-error / failed-clean-cancel /
//    completed_empty
//  - setDecision: optimistic apply + revert on PATCH failure
//  - loadManifest: happy path
//  - executeDecisions: success / 409 locked_paths / 409 already_running / prunePrompt
//  - removeFromList: success / 409 locked_paths
//  - pruneSingletons: success
//  - saveManifest: success
//  - revealInExplorer: 403/501 non-fatal surfaced as executeError

import { beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Mock api/client so no real network calls are made.
//
// The pre-existing stubs (startScan, getManifest, patchDecisions, …) keep their
// vi.fn() forms so existing tests stay untouched.
//
// The Phase-2C2 functions (postExecute, postRemove, postPrune, postSave,
// postReveal) and ApiConflictError are imported from the real module so the
// real 409-parsing logic in postJsonOrConflict runs.  Tests for those actions
// control behaviour by mocking global.fetch with crafted Response objects.
// ---------------------------------------------------------------------------

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  return {
    // Keep stubs for the actions already tested at the vi.fn() boundary.
    startScan: vi.fn(),
    cancelScan: vi.fn(),
    getManifest: vi.fn(),
    patchDecisions: vi.fn(),
    patchLocks: vi.fn(),
    getSettings: vi.fn(),
    patchSettings: vi.fn(),
    browseFs: vi.fn(),
    bulkDecide: vi.fn(),
    scanEventsUrl: vi.fn((id: string) => `/api/scan/${id}/events`),
    // Pass through real implementations so 409 parsing is exercised.
    postExecute: real.postExecute,
    postRemove: real.postRemove,
    postPrune: real.postPrune,
    postSave: real.postSave,
    postReveal: real.postReveal,
    ApiConflictError: real.ApiConflictError,
  };
});

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
    preview: {
      selectedFilePath: null,
      fullResPath: null,
    },
    execute: {
      executeOpen: false,
      executeRunning: false,
      executeResult: null,
      executeError: null,
      lockConflict: null,
      prunePrompt: null,
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

// ---------------------------------------------------------------------------
// Phase 2C2 destructive store actions
//
// These tests mock global.fetch and let the REAL client (postExecute,
// postRemove, etc.) parse real Response objects, so that 409-parsing logic in
// postJsonOrConflict is exercised rather than bypassed.
// ---------------------------------------------------------------------------

// ---- shared fetch-mock helpers --------------------------------------------

/** Craft a 200 Response with JSON body. */
function ok200(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/** Craft a 409 Response with a discriminated detail body. */
function conflict409(detail: unknown): Response {
  return new Response(JSON.stringify({ detail }), {
    status: 409,
    headers: { "Content-Type": "application/json" },
  });
}

/** Craft a non-2xx Response. */
function errorResponse(status: number, statusText = "Error"): Response {
  return new Response(JSON.stringify({ detail: statusText }), {
    status,
    statusText,
    headers: { "Content-Type": "application/json" },
  });
}

/** Seed the store with a manifest so actions that check manifest.path proceed. */
function seedManifest(groups: Group[] = []): void {
  useAppStore.setState((s) => ({
    ...s,
    manifest: {
      path: "/data/scan.db",
      groups,
      totalGroups: groups.length,
      totalFiles: groups.reduce((n, g) => n + g.items.length, 0),
      loading: false,
      error: null,
    },
  }));
}

// ---------------------------------------------------------------------------
// executeDecisions
// ---------------------------------------------------------------------------

describe("executeDecisions – success replaces manifest groups and clears running", () => {
  it("replaces manifest.groups with result.groups and sets executeResult", async () => {
    // Seed manifest with two decided files in one group.
    const initialGroup = makeGroup(1, ["/photos/a.jpg", "/photos/b.jpg"]);
    initialGroup.items[0].user_decision = "delete";
    initialGroup.items[1].user_decision = "ignore";
    seedManifest([initialGroup]);

    // Server returns a reduced groups array (one file was deleted).
    const afterGroup = makeGroup(1, ["/photos/b.jpg"]);
    const executeResult = {
      success_paths: ["/photos/a.jpg"],
      failed: [],
      ignored: ["/photos/b.jpg"],
      missing: [],
      db_write_failed: [],
      log_path: null,
      groups: [afterGroup],
    };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(ok200(executeResult));

    await useAppStore.getState().executeDecisions();

    const { manifest, execute } = useAppStore.getState();
    // Groups must be the server's authoritative list, not the original.
    expect(manifest.groups).toHaveLength(1);
    expect(manifest.groups[0].items).toHaveLength(1);
    expect(manifest.groups[0].items[0].file_path).toBe("/photos/b.jpg");
    expect(execute.executeResult).toEqual(executeResult);
    expect(execute.executeRunning).toBe(false);
    // No conflict surfaced.
    expect(execute.lockConflict).toBeNull();
  });
});

describe("executeDecisions – 409 locked_paths captures originalPaths and op=execute", () => {
  it("sets lockConflict with op=execute and originalPaths = all decided paths", async () => {
    // Seed two decided files.
    const group = makeGroup(1, ["/photos/c.jpg", "/photos/d.jpg"]);
    group.items[0].user_decision = "delete";
    group.items[1].user_decision = "delete";
    seedManifest([group]);

    // Backend reports one of the paths is locked.
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      conflict409({ code: "locked_paths", locked_paths: ["/photos/c.jpg"] })
    );

    await useAppStore.getState().executeDecisions();

    const { execute } = useAppStore.getState();
    expect(execute.lockConflict).not.toBeNull();
    expect(execute.lockConflict!.op).toBe("execute");
    expect(execute.lockConflict!.paths).toEqual(["/photos/c.jpg"]);
    // originalPaths must include ALL decided files so "Unlock Only" can filter.
    expect(execute.lockConflict!.originalPaths).toContain("/photos/c.jpg");
    expect(execute.lockConflict!.originalPaths).toContain("/photos/d.jpg");
    expect(execute.executeRunning).toBe(false);
  });
});

describe("executeDecisions – 409 execute_already_running sets executeError, not lockConflict", () => {
  it("surfaces executeError and leaves lockConflict null", async () => {
    const group = makeGroup(1, ["/photos/e.jpg"]);
    group.items[0].user_decision = "delete";
    seedManifest([group]);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      conflict409({ code: "execute_already_running" })
    );

    await useAppStore.getState().executeDecisions();

    const { execute } = useAppStore.getState();
    expect(execute.lockConflict).toBeNull();
    expect(execute.executeError).toBeTruthy();
    expect(execute.executeRunning).toBe(false);
  });
});

describe("executeDecisions – prunePrompt set when result contains singleton groups", () => {
  it("populates prunePrompt.candidates with paths from member_count===1 groups", async () => {
    const group = makeGroup(1, ["/photos/f.jpg", "/photos/g.jpg"]);
    group.items[0].user_decision = "delete";
    group.items[1].user_decision = "ignore";
    seedManifest([group]);

    // After execute, group has only one file left (singleton).
    const singletonGroup: Group = {
      group_number: 1,
      member_count: 1,
      items: [
        {
          file_path: "/photos/g.jpg",
          basename: "g.jpg",
          folder: "/photos",
          action: "",
          user_decision: "ignore",
          is_locked: false,
          is_ref_winner: true,
          similarity: { kind: "ref", percent: null },
          score: null,
          file_size_bytes: 1024,
          pixel_width: 800,
          pixel_height: 600,
          shot_date: null,
          creation_date: null,
          phash: null,
          hamming_distance: null,
          thumbnail_url: "/api/image?path=%2Fphotos%2Fg.jpg&size=512",
        },
      ],
    };

    const executeResult = {
      success_paths: ["/photos/f.jpg"],
      failed: [],
      ignored: [],
      missing: [],
      db_write_failed: [],
      log_path: null,
      groups: [singletonGroup],
    };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(ok200(executeResult));

    await useAppStore.getState().executeDecisions();

    const { execute } = useAppStore.getState();
    expect(execute.prunePrompt).not.toBeNull();
    expect(execute.prunePrompt!.candidates).toContain("/photos/g.jpg");
  });
});

// ---------------------------------------------------------------------------
// removeFromList
// ---------------------------------------------------------------------------

describe("removeFromList – success replaces manifest groups", () => {
  it("updates manifest.groups from the server response", async () => {
    const initialGroup = makeGroup(1, ["/photos/h.jpg", "/photos/i.jpg"]);
    seedManifest([initialGroup]);

    const afterGroup = makeGroup(1, ["/photos/i.jpg"]);
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      ok200({ removed: 1, groups: [afterGroup] })
    );

    await useAppStore.getState().removeFromList(["/photos/h.jpg"]);

    const { manifest } = useAppStore.getState();
    expect(manifest.groups).toHaveLength(1);
    expect(manifest.groups[0].items).toHaveLength(1);
    expect(manifest.groups[0].items[0].file_path).toBe("/photos/i.jpg");
  });
});

describe("removeFromList – 409 locked_paths sets lockConflict op=remove with originalPaths", () => {
  it("preserves the caller's path list in lockConflict.originalPaths", async () => {
    const group = makeGroup(1, ["/photos/j.jpg", "/photos/k.jpg"]);
    group.items[0].is_locked = true;
    seedManifest([group]);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      conflict409({ code: "locked_paths", locked_paths: ["/photos/j.jpg"] })
    );

    const paths = ["/photos/j.jpg", "/photos/k.jpg"];
    await useAppStore.getState().removeFromList(paths);

    const { execute } = useAppStore.getState();
    expect(execute.lockConflict).not.toBeNull();
    expect(execute.lockConflict!.op).toBe("remove");
    expect(execute.lockConflict!.originalPaths).toEqual(paths);
    expect(execute.lockConflict!.paths).toEqual(["/photos/j.jpg"]);
  });
});

// ---------------------------------------------------------------------------
// pruneSingletons
// ---------------------------------------------------------------------------

describe("pruneSingletons – success replaces groups and clears prunePrompt", () => {
  it("updates manifest.groups and nullifies execute.prunePrompt", async () => {
    const group = makeGroup(1, ["/photos/l.jpg"]);
    seedManifest([group]);

    // Pre-set a prunePrompt to confirm it gets cleared.
    useAppStore.setState((s) => ({
      ...s,
      execute: {
        ...s.execute,
        prunePrompt: { candidates: ["/photos/l.jpg"] },
      },
    }));

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      ok200({ pruned: ["/photos/l.jpg"], locked_skipped: [], groups: [] })
    );

    await useAppStore.getState().pruneSingletons();

    const { manifest, execute } = useAppStore.getState();
    expect(manifest.groups).toHaveLength(0);
    expect(execute.prunePrompt).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// saveManifest
// ---------------------------------------------------------------------------

describe("saveManifest – success resolves without setting error", () => {
  it("completes without throwing or marking manifest.error", async () => {
    seedManifest([]);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      ok200({ saved_to: "/data/scan.db", updated: 0 })
    );

    await useAppStore.getState().saveManifest();

    expect(useAppStore.getState().manifest.error).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// revealInExplorer
// ---------------------------------------------------------------------------

describe("revealInExplorer – 403/501 is non-fatal: surfaces executeError, does not throw", () => {
  it("sets executeError and resolves (does not reject) on a 403 response", async () => {
    seedManifest([]);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(errorResponse(403, "Forbidden"));

    // The action must not throw — a crash here would bubble up to the caller.
    await expect(
      useAppStore.getState().revealInExplorer("/photos/m.jpg")
    ).resolves.toBeUndefined();

    const { execute } = useAppStore.getState();
    expect(execute.executeError).toBeTruthy();
  });

  it("sets executeError and resolves (does not reject) on a 501 response", async () => {
    seedManifest([]);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(errorResponse(501, "Not Implemented"));

    await expect(
      useAppStore.getState().revealInExplorer("/photos/n.jpg")
    ).resolves.toBeUndefined();

    const { execute } = useAppStore.getState();
    expect(execute.executeError).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// action slice — bulk-decide (the adversarial-review ship-blockers)
// ---------------------------------------------------------------------------

describe("useAppStore action slice (bulk-decide)", () => {
  it("applyBulkDecide re-throws a 409 locked_paths conflict (does not swallow it)", async () => {
    // The component relies on this throw to open the lock-confirm dialog +
    // retry with force_locked. A swallowed error made that flow dead.
    useAppStore.setState((s) => ({
      manifest: { ...s.manifest, path: "/m/test.db" },
      action: { ...s.action, field: "File Name", pattern: "IMG", action: "delete" },
    }));
    const conflict = new client.ApiConflictError("locked_paths", ["/p/a.jpg"]);
    vi.mocked(client.bulkDecide).mockReset().mockRejectedValueOnce(conflict);

    await expect(
      useAppStore.getState().applyBulkDecide(false)
    ).rejects.toBe(conflict);
  });

  it("previewBulkDecide drops a stale out-of-order response (latest wins)", async () => {
    // Preview A is issued first but resolves LAST; preview B is issued second
    // and resolves first. Only B (the latest request) may commit — A's late
    // arrival must be dropped, or the counter mis-states what Apply touches.
    useAppStore.setState((s) => ({
      manifest: { ...s.manifest, path: "/m/test.db" },
      action: { ...s.action, field: "File Name", pattern: "IMG", action: "delete" },
    }));

    let resolveA!: (v: unknown) => void;
    let resolveB!: (v: unknown) => void;
    const pA = new Promise((r) => { resolveA = r; });
    const pB = new Promise((r) => { resolveB = r; });
    vi.mocked(client.bulkDecide)
      .mockReset()
      .mockReturnValueOnce(pA as never)
      .mockReturnValueOnce(pB as never);

    const callA = useAppStore.getState().previewBulkDecide(); // seq N
    const callB = useAppStore.getState().previewBulkDecide(); // seq N+1 (latest)

    resolveB({ matched: 2, affected_paths: ["b"], action_applied: "decision", groups: [] });
    await callB;
    resolveA({ matched: 1, affected_paths: ["a"], action_applied: "decision", groups: [] });
    await callA;

    // B (the latest issued request) wins; A's stale late arrival is ignored.
    expect(useAppStore.getState().action.previewMatched).toBe(2);
  });
});
