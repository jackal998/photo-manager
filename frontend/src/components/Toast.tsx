// The single-slot undo toast (#878 layout slice G, open questions Q5).
//
// Q5 refused a confirm dialog in front of "Keep best · delete rest" — «the
// button does not delete anything … A confirm dialog in front of a reversible,
// non-destructive action is the classic way to teach people to dismiss dialogs
// without reading — which then costs you at Execute, where the dialog matters.
// Give it an undo toast instead ("Group 12 · 4 files marked for deletion ·
// Undo", ~6 s)». So this component is the entire safety net for a bulk write,
// and three of its details are load-bearing rather than polish:
//
//   * **`role="status"`**, not `alert`: the write is not an error, and `alert`
//     interrupts a screen-reader user mid-sentence for something benign.
//   * **The timer pauses on hover / focus.** Six seconds is enough to read a
//     sentence, not enough to read it, decide, and aim — and the one user who
//     is still moving the mouse toward Undo is precisely the one who wants it.
//   * **Undo is a real `<button>`**, reachable by keyboard, because the people
//     most likely to trigger a bulk verb by accident are the ones driving the
//     list from the keyboard.
//
// There is no toast QUEUE: a second keep-best replaces this one (see
// `KeepBestToast` in store/types.ts for why).

import { useCallback, useEffect, useRef, useState } from "react";
import { useT } from "@/i18n/useT";
import { useAppStore } from "@/store/useAppStore";
import { DECISION_VOCAB } from "@/components/result/DecisionControl";
import { MAIN_TOAST, MAIN_TOAST_UNDO } from "@/testids";

/** Q5: «~6 s». */
export const TOAST_TIMEOUT_MS = 6000;

export function Toast() {
  const t = useT();
  const toast = useAppStore((s) => s.toast);
  const dismissToast = useAppStore((s) => s.dismissToast);
  const undoKeepBest = useAppStore((s) => s.undoKeepBest);

  const [paused, setPaused] = useState(false);
  // Milliseconds still owed when the timer was last paused. Tracked rather
  // than restarting the full 6 s on mouse-out: a user who hovered for four
  // seconds reading the sentence should get the remaining two, not a fresh
  // six that makes the toast outstay every other one.
  const remainingRef = useRef(TOAST_TIMEOUT_MS);
  const startedAtRef = useRef(0);

  const toastId = toast?.id ?? null;

  // A new toast id resets the budget — that is what makes "newest replaces
  // previous" restart the clock instead of inheriting its predecessor's tail.
  useEffect(() => {
    remainingRef.current = TOAST_TIMEOUT_MS;
    setPaused(false);
  }, [toastId]);

  useEffect(() => {
    if (toastId === null || paused) return;
    startedAtRef.current = Date.now();
    const handle = window.setTimeout(() => {
      dismissToast();
    }, remainingRef.current);
    return () => {
      window.clearTimeout(handle);
      remainingRef.current = Math.max(
        0,
        remainingRef.current - (Date.now() - startedAtRef.current)
      );
    };
  }, [toastId, paused, dismissToast]);

  const pause = useCallback(() => setPaused(true), []);
  const resume = useCallback(() => setPaused(false), []);

  if (toast === null) return null;

  const fileWord =
    toast.affectedCount === 1
      ? t("web.toast.file_singular", "file")
      : t("web.toast.file_plural", "files");
  // Two sentences, one slot. Keep-best names its group and always writes
  // `delete`; a toolbar verb (slice TB) acts on a selection that may span
  // groups and writes whichever of the three decisions was pressed, so its
  // sentence names the VERB instead — the same three words the row control
  // and the right-click menu use, from the one decision vocabulary.
  const message =
    toast.kind === "bulk-decision"
      ? t("web.toast.bulk_decision", "{count} {fileWord} set to {verb}", {
          count: toast.affectedCount,
          fileWord,
          verb: t(
            DECISION_VOCAB[toast.decision ?? ""].key,
            DECISION_VOCAB[toast.decision ?? ""].fallback
          ),
        })
      : t(
          "web.toast.keep_best",
          "Group {n} · {count} {fileWord} marked for deletion",
          { n: toast.groupNumber ?? 0, count: toast.affectedCount, fileWord }
        );
  const lockedNote =
    toast.lockedCount > 0
      ? t(
          toast.lockedCount === 1
            ? "web.toast.locked_unchanged_singular"
            : "web.toast.locked_unchanged_plural",
          toast.lockedCount === 1
            ? "{count} locked file unchanged"
            : "{count} locked files unchanged",
          { count: toast.lockedCount }
        )
      : null;

  return (
    // Bottom-centre, ABOVE the 30px status strip rather than over it — the
    // status bar is where the delete counter and the two error lines live, and
    // a toast that covers them hides exactly the numbers this action changed.
    // The wrapper spans the full viewport width so the box can centre in it,
    // which would make it a full-width invisible click shield over the tree.
    // `pointer-events-none` here and `pointer-events-auto` on the BOX is what
    // keeps the shield from existing — a pair that must move together, and
    // whose breakage is silent (nothing throws; clicks just stop landing).
    // s75 hit-tests both halves.
    <div className="pointer-events-none fixed inset-x-0 bottom-12 z-50 flex justify-center px-4">
      <div
        data-testid={MAIN_TOAST}
        role="status"
        onMouseEnter={pause}
        onMouseLeave={resume}
        onFocus={pause}
        onBlur={resume}
        className={[
          "pointer-events-auto flex max-w-[min(90vw,42rem)] items-center gap-2",
          "rounded-[10px] border border-hairline-input bg-panel px-4 py-2",
          "text-[12.5px] text-ink shadow-lg",
        ].join(" ")}
      >
        <span className="truncate">{message}</span>
        {lockedNote !== null && (
          <>
            <span className="text-ink-hairline">·</span>
            <span className="truncate text-ink-muted">{lockedNote}</span>
          </>
        )}
        <span className="text-ink-hairline">·</span>
        <button
          type="button"
          data-testid={MAIN_TOAST_UNDO}
          disabled={toast.undoing}
          onClick={() => void undoKeepBest()}
          className={[
            "flex-shrink-0 rounded-[6px] px-2 py-0.5 font-semibold text-warm",
            "hover:bg-panel-hover disabled:opacity-50",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warm",
          ].join(" ")}
        >
          {t("web.toast.undo", "Undo")}
        </button>
      </div>
    </div>
  );
}
