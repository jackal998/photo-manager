// Unit cover for useDecisionShortcuts — the bare 'd' / 'k' decision shortcuts.
//
// Non-vacuity is the whole point: an unconditional listener would also pass an
// "acts when a row is selected" assertion, so we assert BOTH that it dispatches
// setDecisions for d/k with a live selection AND that it is inert for every
// guard the Qt NoModifier + focus contract implies — no selection, any modifier
// held, an editable-field target, an open modal layer, an unrelated key, and
// after unmount. setDecisions is replaced with a spy so the keydown path is
// observed without a network PATCH; the hook's own preventDefault is read off
// the dispatched (cancelable) event — the test never preventDefaults itself.

import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDecisionShortcuts } from "./useDecisionShortcuts";
import { useAppStore } from "../store/useAppStore";

type SetDecisions = ReturnType<typeof useAppStore.getState>["setDecisions"];

let decideSpy: ReturnType<typeof vi.fn>;
let originalSetDecisions: SetDecisions;

function seedSelection(paths: string[]): void {
  const prev = useAppStore.getState().selection;
  useAppStore.setState({ selection: { ...prev, selectedPaths: [...paths] } });
}

/** Dispatch a keydown that bubbles to the document listener. `target`, when
 *  given, is the element the event originates from (to exercise the field
 *  guard); otherwise it is dispatched on document. Returns defaultPrevented. */
function pressKey(
  key: string,
  opts: { ctrl?: boolean; meta?: boolean; alt?: boolean; shift?: boolean; target?: HTMLElement } = {}
): boolean {
  const e = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ctrlKey: opts.ctrl ?? false,
    metaKey: opts.meta ?? false,
    altKey: opts.alt ?? false,
    shiftKey: opts.shift ?? false,
  });
  (opts.target ?? document).dispatchEvent(e);
  return e.defaultPrevented;
}

beforeEach(() => {
  decideSpy = vi.fn();
  originalSetDecisions = useAppStore.getState().setDecisions;
  useAppStore.setState({ setDecisions: decideSpy as unknown as SetDecisions });
  seedSelection([]);
});

afterEach(() => {
  useAppStore.setState({ setDecisions: originalSetDecisions });
  seedSelection([]);
  document.body.innerHTML = "";
});

describe("useDecisionShortcuts — d/k decision shortcuts (#615)", () => {
  it("registers a document keydown listener", () => {
    const addSpy = vi.spyOn(document, "addEventListener");
    renderHook(() => useDecisionShortcuts());
    expect(addSpy).toHaveBeenCalledWith("keydown", expect.any(Function));
    addSpy.mockRestore();
  });

  it("sets 'delete' on the selection when 'd' is pressed (and preventDefaults)", () => {
    renderHook(() => useDecisionShortcuts());
    seedSelection(["/photos/a.jpg", "/photos/b.jpg"]);
    const prevented = pressKey("d");
    expect(decideSpy).toHaveBeenCalledTimes(1);
    expect(decideSpy).toHaveBeenCalledWith(["/photos/a.jpg", "/photos/b.jpg"], "delete");
    expect(prevented).toBe(true);
  });

  it("clears the decision ('') on the selection when 'k' is pressed", () => {
    renderHook(() => useDecisionShortcuts());
    seedSelection(["/photos/a.jpg"]);
    pressKey("k");
    expect(decideSpy).toHaveBeenCalledTimes(1);
    expect(decideSpy).toHaveBeenCalledWith(["/photos/a.jpg"], "");
  });

  it("does NOT fire when the selection is empty", () => {
    renderHook(() => useDecisionShortcuts());
    seedSelection([]);
    pressKey("d");
    expect(decideSpy).not.toHaveBeenCalled();
  });

  it.each([
    ["ctrl", { ctrl: true }],
    ["meta", { meta: true }],
    ["alt", { alt: true }],
    ["shift", { shift: true }],
  ])("does NOT fire when '%s' modifier is held (Qt NoModifier guard)", (_label, mods) => {
    renderHook(() => useDecisionShortcuts());
    seedSelection(["/photos/a.jpg"]);
    pressKey("d", mods);
    expect(decideSpy).not.toHaveBeenCalled();
  });

  it("does NOT fire when the keystroke targets an editable field", () => {
    renderHook(() => useDecisionShortcuts());
    seedSelection(["/photos/a.jpg"]);
    const input = document.createElement("input");
    document.body.appendChild(input);
    pressKey("d", { target: input });
    expect(decideSpy).not.toHaveBeenCalled();
  });

  it("does NOT fire while a modal layer (role=dialog) is open", () => {
    renderHook(() => useDecisionShortcuts());
    seedSelection(["/photos/a.jpg"]);
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    document.body.appendChild(dialog);
    pressKey("d");
    expect(decideSpy).not.toHaveBeenCalled();
  });

  it("does NOT fire while a menu layer (role=menu) is open", () => {
    renderHook(() => useDecisionShortcuts());
    seedSelection(["/photos/a.jpg"]);
    const menu = document.createElement("div");
    menu.setAttribute("role", "menu");
    document.body.appendChild(menu);
    pressKey("d");
    expect(decideSpy).not.toHaveBeenCalled();
  });

  it("does NOT fire for an unrelated key", () => {
    renderHook(() => useDecisionShortcuts());
    seedSelection(["/photos/a.jpg"]);
    pressKey("x");
    expect(decideSpy).not.toHaveBeenCalled();
  });

  it("removes its listener on unmount (no late fire)", () => {
    const { unmount } = renderHook(() => useDecisionShortcuts());
    seedSelection(["/photos/a.jpg"]);
    unmount();
    pressKey("d");
    expect(decideSpy).not.toHaveBeenCalled();
  });
});
