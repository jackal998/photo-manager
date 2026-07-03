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
    // postPruneCandidates is the backend singleton classifier maybeOfferPrune
    // calls — stubbed so tests control which buckets it returns.
    postPruneCandidates: vi.fn(),
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
      stageStartedAt: null,
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
      selectedGroupId: null,
    },
    selection: {
      selectedPaths: [],
      anchorPath: null,
      scrollToPath: null,
    },
    execute: {
      executeOpen: false,
      executeRunning: false,
      executeResult: null,
      executeError: null,
      lockConflict: null,
      prunePrompt: null,
      prunePending: null,
      scopeGroupNumbers: null,
    },
  });
  vi.clearAllMocks();
  // Default the authoritative prune pref to "never" so the maybeOfferPrune tail
  // (now run by executeDecisions / removeFromList) is a benign no-op in tests
  // that don't opt into the prune flow. clearAllMocks keeps implementations, so
  // re-establish this each test to neutralise any per-test override that bled.
  vi.mocked(client.getSettings).mockResolvedValue({
    "sorting.defaults": null,
    "ui.prune_singletons": "never",
    "ui.scan_dialog.autotune_read_knee": null,
  });
  // Default: no singletons, so maybeOfferPrune is a clean no-op in tests that
  // don't opt into the prune flow.
  vi.mocked(client.postPruneCandidates).mockResolvedValue({
    plain: [],
    actioned: [],
    locked: [],
  });
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

  // #740 — stageStartedAt feeds the ETA min-samples gate (ScanDialog.tsx).
  it("stamps stageStartedAt on a stage TRANSITION", () => {
    expect(useAppStore.getState().scan.stageStartedAt).toBeNull();
    useAppStore.getState().ingestScanEvent("stage", {
      event: "stage",
      stage_name: "WALK",
      completed: 1,
      total: 0,
      files_per_sec: 0,
    });
    expect(useAppStore.getState().scan.stageStartedAt).not.toBeNull();
  });

  it("does NOT re-stamp stageStartedAt for repeated emits of the SAME stage", () => {
    useAppStore.getState().ingestScanEvent("stage", {
      event: "stage",
      stage_name: "HASH",
      completed: 1,
      total: 100,
      files_per_sec: 1,
    });
    const first = useAppStore.getState().scan.stageStartedAt;
    useAppStore.getState().ingestScanEvent("stage", {
      event: "stage",
      stage_name: "HASH",
      completed: 2,
      total: 100,
      files_per_sec: 2,
    });
    expect(useAppStore.getState().scan.stageStartedAt).toBe(first);
  });

  it("re-stamps stageStartedAt when the stage changes", () => {
    useAppStore.getState().ingestScanEvent("stage", {
      event: "stage",
      stage_name: "WALK",
      completed: 1,
      total: 0,
      files_per_sec: 0,
    });
    const walkStartedAt = useAppStore.getState().scan.stageStartedAt;
    useAppStore.getState().ingestScanEvent("stage", {
      event: "stage",
      stage_name: "HASH",
      completed: 1,
      total: 100,
      files_per_sec: 1,
    });
    // Different stage → a fresh (non-null) timestamp. We can't assert a
    // strictly later Date.now() without flaking on a fast CI clock, but the
    // transition must produce SOME stamp — null would mean the reset logic
    // regressed to "never stamps after the first stage".
    expect(useAppStore.getState().scan.stageStartedAt).not.toBeNull();
    expect(walkStartedAt).not.toBeNull();
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
// loadManifest – post-scan keeper selection (#692, Qt #239 parity)
//
// Only the post-scan auto-load passes { selectKeepers: true }; the
// manifest-OPEN call sites pass nothing. So opening a manifest must keep the
// old "clear the selection" behaviour even when KEEP rows are present, while
// the post-scan path selects the action==="KEEP" rows across all groups and
// arms the one-shot scrollToPath at the first keeper.
// ---------------------------------------------------------------------------

describe("loadManifest – selectKeepers (#692)", () => {
  it("selects every KEEP row across groups and scrolls to the first", async () => {
    const g1 = makeGroup(1, ["/p/k1.jpg", "/p/d1.jpg"]);
    g1.items[0].action = "KEEP";
    const g2 = makeGroup(2, ["/p/k2.jpg", "/p/d2.jpg"]);
    g2.items[0].action = "KEEP";
    vi.mocked(client.getManifest).mockResolvedValue({
      manifest_path: "/data/out.db",
      groups: [g1, g2],
      total_groups: 2,
      total_files: 4,
    });

    await useAppStore
      .getState()
      .loadManifest("/data/out.db", { selectKeepers: true });

    const { selection } = useAppStore.getState();
    expect(selection.selectedPaths).toEqual(["/p/k1.jpg", "/p/k2.jpg"]);
    expect(selection.scrollToPath).toBe("/p/k1.jpg"); // first keeper into view
    expect(selection.anchorPath).toBe("/p/k2.jpg"); // anchor = last, like setSelection
  });

  it("the manifest-OPEN path (no opts) clears the selection even with KEEP rows", async () => {
    const g = makeGroup(1, ["/p/k.jpg", "/p/d.jpg"]);
    g.items[0].action = "KEEP";
    // A stale selection that opening a manifest must wipe.
    useAppStore.setState({
      selection: {
        selectedPaths: ["/p/old.jpg"],
        anchorPath: "/p/old.jpg",
        scrollToPath: null,
      },
    });
    vi.mocked(client.getManifest).mockResolvedValue({
      manifest_path: "/data/out.db",
      groups: [g],
      total_groups: 1,
      total_files: 2,
    });

    await useAppStore.getState().loadManifest("/data/out.db");

    const { selection } = useAppStore.getState();
    expect(selection.selectedPaths).toEqual([]); // NOT auto-selected on open
    expect(selection.anchorPath).toBeNull();
    expect(selection.scrollToPath).toBeNull();
  });

  it("selectKeepers with no KEEP rows is a no-op (auto-select was off)", async () => {
    const g = makeGroup(1, ["/p/a.jpg", "/p/b.jpg"]); // all action=""
    vi.mocked(client.getManifest).mockResolvedValue({
      manifest_path: "/data/out.db",
      groups: [g],
      total_groups: 1,
      total_files: 2,
    });

    await useAppStore
      .getState()
      .loadManifest("/data/out.db", { selectKeepers: true });

    const { selection } = useAppStore.getState();
    expect(selection.selectedPaths).toEqual([]);
    expect(selection.scrollToPath).toBeNull();
    expect(selection.anchorPath).toBeNull();
  });

  it("clearScrollTarget consumes the one-shot without touching the selection", () => {
    useAppStore.setState({
      selection: {
        selectedPaths: ["/p/k.jpg"],
        anchorPath: "/p/k.jpg",
        scrollToPath: "/p/k.jpg",
      },
    });

    useAppStore.getState().clearScrollTarget();

    const { selection } = useAppStore.getState();
    expect(selection.scrollToPath).toBeNull();
    expect(selection.selectedPaths).toEqual(["/p/k.jpg"]); // selection untouched
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

describe("executeDecisions – offers a prune when the backend reports singletons", () => {
  it("opens the prune dialog with the backend's buckets (pref=ask)", async () => {
    seedManifest([makeGroup(1, ["/photos/f.jpg", "/photos/g.jpg"])]);

    const executeResult = {
      success_paths: ["/photos/f.jpg"],
      failed: [],
      ignored: [],
      missing: [],
      db_write_failed: [],
      log_path: null,
      groups: [], // web review view drops singletons; the FE never sees them
    };

    // maybeOfferPrune asks the backend for candidates, then reads the pref.
    vi.mocked(client.postPruneCandidates).mockResolvedValue({
      plain: [],
      actioned: ["/photos/g.jpg"],
      locked: [],
    });
    vi.mocked(client.getSettings).mockResolvedValue({
      "sorting.defaults": null,
      "ui.prune_singletons": "ask",
      "ui.scan_dialog.autotune_read_knee": null,
    });
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(ok200(executeResult));

    await useAppStore.getState().executeDecisions();

    const { execute } = useAppStore.getState();
    expect(execute.prunePrompt).not.toBeNull();
    expect(execute.prunePrompt!.actioned).toEqual(["/photos/g.jpg"]);
    expect(execute.prunePrompt!.lockedToPrune).toEqual([]);
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
// applyPrune
// ---------------------------------------------------------------------------

describe("applyPrune – success replaces groups and clears prune state", () => {
  it("updates manifest.groups and nullifies prunePrompt + prunePending", async () => {
    const group = makeGroup(1, ["/photos/l.jpg"]);
    seedManifest([group]);

    // Pre-set prune state to confirm both get cleared.
    useAppStore.setState((s) => ({
      ...s,
      execute: {
        ...s.execute,
        prunePrompt: { plain: ["/photos/l.jpg"], actioned: [], lockedToPrune: [] },
      },
    }));

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      ok200({ pruned: ["/photos/l.jpg"], locked_skipped: [], groups: [] })
    );

    await useAppStore.getState().applyPrune(["/photos/l.jpg"]);

    const { manifest, execute } = useAppStore.getState();
    expect(manifest.groups).toHaveLength(0);
    expect(execute.prunePrompt).toBeNull();
    expect(execute.prunePending).toBeNull();
    // The explicit-paths body must carry `paths`, not include_actioned.
    const body = JSON.parse((fetchSpy.mock.calls[0][1] as { body: string }).body);
    expect(body.paths).toEqual(["/photos/l.jpg"]);
  });

  it("an empty set is a no-op that still clears the prune state (no fetch)", async () => {
    seedManifest([makeGroup(1, ["/photos/m.jpg"])]);
    useAppStore.setState((s) => ({
      ...s,
      execute: {
        ...s.execute,
        prunePrompt: { plain: [], actioned: [], lockedToPrune: [] },
      },
    }));
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    await useAppStore.getState().applyPrune([]);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(useAppStore.getState().execute.prunePrompt).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// maybeOfferPrune — the post-destructive-op orchestrator (the Qt decision tree)
// ---------------------------------------------------------------------------

function mockPref(pref: string) {
  vi.mocked(client.getSettings).mockResolvedValue({
    "sorting.defaults": null,
    "ui.prune_singletons": pref,
    "ui.scan_dialog.autotune_read_knee": null,
  });
}

describe("maybeOfferPrune", () => {
  function mockCandidates(c: {
    plain?: string[];
    actioned?: string[];
    locked?: string[];
  }) {
    vi.mocked(client.postPruneCandidates).mockResolvedValue({
      plain: c.plain ?? [],
      actioned: c.actioned ?? [],
      locked: c.locked ?? [],
    });
  }

  it("no singletons → never even fetches the pref", async () => {
    seedManifest([]);
    mockCandidates({}); // backend reports no singletons
    await useAppStore.getState().maybeOfferPrune();
    expect(client.getSettings).not.toHaveBeenCalled();
    expect(useAppStore.getState().execute.prunePrompt).toBeNull();
  });

  it("pref=never → reads candidates+pref, then no offer / no prune call", async () => {
    seedManifest([]);
    mockCandidates({ plain: ["/p/plain.jpg"] });
    mockPref("never");
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await useAppStore.getState().maybeOfferPrune();
    // The full path ran (candidates fetched, pref read) — and correctly resolved
    // to no action. Asserting the client mocks fired makes this non-hollow: a
    // dead maybeOfferPrune that never reached the pref read would fail here.
    expect(client.postPruneCandidates).toHaveBeenCalled();
    expect(client.getSettings).toHaveBeenCalled();
    expect(useAppStore.getState().execute.prunePrompt).toBeNull();
    expect(useAppStore.getState().execute.lockConflict).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled(); // no /api/prune
  });

  it("pref=ask, no locked → opens the dialog with the backend buckets", async () => {
    seedManifest([]);
    mockCandidates({ plain: ["/p/plain.jpg"], actioned: ["/p/act.jpg"] });
    mockPref("ask");
    await useAppStore.getState().maybeOfferPrune();
    const p = useAppStore.getState().execute.prunePrompt;
    expect(p).not.toBeNull();
    expect(p!.plain).toEqual(["/p/plain.jpg"]);
    expect(p!.actioned).toEqual(["/p/act.jpg"]);
    expect(p!.lockedToPrune).toEqual([]);
    expect(useAppStore.getState().execute.lockConflict).toBeNull();
  });

  it("pref=always, no locked → auto-prunes both buckets via explicit paths", async () => {
    seedManifest([]);
    mockCandidates({ plain: ["/p/plain.jpg"], actioned: ["/p/act.jpg"] });
    mockPref("always");
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(ok200({ pruned: [], locked_skipped: [], groups: [] }));
    await useAppStore.getState().maybeOfferPrune();
    expect(useAppStore.getState().execute.prunePrompt).toBeNull();
    const body = JSON.parse(
      (fetchSpy.mock.calls[0][1] as { body: string }).body
    );
    expect([...body.paths].sort()).toEqual(["/p/act.jpg", "/p/plain.jpg"]);
  });

  it("locked present → opens the prune LOCK gate first (no prune yet), on BOTH paths", async () => {
    seedManifest([]);
    mockCandidates({
      plain: ["/p/plain.jpg"],
      actioned: ["/p/act.jpg"],
      locked: ["/p/lock.jpg"],
    });
    mockPref("always"); // even "always" gates locked singletons (D6)
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    await useAppStore.getState().maybeOfferPrune();
    const { lockConflict, prunePending, prunePrompt } =
      useAppStore.getState().execute;
    expect(lockConflict).not.toBeNull();
    expect(lockConflict!.op).toBe("prune");
    expect(lockConflict!.paths).toEqual(["/p/lock.jpg"]);
    expect(prunePending).not.toBeNull();
    expect(prunePending!.pref).toBe("always");
    expect(prunePrompt).toBeNull();
    expect(fetchSpy).not.toHaveBeenCalled(); // gate first
  });
});

// ---------------------------------------------------------------------------
// resolvePruneLock — the lock-gate continuation
// ---------------------------------------------------------------------------

describe("resolvePruneLock", () => {
  function seedGate(
    pref: "ask" | "always",
    pending: { plain: string[]; actioned: string[] }
  ) {
    // The locked row sits in a real (≥2-member) group; resolvePruneLock unlocks
    // it by path via setLocks, which just needs the row present in the manifest.
    const lockGroup = makeGroup(1, ["/p/lock.jpg", "/p/sibling.jpg"]);
    lockGroup.items[0].is_locked = true;
    seedManifest([lockGroup]);
    useAppStore.setState((s) => ({
      execute: {
        ...s.execute,
        prunePending: { plain: pending.plain, actioned: pending.actioned, pref },
        lockConflict: { paths: ["/p/lock.jpg"], op: "prune", originalPaths: null },
      },
    }));
  }

  it("cancel on ask → holds locked, opens the dialog for unlocked buckets", async () => {
    seedGate("ask", { plain: [], actioned: ["/p/x.jpg"] });
    await useAppStore.getState().resolvePruneLock("cancel");
    const { prunePrompt, lockConflict, prunePending } =
      useAppStore.getState().execute;
    expect(lockConflict).toBeNull();
    expect(prunePending).toBeNull();
    expect(prunePrompt!.actioned).toEqual(["/p/x.jpg"]);
    expect(prunePrompt!.lockedToPrune).toEqual([]); // locked held
    expect(client.patchLocks).not.toHaveBeenCalled();
  });

  it("unlocked-only on ask → holds locked (like cancel), no unlock", async () => {
    // The prune-context lock gate's "Unlocked only" verdict: hold the locked
    // singleton, continue with the unlocked buckets (lockedToPrune empty). Pins
    // that the store does NOT unlock — a mis-wire to unlock-apply would unlock.
    seedGate("ask", { plain: [], actioned: ["/p/x.jpg"] });
    await useAppStore.getState().resolvePruneLock("unlocked-only");
    const p = useAppStore.getState().execute.prunePrompt;
    expect(p!.actioned).toEqual(["/p/x.jpg"]);
    expect(p!.lockedToPrune).toEqual([]); // locked held
    expect(client.patchLocks).not.toHaveBeenCalled();
  });

  it("unlock-apply on ask → unlocks, then the dialog carries lockedToPrune", async () => {
    seedGate("ask", { plain: [], actioned: ["/p/x.jpg"] });
    await useAppStore.getState().resolvePruneLock("unlock-apply");
    expect(client.patchLocks).toHaveBeenCalledWith("/data/scan.db", [
      { file_path: "/p/lock.jpg", locked: false },
    ]);
    const p = useAppStore.getState().execute.prunePrompt;
    expect(p!.lockedToPrune).toEqual(["/p/lock.jpg"]);
  });

  it("unlock-apply on always → unlocks, then auto-prunes buckets + locked", async () => {
    seedGate("always", { plain: ["/p/a.jpg"], actioned: [] });
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(ok200({ pruned: [], locked_skipped: [], groups: [] }));
    await useAppStore.getState().resolvePruneLock("unlock-apply");
    expect(client.patchLocks).toHaveBeenCalled();
    const body = JSON.parse(
      (fetchSpy.mock.calls[0][1] as { body: string }).body
    );
    expect([...body.paths].sort()).toEqual(["/p/a.jpg", "/p/lock.jpg"]);
    expect(useAppStore.getState().execute.prunePrompt).toBeNull();
  });

  it("cancel on always → holds locked, sweeps only the unlocked buckets", async () => {
    seedGate("always", { plain: ["/p/a.jpg"], actioned: [] });
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(ok200({ pruned: [], locked_skipped: [], groups: [] }));
    await useAppStore.getState().resolvePruneLock("cancel");
    expect(client.patchLocks).not.toHaveBeenCalled();
    const body = JSON.parse(
      (fetchSpy.mock.calls[0][1] as { body: string }).body
    );
    expect(body.paths).toEqual(["/p/a.jpg"]); // locked NOT included
  });

  it("unlock-apply on ask with a failing unlock holds the locked row OUT of the prune set", async () => {
    // #702: when the unlock PATCH rejects, setLocks reverts the row (still
    // locked). resolvePruneLock must NOT fold the still-locked path into the
    // prune set — it holds it back (lockedToPrune stays []) yet still offers the
    // unrelated unlocked buckets, surfacing a prune-context error. This is the
    // mirror of the success test above (which folds /p/lock.jpg into
    // lockedToPrune); RED against the pre-fix source, which folded it regardless.
    seedGate("ask", { plain: [], actioned: ["/p/x.jpg"] });
    vi.mocked(client.patchLocks).mockRejectedValueOnce(new Error("locks failed"));
    await useAppStore.getState().resolvePruneLock("unlock-apply");
    const p = useAppStore.getState().execute.prunePrompt;
    expect(p!.actioned).toEqual(["/p/x.jpg"]); // unlocked bucket still offered
    expect(p!.lockedToPrune).toEqual([]); // still-locked path NOT folded (the bug)
    // setLocks reverted the optimistic unlock — the row stays locked.
    expect(useAppStore.getState().manifest.groups[0].items[0].is_locked).toBe(
      true
    );
    // A prune-context error surfaced (not just the generic PATCH message).
    expect(useAppStore.getState().manifest.error).toContain("held back");
  });

  it("unlock-apply on always with a failing unlock prunes only the unlocked buckets", async () => {
    // The 'always' counterpart: the unrelated unlocked bucket still auto-prunes,
    // but the still-locked path is NOT folded into the POST. RED against the
    // pre-fix source, which posted /p/a.jpg AND the still-locked /p/lock.jpg.
    seedGate("always", { plain: ["/p/a.jpg"], actioned: [] });
    vi.mocked(client.patchLocks).mockRejectedValueOnce(new Error("locks failed"));
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(ok200({ pruned: [], locked_skipped: [], groups: [] }));
    await useAppStore.getState().resolvePruneLock("unlock-apply");
    const pruneCall = fetchSpy.mock.calls.find(([url]) =>
      String(url).includes("/api/prune")
    );
    expect(pruneCall).toBeDefined();
    const body = JSON.parse((pruneCall![1] as { body: string }).body);
    expect(body.paths).toEqual(["/p/a.jpg"]); // locked NOT folded into the POST
    // A prune-context error surfaced for the held locked item.
    expect(useAppStore.getState().manifest.error).toContain("held back");
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
      useAppStore.getState().applyBulkDecide()
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

// ---------------------------------------------------------------------------
// openActionDialog – initialField (#735, right-click "Set Action by Field…")
// ---------------------------------------------------------------------------

describe("openActionDialog – initialField (#735)", () => {
  it("argless call keeps the default field (unchanged prior behaviour)", () => {
    useAppStore.getState().openActionDialog();
    expect(useAppStore.getState().action.field).toBe("File Name");
    expect(useAppStore.getState().action.actionDialogOpen).toBe(true);
  });

  it("a valid field value sets action.field to that value", () => {
    useAppStore.getState().openActionDialog("Size (Bytes)");
    expect(useAppStore.getState().action.field).toBe("Size (Bytes)");
    expect(useAppStore.getState().action.actionDialogOpen).toBe(true);
  });

  it("an unrecognised string value falls back to the default field", () => {
    useAppStore.getState().openActionDialog("Not A Real Field");
    expect(useAppStore.getState().action.field).toBe("File Name");
  });

  it("a non-string value (e.g. a stray DOM event from onClick={openActionDialog}) falls back to the default field", () => {
    // Regression guard: App.tsx's toolbar button wires onClick={openActionDialog}
    // directly, so the browser passes a MouseEvent as the first argument. That
    // must never leak into store.action.field.
    useAppStore.getState().openActionDialog({ type: "click" } as never);
    expect(useAppStore.getState().action.field).toBe("File Name");
  });

  it("resets transient preview/error state on open, same as the argless path", () => {
    useAppStore.setState((s) => ({
      action: { ...s.action, previewMatched: 5, actionError: "boom" },
    }));
    useAppStore.getState().openActionDialog("Similarity");
    const action = useAppStore.getState().action;
    expect(action.field).toBe("Similarity");
    expect(action.previewMatched).toBe(-1);
    expect(action.actionError).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// selection slice — main result-tree multi-selection (Phase 4)
// ---------------------------------------------------------------------------

const ORDERED = ["/p/a.jpg", "/p/b.jpg", "/p/c.jpg", "/p/d.jpg"];

describe("selection – setSelection replaces and anchors on the last entry", () => {
  it("sets selectedPaths verbatim and anchor = last path", () => {
    useAppStore.getState().setSelection(["/p/a.jpg", "/p/b.jpg"]);
    const { selection } = useAppStore.getState();
    expect(selection.selectedPaths).toEqual(["/p/a.jpg", "/p/b.jpg"]);
    expect(selection.anchorPath).toBe("/p/b.jpg");
  });

  it("an empty selection clears the anchor", () => {
    useAppStore.getState().setSelection(["/p/a.jpg"]);
    useAppStore.getState().setSelection([]);
    const { selection } = useAppStore.getState();
    expect(selection.selectedPaths).toEqual([]);
    expect(selection.anchorPath).toBeNull();
  });
});

describe("selection – toggleSelection (Ctrl/Cmd+click) adds, removes, re-anchors", () => {
  it("adds an absent path, removes a present one, re-anchoring each time", () => {
    const { toggleSelection } = useAppStore.getState();
    toggleSelection("/p/a.jpg");
    expect(useAppStore.getState().selection.selectedPaths).toEqual(["/p/a.jpg"]);
    toggleSelection("/p/b.jpg");
    expect(useAppStore.getState().selection.selectedPaths).toEqual([
      "/p/a.jpg",
      "/p/b.jpg",
    ]);
    expect(useAppStore.getState().selection.anchorPath).toBe("/p/b.jpg");
    // Toggling an already-selected path removes it (and still re-anchors to it).
    toggleSelection("/p/a.jpg");
    expect(useAppStore.getState().selection.selectedPaths).toEqual(["/p/b.jpg"]);
    expect(useAppStore.getState().selection.anchorPath).toBe("/p/a.jpg");
  });
});

describe("selection – extendSelection (Shift+click) ranges over the visible order", () => {
  it("selects the inclusive forward range from the anchor", () => {
    useAppStore.getState().setSelection(["/p/b.jpg"]); // anchor = b
    useAppStore.getState().extendSelection("/p/d.jpg", ORDERED);
    expect(useAppStore.getState().selection.selectedPaths).toEqual([
      "/p/b.jpg",
      "/p/c.jpg",
      "/p/d.jpg",
    ]);
    // Anchor is preserved so a follow-up Shift+click re-ranges from b.
    expect(useAppStore.getState().selection.anchorPath).toBe("/p/b.jpg");
  });

  it("selects the inclusive reverse range when the target precedes the anchor", () => {
    useAppStore.getState().setSelection(["/p/c.jpg"]); // anchor = c
    useAppStore.getState().extendSelection("/p/a.jpg", ORDERED);
    expect(useAppStore.getState().selection.selectedPaths).toEqual([
      "/p/a.jpg",
      "/p/b.jpg",
      "/p/c.jpg",
    ]);
  });

  it("falls back to a single-path selection when there is no usable anchor", () => {
    useAppStore.getState().clearSelection(); // anchor = null
    useAppStore.getState().extendSelection("/p/c.jpg", ORDERED);
    const { selection } = useAppStore.getState();
    expect(selection.selectedPaths).toEqual(["/p/c.jpg"]);
    expect(selection.anchorPath).toBe("/p/c.jpg");
  });

  it("falls back to a single-path selection when the anchor scrolled out of view", () => {
    useAppStore.getState().setSelection(["/p/offscreen.jpg"]); // anchor not in ORDERED
    useAppStore.getState().extendSelection("/p/b.jpg", ORDERED);
    expect(useAppStore.getState().selection.selectedPaths).toEqual(["/p/b.jpg"]);
  });
});

describe("selection – clearSelection empties the slice", () => {
  it("resets selectedPaths and anchor", () => {
    useAppStore.getState().setSelection(["/p/a.jpg", "/p/b.jpg"]);
    useAppStore.getState().clearSelection();
    const { selection } = useAppStore.getState();
    expect(selection.selectedPaths).toEqual([]);
    expect(selection.anchorPath).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// batch decision / lock actions (multi-selection context menu)
// ---------------------------------------------------------------------------

describe("setDecisions – one PATCH for the whole list, optimistic on all", () => {
  it("applies the decision to every listed row and PATCHes once", async () => {
    seedManifest([makeGroup(1, ["/p/a.jpg", "/p/b.jpg", "/p/c.jpg"])]);
    vi.mocked(client.patchDecisions).mockResolvedValue({ updated: 2 });

    await useAppStore.getState().setDecisions(["/p/a.jpg", "/p/c.jpg"], "delete");

    const items = useAppStore.getState().manifest.groups[0].items;
    expect(items[0].user_decision).toBe("delete"); // a
    expect(items[1].user_decision).toBe(""); // b — untouched
    expect(items[2].user_decision).toBe("delete"); // c
    expect(client.patchDecisions).toHaveBeenCalledTimes(1);
    expect(client.patchDecisions).toHaveBeenCalledWith(
      "/data/scan.db",
      [
        { file_path: "/p/a.jpg", decision: "delete" },
        { file_path: "/p/c.jpg", decision: "delete" },
      ],
      { forceLocked: false }
    );
  });

  it("is a no-op for an empty path list (no PATCH)", async () => {
    seedManifest([makeGroup(1, ["/p/a.jpg"])]);
    await useAppStore.getState().setDecisions([], "delete");
    expect(client.patchDecisions).not.toHaveBeenCalled();
  });

  it("reverts every row and records the error when the PATCH fails", async () => {
    seedManifest([makeGroup(1, ["/p/a.jpg", "/p/b.jpg"])]);
    vi.mocked(client.patchDecisions).mockRejectedValue(new Error("server error"));

    await useAppStore.getState().setDecisions(["/p/a.jpg", "/p/b.jpg"], "delete");

    const items = useAppStore.getState().manifest.groups[0].items;
    expect(items[0].user_decision).toBe(""); // reverted
    expect(items[1].user_decision).toBe(""); // reverted
    expect(useAppStore.getState().manifest.error).toBe("server error");
  });

  // #733 — a 409 locked_paths conflict reverts the optimistic apply (same as
  // any other failure) but routes to execute.lockConflict INSTEAD of
  // manifest.error, so LockConfirmDialog (not a bare error banner) drives
  // the recovery — mirrors removeFromList's 409 flow.
  it("on 409 locked_paths: reverts optimistically, sets lockConflict (op=decision), leaves manifest.error untouched", async () => {
    seedManifest([makeGroup(1, ["/p/a.jpg", "/p/b.jpg"])]);
    const conflict = new client.ApiConflictError("locked_paths", ["/p/b.jpg"]);
    vi.mocked(client.patchDecisions).mockRejectedValueOnce(conflict);

    await useAppStore
      .getState()
      .setDecisions(["/p/a.jpg", "/p/b.jpg"], "delete");

    const items = useAppStore.getState().manifest.groups[0].items;
    expect(items[0].user_decision).toBe(""); // reverted
    expect(items[1].user_decision).toBe(""); // reverted
    expect(useAppStore.getState().manifest.error).toBeNull();
    expect(useAppStore.getState().execute.lockConflict).toEqual({
      paths: ["/p/b.jpg"],
      op: "decision",
      originalPaths: ["/p/a.jpg", "/p/b.jpg"],
      pendingDecision: "delete",
    });
  });
});

describe("setLocks – one PATCH for the whole list, reverts all on failure", () => {
  it("locks every listed row and PATCHes once", async () => {
    seedManifest([makeGroup(1, ["/p/a.jpg", "/p/b.jpg"])]);
    vi.mocked(client.patchLocks).mockResolvedValue({ updated: 2 });

    await useAppStore.getState().setLocks(["/p/a.jpg", "/p/b.jpg"], true);

    const items = useAppStore.getState().manifest.groups[0].items;
    expect(items[0].is_locked).toBe(true);
    expect(items[1].is_locked).toBe(true);
    expect(client.patchLocks).toHaveBeenCalledTimes(1);
    expect(client.patchLocks).toHaveBeenCalledWith("/data/scan.db", [
      { file_path: "/p/a.jpg", locked: true },
      { file_path: "/p/b.jpg", locked: true },
    ]);
  });

  it("reverts the lock on every row when the PATCH fails", async () => {
    seedManifest([makeGroup(1, ["/p/a.jpg", "/p/b.jpg"])]);
    vi.mocked(client.patchLocks).mockRejectedValue(new Error("locks failed"));

    await useAppStore.getState().setLocks(["/p/a.jpg", "/p/b.jpg"], true);

    const items = useAppStore.getState().manifest.groups[0].items;
    expect(items[0].is_locked).toBe(false);
    expect(items[1].is_locked).toBe(false);
    expect(useAppStore.getState().manifest.error).toBe("locks failed");
  });
});

// ---------------------------------------------------------------------------
// clear-on-mutation — a stale selection must never survive a groups replacement
// ---------------------------------------------------------------------------

describe("selection is cleared whenever manifest.groups is replaced", () => {
  it("removeFromList success clears the selection", async () => {
    seedManifest([makeGroup(1, ["/p/h.jpg", "/p/i.jpg"])]);
    useAppStore.getState().setSelection(["/p/h.jpg", "/p/i.jpg"]);

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      ok200({ removed: 1, groups: [makeGroup(1, ["/p/i.jpg"])] })
    );

    await useAppStore.getState().removeFromList(["/p/h.jpg"]);

    expect(useAppStore.getState().selection.selectedPaths).toEqual([]);
    expect(useAppStore.getState().selection.anchorPath).toBeNull();
  });

  it("loadManifest clears any selection from the previous manifest", async () => {
    useAppStore.getState().setSelection(["/old/x.jpg"]);
    vi.mocked(client.getManifest).mockResolvedValue({
      manifest_path: "/data/out.db",
      groups: [makeGroup(1, ["/p/a.jpg"])],
      total_groups: 1,
      total_files: 1,
    });

    await useAppStore.getState().loadManifest("/data/out.db");

    expect(useAppStore.getState().selection.selectedPaths).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// openExecuteDialog scope (#430 group-pull — "Execute (only selected)")
// ---------------------------------------------------------------------------

describe("openExecuteDialog – group-pull scope from a selection", () => {
  beforeEach(() => {
    // Two groups: A = {a0,a1}, B = {b0,b1}.
    seedManifest([
      makeGroup(1, ["/p/a0.jpg", "/p/a1.jpg"]),
      makeGroup(2, ["/p/b0.jpg", "/p/b1.jpg"]),
    ]);
  });

  it("expands a single selected path to its whole group's number", () => {
    // Select just ONE row in group A; the dialog must scope to group 1 (both
    // members pulled in), proving #430 group-pull rather than per-row scope.
    useAppStore.getState().openExecuteDialog(["/p/a0.jpg"]);
    expect(useAppStore.getState().execute.executeOpen).toBe(true);
    expect(useAppStore.getState().execute.scopeGroupNumbers).toEqual([1]);
  });

  it("unions the groups of a cross-group selection", () => {
    useAppStore.getState().openExecuteDialog(["/p/a1.jpg", "/p/b0.jpg"]);
    expect(useAppStore.getState().execute.scopeGroupNumbers).toEqual([1, 2]);
  });

  it("opens unscoped (null) when called with no argument", () => {
    useAppStore.getState().openExecuteDialog();
    expect(useAppStore.getState().execute.executeOpen).toBe(true);
    expect(useAppStore.getState().execute.scopeGroupNumbers).toBeNull();
  });

  it("opens unscoped when the selection is empty", () => {
    useAppStore.getState().openExecuteDialog([]);
    expect(useAppStore.getState().execute.scopeGroupNumbers).toBeNull();
  });

  it("opens unscoped when no selected path matches any manifest row", () => {
    useAppStore.getState().openExecuteDialog(["/p/ghost.jpg"]);
    expect(useAppStore.getState().execute.scopeGroupNumbers).toBeNull();
  });

  it("closeExecuteDialog clears the scope back to null", () => {
    useAppStore.getState().openExecuteDialog(["/p/a0.jpg"]);
    expect(useAppStore.getState().execute.scopeGroupNumbers).toEqual([1]);
    useAppStore.getState().closeExecuteDialog();
    expect(useAppStore.getState().execute.executeOpen).toBe(false);
    expect(useAppStore.getState().execute.scopeGroupNumbers).toBeNull();
  });
});
