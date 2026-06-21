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

import { useEffect, useRef } from "react";
import { scanEventsUrl } from "../api/client";
import { useAppStore } from "../store/useAppStore";

const TERMINAL_EVENTS = new Set(["finished", "failed", "completed_empty"]);

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

    function closeOnce(): void {
      if (closed) return;
      closed = true;
      es.close();
      esRef.current = null;
    }

    /** Generic handler for a named event: parse JSON and dispatch to store. */
    function makeHandler(name: string) {
      return (ev: MessageEvent): void => {
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

    // onerror: leave to native auto-retry unless connection is already closed.
    // We do NOT set es.onerror to closeOnce because transient network errors
    // should reconnect (EventSource CONNECTING state) not terminate the stream.
    // The backend will replay missing events via Last-Event-ID on reconnect.

    return () => {
      for (const { name, h } of handlers) {
        es.removeEventListener(name, h);
      }
      closeOnce();
    };
  // Re-run only when taskId changes (new scan started) or ingestScanEvent ref
  // changes (store re-created in tests).
  }, [taskId, ingestScanEvent]);
}
