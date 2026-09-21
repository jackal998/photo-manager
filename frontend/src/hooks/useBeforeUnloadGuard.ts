// useBeforeUnloadGuard — warn before leaving the tab while a scan is running.
//
// Web port of the Qt MainWindow close-guard (#468): the desktop pops a
// "Scan in progress" Yes/No QMessageBox from closeEvent when scan_running
// (No = keep running, Yes = cancel the worker and close). The browser has no
// interceptable window-close with a custom dialog — the only close-time hook is
// the native `beforeunload` event, which we can ARM (preventDefault) but whose
// Leave/Stay UI the browser owns. So the web analog has two parts:
//
//   1. WARN (beforeunload): block unload only while a scan is running so the
//      browser shows its native Leave/Stay prompt.
//   2. LEAVE = CANCEL (pagehide, #703): on a CONFIRMED unload while a scan is
//      running, best-effort `navigator.sendBeacon('/api/scan/{id}/cancel')`
//      (the endpoint is body-less + auth-free) so the worker doesn't orphan —
//      the backend deliberately does NOT cancel on SSE disconnect
//      (app/web/routes/scan.py), and an orphaned worker makes the registry
//      409-reject the next scan until it self-finishes. This ports Qt's
//      "Yes = cancel + close" branch.
//
// Event choice for the cancel: `pagehide`, NOT `beforeunload` (which fires on the
// Stay/Leave prompt, before the user commits → would cancel even on Stay) and NOT
// `visibilitychange` (fires on tab-switch/minimize → would cancel on a mere tab
// switch). A bfcache STASH is skipped via `e.persisted` — the page is paused, not
// left, and `pageshow` can restore the still-running scan (a no-op on the
// Chromium/WebView2 target, where the open EventSource forces the page
// bfcache-ineligible so `persisted` is already false during a scan). The
// `sendBeacon` call is optional-chained so the pagehide handler never throws in a
// UA that lacks the API. Best-effort by spec: if the browser drops the beacon the
// orphan persists and self-heals on natural finish — accepted as a UX tradeoff vs
// today's 100%-orphan baseline (Option B, a backend grace-period, intentionally
// not built).
//
// Both handlers are CONDITIONAL on the live `scan.status==='running'` read via
// getState() at fire time (no stale closure), and are registered ONCE in a single
// useEffect whose cleanup removes BOTH (StrictMode add→cleanup→add ⇒ one of each).
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
    const warn = (e: BeforeUnloadEvent) => {
      if (useAppStore.getState().scan.status !== "running") return;
      // Arm the browser-native confirm. Modern browsers ignore custom text and
      // show a generic prompt; preventDefault() is the canonical trigger and is
      // what sets event.defaultPrevented — the signal the QA scenario observes.
      // returnValue="" is the legacy belt-and-suspenders opt-in for older
      // engines; it does not affect defaultPrevented.
      e.preventDefault();
      e.returnValue = "";
    };
    const cancelOnLeave = (e: PageTransitionEvent) => {
      // Skip a bfcache stash — paused, not left (pageshow can restore the scan).
      if (e.persisted) return;
      const { status, taskId } = useAppStore.getState().scan;
      if (status !== "running" || taskId === null) return;
      // Body-less, best-effort. Optional-chained so a UA without sendBeacon
      // (incl. the jsdom test env) never throws inside the unload handler.
      navigator.sendBeacon?.(`/api/scan/${taskId}/cancel`);
    };
    window.addEventListener("beforeunload", warn);
    window.addEventListener("pagehide", cancelOnLeave);
    return () => {
      window.removeEventListener("beforeunload", warn);
      window.removeEventListener("pagehide", cancelOnLeave);
    };
  }, []);
}
