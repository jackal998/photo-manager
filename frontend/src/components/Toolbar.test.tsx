// Toolbar — the two NEW behaviours layout slice TB adds (#878), not the
// restyling: the counted bulk verbs (REPLY L5) and the danger CTA's count
// (#906). Everything the strip inherited from App.tsx is still covered by
// App.test.tsx, which renders the real App.

import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, beforeEach, vi } from "vitest";

import { Toolbar } from "./Toolbar";
import { useAppStore } from "@/store/useAppStore";
import { useI18nStore } from "@/i18n/useI18nStore";
import type { DecisionValue, FileRow, Group } from "@/api/types";
import {
  MAIN_BULK_LABEL,
  MAIN_BULK_VERB_DELETE,
  MAIN_BULK_VERB_KEEP,
  MAIN_BULK_VERB_SKIP,
  MAIN_DELETE_CTA,
  MAIN_FILTER_INPUT,
  MAIN_SCAN_BUTTON,
} from "@/testids";

function mkRow(
  basename: string,
  decision: DecisionValue = "",
  bytes = 1024
): FileRow {
  return {
    file_path: `/photos/${basename}`,
    basename,
    folder: "/photos",
    action: "REVIEW_DUPLICATE",
    user_decision: decision,
    is_locked: false,
    is_ref_winner: false,
    similarity: { kind: "near_dup", percent: 98 },
    score: null,
    file_size_bytes: bytes,
    pixel_width: null,
    pixel_height: null,
    shot_date: null,
    creation_date: null,
    phash: null,
    hamming_distance: 0,
    thumbnail_url: "",
  } as FileRow;
}

function seed(items: FileRow[], selectedPaths: string[] = []) {
  const groups: Group[] = [
    { group_number: 1, member_count: items.length, items },
  ];
  act(() => {
    useAppStore.setState({
      manifest: {
        path: "/m/test.db",
        groups,
        totalGroups: 1,
        totalFiles: items.length,
        loading: false,
        error: null,
      },
      selection: { selectedPaths, anchorPath: null, scrollToPath: null },
    });
  });
}

function renderToolbar() {
  return render(
    <Toolbar
      manifestPath={useAppStore.getState().manifest.path}
      locale="en"
      manifestInputValue=""
      onManifestInputChange={() => {}}
      onManifestOpen={() => {}}
      onScan={() => {}}
      onExecute={() => {}}
      onSetAction={() => {}}
      onSettings={() => {}}
      onSetLocale={() => {}}
    />
  );
}

describe("Toolbar bulk verbs (L5)", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
    useAppStore.setState({
      resultView: { ...useAppStore.getState().resultView, filterText: "" },
    });
  });

  it("puts the live selection COUNT in the label", () => {
    // «the count must be in the label (so "Delete" never means an unknown
    // number)» — the whole safety argument for having no confirm step.
    seed([mkRow("a.jpg"), mkRow("b.jpg"), mkRow("c.jpg")], [
      "/photos/a.jpg",
      "/photos/b.jpg",
      "/photos/c.jpg",
    ]);
    renderToolbar();
    expect(screen.getByTestId(MAIN_BULK_LABEL)).toHaveTextContent(
      "Set 3 selected:"
    );
  });

  it("reads 'Set selected:' with the verbs disabled at zero selection", () => {
    seed([mkRow("a.jpg")], []);
    renderToolbar();
    expect(screen.getByTestId(MAIN_BULK_LABEL)).toHaveTextContent("Set selected:");
    expect(screen.getByTestId(MAIN_BULK_VERB_KEEP)).toBeDisabled();
    expect(screen.getByTestId(MAIN_BULK_VERB_DELETE)).toBeDisabled();
    expect(screen.getByTestId(MAIN_BULK_VERB_SKIP)).toBeDisabled();
  });

  it("counts one selected row in the singular slot without breaking", () => {
    seed([mkRow("a.jpg")], ["/photos/a.jpg"]);
    renderToolbar();
    expect(screen.getByTestId(MAIN_BULK_LABEL)).toHaveTextContent(
      "Set 1 selected:"
    );
    expect(screen.getByTestId(MAIN_BULK_VERB_DELETE)).toBeEnabled();
  });

  it("dispatches the pressed verb's decision to the store", async () => {
    const spy = vi.fn().mockResolvedValue(undefined);
    seed([mkRow("a.jpg"), mkRow("b.jpg")], ["/photos/a.jpg"]);
    act(() => {
      useAppStore.setState({ applyBulkDecision: spy });
    });
    renderToolbar();
    await userEvent.click(screen.getByTestId(MAIN_BULK_VERB_SKIP));
    expect(spy).toHaveBeenCalledWith("ignore");
    await userEvent.click(screen.getByTestId(MAIN_BULK_VERB_KEEP));
    expect(spy).toHaveBeenCalledWith("");
  });

  it("renders the zh_TW label from the catalog, not the English fallback", () => {
    useI18nStore.setState({
      locale: "zh_TW",
      catalog: { "web.toolbar.bulk_set_n": "設定 {count} 個選取項目：" },
    });
    seed([mkRow("a.jpg"), mkRow("b.jpg")], ["/photos/a.jpg", "/photos/b.jpg"]);
    renderToolbar();
    expect(screen.getByTestId(MAIN_BULK_LABEL)).toHaveTextContent(
      "設定 2 個選取項目："
    );
  });
});

