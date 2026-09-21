// Tests for useScanSSE.
//
// Strategy: replace globalThis.EventSource with a fake that records calls and
// lets us fire synthetic events. We then assert that:
//  1. Named-event listeners dispatch the right ingestScanEvent calls.
//  2. A terminal event closes the EventSource (no further reconnect).
//  3. Unmount (cleanup) closes the EventSource.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useScanSSE } from "./useScanSSE";
import { useAppStore } from "../store/useAppStore";

// ---------------------------------------------------------------------------
// Fake EventSource
// ---------------------------------------------------------------------------

type EventListener = (ev: MessageEvent) => void;

class FakeEventSource {
  url: string;
  listeners: Record<string, EventListener[]> = {};
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(name: string, handler: EventListener): void {
    if (!this.listeners[name]) this.listeners[name] = [];
    this.listeners[name].push(handler);
  }

  removeEventListener(name: string, handler: EventListener): void {
    if (!this.listeners[name]) return;
    this.listeners[name] = this.listeners[name].filter((h) => h !== handler);
  }

  close(): void {
    this.closed = true;
  }

  /** Helper: fire an event on this instance. */
  emit(name: string, data: unknown): void {
    const handlers = this.listeners[name] ?? [];
    const ev = { data: JSON.stringify(data) } as MessageEvent;
    for (const h of handlers) h(ev);
  }

  static instances: FakeEventSource[] = [];
  static reset(): void {
    FakeEventSource.instances = [];
  }
}

// ---------------------------------------------------------------------------
// Store mock: capture ingestScanEvent calls.
// ---------------------------------------------------------------------------

const ingestMock = vi.fn();

vi.mock("../store/useAppStore", () => ({
  useAppStore: vi.fn((selector: (s: { ingestScanEvent: typeof ingestMock }) => unknown) =>
    selector({ ingestScanEvent: ingestMock })
  ),
}));

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

let originalEventSource: typeof EventSource;

beforeEach(() => {
  FakeEventSource.reset();
  ingestMock.mockClear();
  originalEventSource = globalThis.EventSource;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).EventSource = FakeEventSource;
});

afterEach(() => {
  globalThis.EventSource = originalEventSource;
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useScanSSE – no taskId", () => {
  it("does not open an EventSource when taskId is null", () => {
    renderHook(() => useScanSSE(null));
    expect(FakeEventSource.instances).toHaveLength(0);
  });
});

describe("useScanSSE – non-terminal event dispatches to store", () => {
  it("calls ingestScanEvent with stage payload", () => {
    renderHook(() => useScanSSE("task-1"));
    expect(FakeEventSource.instances).toHaveLength(1);
    const es = FakeEventSource.instances[0];

    const payload = {
      event: "stage",
      stage_name: "HASH",
      completed: 10,
      total: 50,
      files_per_sec: 5,
    };
    es.emit("stage", payload);

    expect(ingestMock).toHaveBeenCalledWith("stage", payload);
    // Non-terminal: connection stays open.
    expect(es.closed).toBe(false);
  });
});

describe("useScanSSE – log event dispatches to store", () => {
  it("calls ingestScanEvent with log payload", () => {
    renderHook(() => useScanSSE("task-2"));
    const es = FakeEventSource.instances[0];

    const payload = { event: "log", msg: "scanning..." };
    es.emit("log", payload);

    expect(ingestMock).toHaveBeenCalledWith("log", payload);
    expect(es.closed).toBe(false);
  });
});

describe("useScanSSE – terminal event closes EventSource", () => {
  it.each(["finished", "failed", "completed_empty"])(
    "%s closes the connection",
    (eventName) => {
      renderHook(() => useScanSSE("task-3"));
      const es = FakeEventSource.instances[0];

      const payloads: Record<string, unknown> = {
        finished: { event: "finished", output_path: "/out.db" },
        failed: { event: "failed", msg: "error" },
        completed_empty: { event: "completed_empty" },
      };
      es.emit(eventName, payloads[eventName]);

      expect(ingestMock).toHaveBeenCalledWith(eventName, payloads[eventName]);
      expect(es.closed).toBe(true);
    }
  );
});

