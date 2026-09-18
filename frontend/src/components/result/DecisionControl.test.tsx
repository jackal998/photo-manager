// Daylight decision chips (#878 slice b).
//
// jsdom never runs the Tailwind pipeline, so these assert the CLASS the
// segment carries, not the colour it resolves to — the computed colours are
// pinned live by qa/web/scenarios/s74_daylight_theme.py. What a class-level
// test CAN prove, and what the scenario cannot cheaply prove for all three
// states at once, is the mapping: exactly one segment is filled, it is the
// one matching the stored decision, and it is filled with THAT decision's
// chip rather than another's.

import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { DecisionControl } from "./DecisionControl";
import { useI18nStore } from "@/i18n/useI18nStore";
import type { DecisionValue } from "@/api/types";

const TESTID = "row-decision-1-dup.jpg";

function renderControl(value: DecisionValue) {
  render(
    <DecisionControl value={value} onChange={vi.fn()} data-testid={TESTID} />
  );
  return {
    none: screen.getByTestId(`${TESTID}-none`),
    delete: screen.getByTestId(`${TESTID}-delete`),
    ignore: screen.getByTestId(`${TESTID}-ignore`),
  };
}

describe("DecisionControl chips", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
  });

  // Copy audit R5 — only the group's accessible NAME moves to the catalog
  // here. The three option labels stay literal on purpose: the decision
  // vocabulary (R3/R4) is still waiting on the owner's decision, and
  // renaming them now would pre-empt it.
  it("takes the control's accessible name from the catalog", () => {
    useI18nStore.setState({
      locale: "zh_TW",
      catalog: { "web.column.decision": "決策" },
    });
    render(<DecisionControl value="" onChange={vi.fn()} data-testid={TESTID} />);
    expect(screen.getByTestId(TESTID)).toHaveAttribute("aria-label", "決策");
  });

  it("fills the Keep segment with the KEEP chip when nothing is staged", () => {
    // "" is keep under the #584 decision model — the row survives Execute —
    // so the active Keep segment is green, not the faint undecided grey the
    // inactive segments wear.
    const seg = renderControl("");

    expect(seg.none.className).toContain("bg-dec-keep-bg");
    expect(seg.none.className).toContain("text-dec-keep-ink");
    expect(seg.delete.className).toContain("bg-dec-undecided-bg");
    expect(seg.ignore.className).toContain("bg-dec-undecided-bg");
  });

  it("fills the Delete segment with the DELETE chip when delete is staged", () => {
    const seg = renderControl("delete");

    expect(seg.delete.className).toContain("bg-dec-delete-bg");
    expect(seg.delete.className).toContain("text-dec-delete-ink");
    expect(seg.none.className).toContain("bg-dec-undecided-bg");
    expect(seg.none.className).not.toContain("bg-dec-keep-bg");
  });

  it("fills the Skip segment with the REMOVE chip when ignore is staged", () => {
    const seg = renderControl("ignore");

    expect(seg.ignore.className).toContain("bg-dec-remove-bg");
    expect(seg.ignore.className).toContain("text-dec-remove-ink");
    // A removal must not borrow the delete chip: the whole point of #584 is
    // that "remove from the list" and "delete the file" are different acts.
    expect(seg.ignore.className).not.toContain("bg-dec-delete-bg");
    expect(seg.delete.className).toContain("bg-dec-undecided-bg");
  });

  it("keeps the three labels and aria-pressed states the store drives", () => {
    const seg = renderControl("delete");

    expect(seg.none).toHaveTextContent("Keep");
    expect(seg.delete).toHaveTextContent("Delete");
    expect(seg.ignore).toHaveTextContent("Skip");
    expect(seg.delete).toHaveAttribute("aria-pressed", "true");
    expect(seg.none).toHaveAttribute("aria-pressed", "false");
  });
});