describe("Toolbar danger CTA (#906)", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
  });

  it("carries the delete-marked COUNT and opens the execute flow", async () => {
    const onExecute = vi.fn();
    seed([mkRow("a.jpg", "delete"), mkRow("b.jpg", "delete"), mkRow("c.jpg")]);
    render(
      <Toolbar
        manifestPath="/m/test.db"
        locale="en"
        manifestInputValue=""
        onManifestInputChange={() => {}}
        onManifestOpen={() => {}}
        onScan={() => {}}
        onExecute={onExecute}
        onSetAction={() => {}}
        onSettings={() => {}}
        onSetLocale={() => {}}
      />
    );
    const cta = screen.getByTestId(MAIN_DELETE_CTA);
    expect(cta).toHaveTextContent("Delete 2 files…");
    expect(cta).toBeEnabled();
    await userEvent.click(cta);
    expect(onExecute).toHaveBeenCalledTimes(1);
  });

  it("stays visible but disabled at zero, reading 'Delete 0 files…'", () => {
    // «Zero marked → disabled, label reads "Delete 0 files…" rather than
    // disappearing, so the button never moves.» A destructive control that
    // appears under the cursor gets pressed by a click aimed at something else.
    seed([mkRow("a.jpg"), mkRow("b.jpg")]);
    renderToolbar();
    const cta = screen.getByTestId(MAIN_DELETE_CTA);
    expect(cta).toBeVisible();
    expect(cta).toBeDisabled();
    expect(cta).toHaveTextContent("Delete 0 files…");
  });

  it("uses the SINGULAR file word at one marked row", () => {
    seed([mkRow("a.jpg", "delete"), mkRow("b.jpg")]);
    renderToolbar();
    expect(screen.getByTestId(MAIN_DELETE_CTA)).toHaveTextContent(
      "Delete 1 file…"
    );
  });
});

describe("Toolbar filter", () => {
  beforeEach(() => {
    useI18nStore.setState({ locale: "en", catalog: {} });
    act(() => {
      useAppStore.setState({
        resultView: { ...useAppStore.getState().resultView, filterText: "" },
      });
    });
  });

  it("writes what the user types into the view-only store field", async () => {
    seed([mkRow("a.jpg")]);
    renderToolbar();
    await userEvent.type(screen.getByTestId(MAIN_FILTER_INPUT), "q95");
    expect(useAppStore.getState().resultView.filterText).toBe("q95");
  });

  it("leaves decisions and the manifest untouched — it is a VIEW control", async () => {
    seed([mkRow("a.jpg"), mkRow("b.jpg")]);
    const before = JSON.stringify(useAppStore.getState().manifest.groups);
    renderToolbar();
    await userEvent.type(screen.getByTestId(MAIN_FILTER_INPUT), "a");
    expect(JSON.stringify(useAppStore.getState().manifest.groups)).toBe(before);
  });
});

describe("Toolbar primary", () => {
  it("has EXACTLY ONE warm-filled control — the Scan button", () => {
    // REPLY: «One filled warm button in the toolbar, so "where do I start" has
    // exactly one answer». jsdom cannot resolve Tailwind to a colour, so the
    // check is on the utility class; s76 asserts the computed rgb in a browser.
    useI18nStore.setState({ locale: "en", catalog: {} });
    seed([mkRow("a.jpg", "delete")]);
    const { container } = renderToolbar();
    const warm = container.querySelectorAll("header button.bg-warm");
    expect(warm).toHaveLength(1);
    expect(warm[0]).toBe(screen.getByTestId(MAIN_SCAN_BUTTON));
  });
});