describe("useScanSSE – unmount cleans up", () => {
  it("closes the EventSource on unmount", () => {
    const { unmount } = renderHook(() => useScanSSE("task-4"));
    const es = FakeEventSource.instances[0];
    expect(es.closed).toBe(false);

    unmount();
    expect(es.closed).toBe(true);
  });
});

describe("useScanSSE – taskId change reopens connection", () => {
  it("closes old EventSource and opens a new one when taskId changes", () => {
    const { rerender } = renderHook(
      ({ taskId }: { taskId: string | null }) => useScanSSE(taskId),
      { initialProps: { taskId: "task-A" as string | null } }
    );
    expect(FakeEventSource.instances).toHaveLength(1);
    const firstEs = FakeEventSource.instances[0];

    rerender({ taskId: "task-B" });

    // Old connection should be closed.
    expect(firstEs.closed).toBe(true);
    // New connection should be open.
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[1].closed).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// #661 — connection-drop watchdog (fake timers)
// ---------------------------------------------------------------------------

describe("useScanSSE – #661 connection watchdog", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("fires a 'failed' error and closes the stream after ~90s of silence", () => {
    renderHook(() => useScanSSE("task-wd-1"));
    const es = FakeEventSource.instances[0];

    // Just under the window: nothing yet.
    vi.advanceTimersByTime(89_000);
    expect(ingestMock).not.toHaveBeenCalled();
    expect(es.closed).toBe(false);

    // Cross the window → connection-lost surfaces as a failed status.
    vi.advanceTimersByTime(2_000);
    expect(ingestMock).toHaveBeenCalledWith("failed", {
      event: "failed",
      msg: expect.stringContaining("Lost connection"),
    });
    // The futile native auto-reconnect loop is stopped.
    expect(es.closed).toBe(true);
  });

  it("a named 'ping' keepalive rearms the watchdog (no false drop)", () => {
    renderHook(() => useScanSSE("task-wd-2"));
    const es = FakeEventSource.instances[0];

    // A ping arrives at 60s (well within the 90s window)...
    vi.advanceTimersByTime(60_000);
    es.emit("ping", {});
    // ...so 60s later (120s total, only 60s since the ping) still no drop.
    vi.advanceTimersByTime(60_000);
    expect(ingestMock).not.toHaveBeenCalled();
    expect(es.closed).toBe(false);

    // Now go silent past a full window from the last ping → drop.
    vi.advanceTimersByTime(91_000);
    expect(ingestMock).toHaveBeenCalledWith("failed", {
      event: "failed",
      msg: expect.stringContaining("Lost connection"),
    });
  });

  it("a real progress event also rearms the watchdog", () => {
    renderHook(() => useScanSSE("task-wd-3"));
    const es = FakeEventSource.instances[0];

    vi.advanceTimersByTime(80_000);
    es.emit("stage", { event: "stage", stage_name: "HASH", completed: 1, total: 9, files_per_sec: 1 });
    ingestMock.mockClear(); // drop the stage call; we only care about a spurious failed
    vi.advanceTimersByTime(80_000); // 80s since the stage — still inside the window
    expect(ingestMock).not.toHaveBeenCalledWith(
      "failed",
      expect.objectContaining({ msg: expect.stringContaining("Lost connection") })
    );
    expect(es.closed).toBe(false);
  });

  it("a terminal event disarms the watchdog (no late connection-lost)", () => {
    renderHook(() => useScanSSE("task-wd-4"));
    const es = FakeEventSource.instances[0];

    es.emit("finished", { event: "finished", output_path: "/out.db" });
    expect(es.closed).toBe(true);
    ingestMock.mockClear();

    // Long after the terminal event — the watchdog must NOT fire.
    vi.advanceTimersByTime(300_000);
    expect(ingestMock).not.toHaveBeenCalled();
  });
});

// Satisfy the type system — useAppStore is only used as a selector in the
// hook; the mock above captures all calls.
void useAppStore;
