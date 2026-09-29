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

  // Design F1 (#922): the unlocked padlock is a STATE, so it wears `ink-muted`
  // #6b6358 at rest — not `hairline` #a89f8f (2.58:1 on panel), which carries
  // no state anywhere. classList, not a className substring: the old markup's
  // `hover:text-ink-muted` contains "text-ink-muted" and would pass a
  // substring check while the resting ink was still the hairline.
  it("renders the unlocked padlock in ink-muted, not the hairline", () => {
    const { el } = renderToggle(false);
    expect(el.classList.contains("text-ink-muted")).toBe(true);
    expect(el.classList.contains("text-ink-hairline")).toBe(false);
    expect(el.className).not.toContain("text-warm");
  });

  it("renders the warm accent when locked", () => {
    const { el } = renderToggle(true);
    expect(el.className).toContain("text-warm");
    expect(el.classList.contains("text-ink-muted")).toBe(false);
  });

  // REPLY §"Padlock (R17)": «28px, not 16px. A 16px target is below a
  // comfortable pointer target and this is the control that protects a file
  // from bulk operations — it is the last thing that should be fiddly. The
  // glyph stays 14px; the growth is all hit area.»
  it("gives the padlock a 28px hit target with a 14px glyph", () => {
    const { el } = renderToggle(false);
    expect(el.className).toContain("h-[28px]");
    expect(el.className).toContain("w-[28px]");
    expect(el.querySelector("svg")?.getAttribute("class")).toContain("h-[14px]");
  });

  it("shrinks the hit target to 24px at the compact density", () => {
    render(
      <LockToggle
        checked={false}
        onChange={vi.fn()}
        density="compact"
        data-testid={TESTID}
      />
    );
    const el = screen.getByTestId(TESTID);
    expect(el.className).toContain("h-[24px]");
    // The glyph does NOT shrink with the box — the hit area is what varies.
    expect(el.querySelector("svg")?.getAttribute("class")).toContain("h-[14px]");
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
