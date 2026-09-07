// React hook that opens a native EventSource for a scan task and dispatches
// every incoming SSE event into the Zustand store via ingestScanEvent.
//
// Design decisions:
// - Uses native EventSource (not a polyfill) — auto-reconnect and
//   Last-Event-ID resume are handled by the browser at zero cost.
// - Terminal events (finished / failed / completed_empty) close the
//   EventSource — no further reconnect is needed.
// - onerror is left to auto-retry unless the connection is already terminal.
// - No manual reconnect logic: backend guarantees Last-Event-ID replay.
// - #661 connection-drop watchdog: a timer, reset on EVERY incoming event
//   (real progress OR the server's named `ping` keepalive), that surfaces a
//   "connection lost" error if the stream goes fully silent past the window.
//   This is the ONLY way to distinguish a healthy-but-quiet stream (pings
//   flowing) from a dead server: EventSource.onerror can't (it also fires on
//   recoverable transient blips), and SSE keepalive *comments* fire no JS
//   handler at all — which is why the backend now sends a named `ping` event.

import { useEffect, useRef } from "react";
import { scanEventsUrl } from "../api/client";
import { useAppStore } from "../store/useAppStore";

const TERMINAL_EVENTS = new Set(["finished", "failed", "completed_empty"]);

// The server emits a `ping` keepalive every ~30s while a stream is idle
// (scan.py `_KEEPALIVE_TIMEOUT_S`). 90s tolerates two dropped pings (network
// jitter, a brief EventSource reconnect + Last-Event-ID replay) before we
// declare the connection lost — long enough that a legitimately slow-but-alive
// scan never trips it, short enough that a dead server surfaces promptly.
const CONNECTION_WATCHDOG_MS = 90_000;

// Shown in the scan dialog when the watchdog fires. Honest about the decoupled
// backend: the scan runs on a server-side thread independent of this stream, so
// it may still be completing even though the browser lost the progress feed.
const CONNECTION_LOST_MSG =
  "Lost connection to the scanner. The scan may still be running on the " +
  "server — reopen Scan to check the result.";

export function useScanSSE(taskId: string | null): void {
  const ingestScanEvent = useAppStore((s) => s.ingestScanEvent);

  // Keep a ref to the active EventSource so the cleanup function (and
  // terminal-event handler) can close it even after re-renders.
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (taskId === null) return;

    const url = scanEventsUrl(taskId);
    const es = new EventSource(url);
    esRef.current = es;

    let closed = false;
    let watchdog: ReturnType<typeof setTimeout> | null = null;

    function clearWatchdog(): void {
      if (watchdog !== null) {
        clearTimeout(watchdog);
        watchdog = null;
      }
    }

    function onConnectionLost(): void {
      // Surface a failed-status error (ScanDialog renders scan.error and keeps
      // the dialog open) and stop the futile native auto-reconnect loop.
      ingestScanEvent("failed", { event: "failed", msg: CONNECTION_LOST_MSG });
      closeOnce();
    }

    /** (Re)arm the connection-drop watchdog — called on every incoming event. */
    function resetWatchdog(): void {
      if (closed) return;
      clearWatchdog();
      watchdog = setTimeout(onConnectionLost, CONNECTION_WATCHDOG_MS);
    }

    function closeOnce(): void {
      if (closed) return;
      closed = true;
      clearWatchdog();
      es.close();
      esRef.current = null;
    }

    /** Generic handler for a named event: parse JSON and dispatch to store. */
    function makeHandler(name: string) {
      return (ev: MessageEvent): void => {
        // Any event — including the server's `ping` keepalive handled below —
        // means the stream is alive; rearm the watchdog first.
        resetWatchdog();
        let payload: unknown;
        try {
          payload = JSON.parse(ev.data as string) as unknown;
        } catch {
          // Malformed JSON in an SSE frame — surface in log and move on.
          ingestScanEvent("log", { msg: `[SSE parse error for event "${name}"]`, event: "log" });
          return;
        }
        ingestScanEvent(name, payload);
        if (TERMINAL_EVENTS.has(name)) {
          closeOnce();
        }
      };
    }

    const eventNames = [
      "log",
      "stage",
      "finished",
      "failed",
      "completed_empty",
      "hash_pool_measured",
      "read_knee_measured",
    ] as const;

    // Store handler references so they can be passed to removeEventListener
    // in cleanup. Without storing the reference, the exact same function
    // object is needed to deregister — anonymous returns from makeHandler()
    // would be permanently lost and removeEventListener would silently no-op.
    const handlers = eventNames.map((name) => {
      const h = makeHandler(name);
      es.addEventListener(name, h);
      return { name, h } as { name: string; h: (ev: MessageEvent) => void };
    });

    // #661 — the named keepalive. It carries no scan data (nothing to ingest);
    // its sole job is to prove the server is alive and rearm the watchdog.
    const pingHandler = (): void => resetWatchdog();
    es.addEventListener("ping", pingHandler);

    // Arm the watchdog immediately: a server that is dead from the very first
    // connection never delivers any event, and must still surface an error.
    resetWatchdog();

    // onerror: leave to native auto-retry unless connection is already closed.
    // We do NOT set es.onerror to closeOnce because transient network errors
    // should reconnect (EventSource CONNECTING state) not terminate the stream.
    // The backend will replay missing events via Last-Event-ID on reconnect;
    // a drop that outlasts the watchdog window is what surfaces the error.

    return () => {
      clearWatchdog();
      for (const { name, h } of handlers) {
        es.removeEventListener(name, h);
      }
      es.removeEventListener("ping", pingHandler);
      closeOnce();
    };
  // Re-run only when taskId changes (new scan started) or ingestScanEvent ref
  // changes (store re-created in tests).
  }, [taskId, ingestScanEvent]);
}
