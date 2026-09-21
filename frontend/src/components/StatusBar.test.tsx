// StatusBar — the #906 figures and the density switch (#878 layout slice TB).
// The error lines it inherited from App.tsx keep their coverage in App.test.tsx,
// which renders the real App.

import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, beforeEach } from "vitest";

import { StatusBar } from "./StatusBar";
import { useAppStore } from "@/store/useAppStore";
import { useI18nStore } from "@/i18n/useI18nStore";
import { DEFAULT_DENSITY } from "@/lib/density";
import type { DecisionValue, FileRow, Group } from "@/api/types";
import {
  MAIN_DENSITY_COMFORTABLE,
  MAIN_DENSITY_COMPACT,
  MAIN_STATUS_BAR,
  MAIN_STATUS_DELETE_COUNT,
  MAIN_STATUS_RECLAIM,
  MAIN_STATUS_STRIP,
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

function seed(items: FileRow[], path: string | null = "/m/test.db") {
  const groups: Group[] = [
    { group_number: 1, member_count: items.length, items },
  ];
  act(() => {
    useAppStore.setState({
      manifest: {
        path,
        groups: path === null ? [] : groups,
        totalGroups: path === null ? 0 : 1,
        totalFiles: path === null ? 0 : items.length,
        loading: false,
        error: null,
      },
      resultView: { ...useAppStore.getState().resultView, density: DEFAULT_DENSITY },
    });
  });
}

describe("StatusBar figures (#906)", () => {
  beforeEach(() => {
    localStorage.clear();
    useI18nStore.setState({ locale: "en", catalog: {} });
  });

  it("shows the delete count and the reclaimable byte sum", () => {
    seed([
      mkRow("a.jpg", "delete", 2_000_000),
      mkRow("b.jpg", "delete", 1_000_000),
      mkRow("c.jpg", "", 9_000_000),
    ]);
    render(<StatusBar statusText="1 group · 3 files" />);
    expect(screen.getByTestId(MAIN_STATUS_DELETE_COUNT)).toHaveTextContent(
      "2 marked to delete"
    );
    // 3,000,000 B through the same binary formatBytes the rest of the UI uses
    // (3e6 / 1024² = 2.86) — the figure a user weighs "is this session worth
    // continuing" against, and it must agree with every other size on screen.
    expect(screen.getByTestId(MAIN_STATUS_RECLAIM)).toHaveTextContent("2.9 MB");
  });

  it("keeps the summary <p> as its own element, unchanged", () => {
    // s74 and others read the footer surface through
    // `[data-testid=main-status-bar].closest('footer')`, and the summary text
    // must not absorb the two new figures.
    seed([mkRow("a.jpg", "delete")]);
    render(<StatusBar statusText="1 group · 1 file" />);
    const p = screen.getByTestId(MAIN_STATUS_BAR);
    expect(p.textContent).toBe("1 group · 1 file");
    expect(p.closest("footer")).not.toBeNull();
    // The 30px strip is a separate box, so the error lines below it never
    // squeeze it (they are siblings, not children).
    expect(p.closest(`[data-testid="${MAIN_STATUS_STRIP}"]`)).not.toBeNull();
  });

  it("hides both figures until a manifest is loaded", () => {
    seed([], null);
    render(<StatusBar statusText="Ready" />);
    expect(screen.queryByTestId(MAIN_STATUS_DELETE_COUNT)).toBeNull();
    expect(screen.queryByTestId(MAIN_STATUS_RECLAIM)).toBeNull();
  });

  it("shows zero rather than disappearing once a manifest IS loaded", () => {
    // Same "never moves" rule as the danger CTA: a counter that only exists
    // when non-zero cannot answer "did that write land".
    seed([mkRow("a.jpg"), mkRow("b.jpg")]);
    render(<StatusBar statusText="1 group · 2 files" />);
    expect(screen.getByTestId(MAIN_STATUS_DELETE_COUNT)).toHaveTextContent(
      "0 marked to delete"
    );
  });
});

describe("StatusBar density toggle", () => {
  beforeEach(() => {
    localStorage.clear();
    useI18nStore.setState({ locale: "en", catalog: {} });
    seed([mkRow("a.jpg")]);
  });

  it("marks the current density pressed and the other not", () => {
    render(<StatusBar statusText="Ready" />);
    expect(screen.getByTestId(MAIN_DENSITY_COMFORTABLE)).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(screen.getByTestId(MAIN_DENSITY_COMPACT)).toHaveAttribute(
      "aria-pressed",
      "false"
    );
  });

  it("writes the preference to the store AND to localStorage", async () => {
    // Persistence is the point of the preference: slice R hydrates it at store
    // creation, so a toggle that only changed memory would reset on reload.
    render(<StatusBar statusText="Ready" />);
    await userEvent.click(screen.getByTestId(MAIN_DENSITY_COMPACT));
    expect(useAppStore.getState().resultView.density).toBe("compact");
    expect(localStorage.getItem("density")).toBe("compact");

    await userEvent.click(screen.getByTestId(MAIN_DENSITY_COMFORTABLE));
    expect(useAppStore.getState().resultView.density).toBe("comfortable");
    expect(localStorage.getItem("density")).toBe("comfortable");
  });
});
