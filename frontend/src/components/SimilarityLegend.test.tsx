// SimilarityLegend — the five-state key pinned under the preview pane
// (layout slice E, audit row P8).
//
// What is worth testing here is COMPOSITION, not colour: that all five states
// are present (a legend missing one state is worse than none — the reader
// concludes the missing pill means something else), that each badge carries
// the state's own label text, and that the meanings are translated. The
// computed colours, the 74px min-width and the footer's pinned position are
// `qa/web/scenarios/s74_daylight_theme.py`: in jsdom every one of those is a
// class string that survives its token being deleted.

import { act, render, screen, within } from "@testing-library/react";
import { describe, it, expect, beforeEach } from "vitest";

import { SimilarityLegend } from "./SimilarityLegend";
import { useI18nStore } from "@/i18n/useI18nStore";
import { PREVIEW_LEGEND } from "@/testids";

function legendRows(): HTMLElement[] {
  return Array.from(
    screen.getByTestId(PREVIEW_LEGEND).querySelectorAll("[data-legend-state]")
  );
}

describe("SimilarityLegend", () => {
  beforeEach(() => {
    act(() => {
      useI18nStore.setState({ locale: "en", catalog: {} });
    });
  });

  it("renders one badge per similarity state, all five, in prototype order", () => {
    render(<SimilarityLegend />);
    expect(
      legendRows().map((el) => el.getAttribute("data-legend-state"))
    ).toEqual(["ref", "exact", "near", "indirect", "none"]);
  });

  it("labels each badge with what the row cell would print for that state", () => {
    // The labels come from `similarityLabel()`, the same function the row and
    // the pane's own badge call — a legend with its own hardcoded pills would
    // keep explaining a vocabulary the table had already stopped using.
    render(<SimilarityLegend />);
    const texts = legendRows().map((el) => el.textContent?.trim());
    expect(texts).toEqual(["★Ref", "100%", "96%", "★ 95%", "—"]);
  });

  it("gives every state a meaning phrase beside its badge", () => {
    render(<SimilarityLegend />);
    const legend = screen.getByTestId(PREVIEW_LEGEND);
    expect(legend).toHaveTextContent("Similarity legend");
    for (const phrase of [
      "chosen keeper",
      "exact duplicate",
      "near match",
      "linked indirectly",
      "video / no image",
    ]) {
      expect(legend).toHaveTextContent(phrase);
    }
  });

  it("keeps the grayscale cue distinct for every pair of states", () => {
    // The same invariant `similarityBadge.test.ts` pins on the spec table,
    // asserted on what the legend actually MOUNTS: five pills whose border
    // style and weight repeat would be five rows of colour-only difference,
    // which is the defect the badge exists to fix — shown in the one place
    // that claims to explain it.
    render(<SimilarityLegend />);
    const cues = legendRows().map((el) => {
      const cls = el.className;
      const border = ["border-solid", "border-dashed", "border-dotted"].find(
        (c) => cls.includes(c)
      );
      const weight = ["font-normal", "font-medium", "font-semibold", "font-bold"].find(
        (c) => cls.includes(c)
      );
      return `${border}/${weight}`;
    });
    expect(new Set(cues).size).toBe(5);
  });

  it("gives every badge the legend's 74px min-width", () => {
    // The one place the badge takes a min-width (REPLY P8): five pills read as
    // a key only when their edges line up. In a ROW the badge sizes to content,
    // so this class must not migrate into the shared spec.
    render(<SimilarityLegend />);
    for (const el of legendRows()) {
      expect(el.className).toContain("min-w-[74px]");
    }
  });

  it("translates the title and every meaning into zh_TW", () => {
    act(() => {
      useI18nStore.setState({
        locale: "zh_TW",
        catalog: {
          "web.preview.legend_title": "相似度圖例",
          "web.preview.legend_ref": "選定保留的檔案",
          "web.preview.legend_exact": "完全相同",
          "web.preview.legend_near": "外觀相近",
          "web.preview.legend_indirect": "間接關聯",
          "web.preview.legend_none": "影片／無影像",
          "web.format.similarity_ref": "參考",
        },
      });
    });
    render(<SimilarityLegend />);
    const legend = screen.getByTestId(PREVIEW_LEGEND);
    expect(legend).toHaveTextContent("相似度圖例");
    expect(legend).toHaveTextContent("選定保留的檔案");
    expect(legend).toHaveTextContent("影片／無影像");
    // The Ref badge's own word is translated too — it is the one natural
    // -language label among the badge's symbol outputs.
    expect(within(legend).getByText("參考")).toBeInTheDocument();
    expect(legend.textContent).not.toContain("chosen keeper");
  });
});
