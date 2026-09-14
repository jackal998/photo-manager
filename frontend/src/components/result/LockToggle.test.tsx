// Clickable padlock (#878 slice b).
//
// The visual claim (faint vs solid) is a class here and a computed colour in
// qa/web/scenarios/s74_daylight_theme.py. What these pin is the part a
// scenario would not notice going wrong: the toggle still dispatches the same
// lock action, still reports its state, and still emits the `data-state`
// attribute the Radix checkbox it replaces emitted — several existing tests
// and any scenario built on them read that attribute.

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { LockToggle } from "./LockToggle";
import { useI18nStore } from "@/i18n/useI18nStore";

const TESTID = "row-lock-1-dup.jpg";

function renderToggle(checked: boolean, onChange = vi.fn()) {
  render(
    <LockToggle checked={checked} onChange={onChange} data-testid={TESTID} />
  );
  return { el: screen.getByTestId(TESTID), onChange };
}

describe("LockToggle", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
  });

  // Copy audit R9 — the accessible name was a hardcoded English literal, so a
  // zh_TW screen-reader user still heard English on every row.
  it("takes its accessible name from the catalog, not a literal", () => {
    useI18nStore.setState({
      locale: "zh_TW",
      catalog: { "web.column.lock": "鎖定" },
    });
    renderToggle(false);
    expect(screen.getByTestId(TESTID)).toHaveAttribute("aria-label", "鎖定");
  });

  it("renders faint when unlocked and solid accent when locked", () => {
    const { el } = renderToggle(false);
    expect(el.className).toContain("text-ink-faint");
    expect(el.className).not.toContain("text-warm");
  });

  it("renders the warm accent when locked", () => {
    const { el } = renderToggle(true);
    expect(el.className).toContain("text-warm");
    expect(el.className).not.toContain("text-ink-faint");
  });

  it("reports a locked row through aria-pressed and data-state", () => {
    const { el } = renderToggle(true);
    expect(el).toHaveAttribute("aria-pressed", "true");
    // `data-state` is what the Radix checkbox this replaces emitted and what
    // ResultTree.test.tsx still asserts — dropping it would break callers
    // that never mention LockToggle.
    expect(el).toHaveAttribute("data-state", "checked");
  });

  it("reports an unlocked row through aria-pressed and data-state", () => {
    const { el } = renderToggle(false);
    expect(el).toHaveAttribute("aria-pressed", "false");
    expect(el).toHaveAttribute("data-state", "unchecked");
  });

  it("dispatches the inverse of the current state on click", () => {
    const { el, onChange } = renderToggle(false);
    fireEvent.click(el);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("unlocks a locked row on click", () => {
    const { el, onChange } = renderToggle(true);
    fireEvent.click(el);
    expect(onChange).toHaveBeenCalledWith(false);
  });
});
