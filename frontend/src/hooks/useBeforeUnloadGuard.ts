// useBeforeUnloadGuard — warn before leaving the tab while a scan is running.
//
// Web port of the Qt MainWindow close-guard (#468): the desktop pops a
// "Scan in progress" Yes/No QMessageBox from closeEvent when scan_running
// (No = keep running, Yes = cancel the worker and close). The browser has no
// interceptable window-close with a custom dialog — the only close-time hook is
// the native `beforeunload` event, which we can ARM (preventDefault) but whose
// Leave/Stay UI the browser owns. So the web analog is a `beforeunload` handler
// that blocks unload only while a scan is running. This PR ships the WARN only:
// it does NOT cancel the worker when the user chooses to leave. That is a
// deliberate scope choice, NOT a platform impossibility — Qt's "Yes = cancel +
// close" branch is reproducible with navigator.sendBeacon(`/api/scan/{id}/cancel`)
// (the endpoint is body-less + auth-free) fired from a `pagehide` listener
// (pagehide, not beforeunload, because beforeunload fires before the Stay/Leave
// choice and would cancel even on Stay). Leaving it unimplemented means a
// leave-mid-scan orphans the worker (the backend deliberately does NOT cancel on
// SSE disconnect — app/web/routes/scan.py) and the registry then 409-rejects the
// next scan until the orphan self-finishes. That gap interacts with the backend
// disconnect policy + the SSE-resume design, so it is tracked as a follow-up
// (#703) rather than bundled into this warn-guard. Today the scan stays
// cancellable via the ScanDialog Cancel button. The guard only warns.
//
// The guard is CONDITIONAL — it blocks unload only while scan.status==='running'
// (idle/finished/failed/cancelled navigate freely). The handler reads the live
// status via getState() at unload time (no stale closure), and is registered
// once for the app's lifetime.
//
// QA-safety invariant the whole web Playwright suite relies on: no scenario
// navigates (page.reload/goto/close) while a scan is running — every scan
// scenario blocks on wait_manifest_loaded or cancels first — so this guard never
// fires a native confirm during the batch (which would hang/auto-dismiss). The
// guard is asserted directly (armed-while-running vs dormant-otherwise) by
// qa/web/scenarios/s63 via a dispatched cancelable beforeunload event.

import { useEffect } from "react";

import { useAppStore } from "../store/useAppStore";

export function useBeforeUnloadGuard(): void {
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (useAppStore.getState().scan.status !== "running") return;
      // Arm the browser-native confirm. Modern browsers ignore custom text and
      // show a generic prompt; preventDefault() is the canonical trigger and is
      // what sets event.defaultPrevented — the signal the QA scenario observes.
      // returnValue="" is the legacy belt-and-suspenders opt-in for older
      // engines; it does not affect defaultPrevented.
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, []);
}
