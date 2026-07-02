// Tests for PreviewPane and FullResViewer.
// Seeds the Zustand store directly (same pattern as ResultTree.test.tsx).

import { act, render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { PreviewPane } from "./PreviewPane";
import { FullResViewer } from "./FullResViewer";
import { useAppStore } from "@/store/useAppStore";
import { PREVIEW_PANE, PREVIEW_SINGLE_IMAGE, PREVIEW_INFO, FULLRES_DIALOG, FULLRES_IMAGE } from "@/testids";
import type { Group } from "@/api/types";

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

const FILE_PATH = "/photos/sunset.jpg";
const FILE_PATH_2 = "/photos/mountain.jpg";

const testRow = {
  file_path: FILE_PATH,
  basename: "sunset.jpg",
  folder: "/photos",
  action: "keep",
  user_decision: "" as const,
  is_locked: false,
  is_ref_winner: true,
  similarity: { kind: "ref" as const, percent: null },
  score: 88.5,
  file_size_bytes: 2_097_152,
  pixel_width: 3000,
  pixel_height: 2000,
  shot_date: "2024-06-01",
  creation_date: "2024-06-01",
  phash: "aabbcc001122",
  hamming_distance: null,
  thumbnail_url: `/api/image?path=${encodeURIComponent(FILE_PATH)}&size=512`,
};

const testRow2 = {
  file_path: FILE_PATH_2,
  basename: "mountain.jpg",
  folder: "/photos",
  action: "delete",
  user_decision: "" as const,
  is_locked: false,
  is_ref_winner: false,
  similarity: { kind: "passenger" as const, percent: 85 },
  score: 72.0,
  file_size_bytes: 1_048_576,
  pixel_width: 1920,
  pixel_height: 1080,
  shot_date: null,
  creation_date: null,
  phash: null,
  hamming_distance: 4,
  thumbnail_url: `/api/image?path=${encodeURIComponent(FILE_PATH_2)}&size=512`,
};

const TEST_GROUPS: Group[] = [
  {
    group_number: 1,
    member_count: 2,
    items: [testRow, testRow2],
  },
];

// ---------------------------------------------------------------------------
// Store helpers
// ---------------------------------------------------------------------------

function seedManifest() {
  useAppStore.setState({
    manifest: {
      path: "/manifests/test.db",
      groups: TEST_GROUPS,
      totalGroups: 1,
      totalFiles: 2,
      loading: false,
      error: null,
    },
  });
}

function resetStore() {
  useAppStore.setState({
    manifest: {
      path: null,
      groups: [],
      totalGroups: 0,
      totalFiles: 0,
      loading: false,
      error: null,
    },
    preview: {
      selectedFilePath: null,
      fullResPath: null,
      selectedGroupId: null,
    },
  });
}

// ---------------------------------------------------------------------------
// PreviewPane tests
// ---------------------------------------------------------------------------

describe("PreviewPane", () => {
  beforeEach(() => {
    resetStore();
  });

  it("renders the pane container with correct testid", () => {
    render(<PreviewPane />);
    expect(screen.getByTestId(PREVIEW_PANE)).toBeInTheDocument();
  });

  it("shows empty state when no file is selected", () => {
    render(<PreviewPane />);
    expect(screen.getByText(/select a file to preview/i)).toBeInTheDocument();
    // Image and info panel must NOT be present in empty state.
    expect(screen.queryByTestId(PREVIEW_SINGLE_IMAGE)).not.toBeInTheDocument();
    expect(screen.queryByTestId(PREVIEW_INFO)).not.toBeInTheDocument();
  });

  it("shows empty state when selectedFilePath is set but file not in manifest", () => {
    // File path set but manifest is empty — no groups to find the row in.
    useAppStore.setState({
      preview: { selectedFilePath: "/photos/ghost.jpg", fullResPath: null, selectedGroupId: null },
    });
    render(<PreviewPane />);
    expect(screen.getByText(/select a file to preview/i)).toBeInTheDocument();
  });

  describe("with a selected file in the manifest", () => {
    beforeEach(() => {
      seedManifest();
      useAppStore.setState({
        preview: { selectedFilePath: FILE_PATH, fullResPath: null, selectedGroupId: null },
      });
    });

    it("renders the thumbnail image with correct testid", () => {
      render(<PreviewPane />);
      const img = screen.getByTestId(PREVIEW_SINGLE_IMAGE);
      expect(img).toBeInTheDocument();
      expect(img).toHaveAttribute("src");
      // URL must contain the path and size=512.
      const src = img.getAttribute("src") ?? "";
      expect(src).toContain("size=512");
      expect(src).toContain(encodeURIComponent(FILE_PATH));
    });

    it("renders the info panel with correct testid", () => {
      render(<PreviewPane />);
      expect(screen.getByTestId(PREVIEW_INFO)).toBeInTheDocument();
    });

    it("info panel shows filename", () => {
      render(<PreviewPane />);
      expect(screen.getByTestId(PREVIEW_INFO)).toHaveTextContent("sunset.jpg");
    });

    it("info panel shows folder path", () => {
      render(<PreviewPane />);
      expect(screen.getByTestId(PREVIEW_INFO)).toHaveTextContent("/photos");
    });

    it("info panel shows formatted file size", () => {
      render(<PreviewPane />);
      // 2_097_152 bytes = 2.0 MB
      expect(screen.getByTestId(PREVIEW_INFO)).toHaveTextContent("MB");
    });

    it("info panel shows formatted score", () => {
      render(<PreviewPane />);
      expect(screen.getByTestId(PREVIEW_INFO)).toHaveTextContent("88.5");
    });

    it("info panel shows pixel dimensions", () => {
      render(<PreviewPane />);
      expect(screen.getByTestId(PREVIEW_INFO)).toHaveTextContent("3000");
      expect(screen.getByTestId(PREVIEW_INFO)).toHaveTextContent("2000");
    });

    it("info panel shows shot date", () => {
      render(<PreviewPane />);
      // The date is formatted via toLocaleDateString() — just check something date-ish is there.
      const infoEl = screen.getByTestId(PREVIEW_INFO);
      // Should contain "2024" somewhere from the formatted date.
      expect(infoEl.textContent).toMatch(/2024/);
    });

    it("image scroll container has overflow-y-scroll class (not auto) — #535 invariant", () => {
      render(<PreviewPane />);
      const img = screen.getByTestId(PREVIEW_SINGLE_IMAGE);
      // Walk up to the scroll container (the parent of the img).
      const scrollContainer = img.parentElement;
      expect(scrollContainer).not.toBeNull();
      // Must have overflow-y:scroll applied (Tailwind class overflow-y-scroll).
      // We assert the class is present on the container.
      expect(scrollContainer!.className).toMatch(/overflow-y-scroll/);
    });

    it("double-click on image calls store.openFullRes with the file path", () => {
      const spy = vi.fn();
      useAppStore.setState({ openFullRes: spy } as Partial<ReturnType<typeof useAppStore.getState>>);

      render(<PreviewPane />);
      const img = screen.getByTestId(PREVIEW_SINGLE_IMAGE);
      act(() => {
        fireEvent.doubleClick(img);
      });
      expect(spy).toHaveBeenCalledWith(FILE_PATH);
    });
  });

  it("switches to a different file when store.preview.selectedFilePath changes", () => {
    seedManifest();
    useAppStore.setState({
      preview: { selectedFilePath: FILE_PATH, fullResPath: null, selectedGroupId: null },
    });
    const { rerender } = render(<PreviewPane />);

    // Verify first file is shown.
    expect(screen.getByTestId(PREVIEW_INFO)).toHaveTextContent("sunset.jpg");

    // Update store to select the second file.
    act(() => {
      useAppStore.setState({
        preview: { selectedFilePath: FILE_PATH_2, fullResPath: null, selectedGroupId: null },
      });
    });
    rerender(<PreviewPane />);

    expect(screen.getByTestId(PREVIEW_INFO)).toHaveTextContent("mountain.jpg");
  });
});

// ---------------------------------------------------------------------------
// FullResViewer tests
// ---------------------------------------------------------------------------

describe("FullResViewer", () => {
  beforeEach(() => {
    resetStore();
  });

  it("renders nothing visible when fullResPath is null", () => {
    render(<FullResViewer />);
    expect(screen.queryByTestId(FULLRES_DIALOG)).not.toBeInTheDocument();
  });

  it("renders the dialog when fullResPath is set", () => {
    useAppStore.setState({
      preview: { selectedFilePath: null, fullResPath: FILE_PATH, selectedGroupId: null },
    });
    render(<FullResViewer />);
    expect(screen.getByTestId(FULLRES_DIALOG)).toBeInTheDocument();
  });

  it("dialog shows the image element with correct testid", () => {
    useAppStore.setState({
      preview: { selectedFilePath: null, fullResPath: FILE_PATH, selectedGroupId: null },
    });
    render(<FullResViewer />);
    expect(screen.getByTestId(FULLRES_IMAGE)).toBeInTheDocument();
  });

  it("image src uses size=0 (full-res URL)", () => {
    useAppStore.setState({
      preview: { selectedFilePath: null, fullResPath: FILE_PATH, selectedGroupId: null },
    });
    render(<FullResViewer />);
    const img = screen.getByTestId(FULLRES_IMAGE);
    const src = img.getAttribute("src") ?? "";
    expect(src).toContain("size=0");
    expect(src).toContain(encodeURIComponent(FILE_PATH));
  });

  it("dialog title area contains the filename", () => {
    useAppStore.setState({
      preview: { selectedFilePath: null, fullResPath: FILE_PATH, selectedGroupId: null },
    });
    render(<FullResViewer />);
    const dialog = screen.getByTestId(FULLRES_DIALOG);
    expect(dialog.textContent).toContain("sunset.jpg");
  });

  it("Esc key calls store.closeFullRes", () => {
    useAppStore.setState({
      preview: { selectedFilePath: null, fullResPath: FILE_PATH, selectedGroupId: null },
    });
    const spy = vi.fn();
    useAppStore.setState({ closeFullRes: spy } as Partial<ReturnType<typeof useAppStore.getState>>);

    render(<FullResViewer />);

    act(() => {
      fireEvent.keyDown(document, { key: "Escape", code: "Escape" });
    });
    expect(spy).toHaveBeenCalled();
  });

  it("close button calls store.closeFullRes", () => {
    useAppStore.setState({
      preview: { selectedFilePath: null, fullResPath: FILE_PATH, selectedGroupId: null },
    });
    const spy = vi.fn();
    useAppStore.setState({ closeFullRes: spy } as Partial<ReturnType<typeof useAppStore.getState>>);

    render(<FullResViewer />);

    const closeBtn = screen.getByRole("button", { name: /close/i });
    act(() => {
      fireEvent.click(closeBtn);
    });
    expect(spy).toHaveBeenCalled();
  });

  it("shows 413 fallback button and hides image on load error", () => {
    useAppStore.setState({
      preview: { selectedFilePath: null, fullResPath: FILE_PATH, selectedGroupId: null },
    });
    render(<FullResViewer />);

    const img = screen.getByTestId(FULLRES_IMAGE);
    act(() => {
      fireEvent.error(img);
    });

    // Fallback text is shown.
    expect(screen.getByText(/file too large for in-browser full-res/i)).toBeInTheDocument();

    // Fallback "Open in default app" button is shown.
    expect(
      screen.getByRole("button", { name: /open in default app/i })
    ).toBeInTheDocument();

    // The fullres image should no longer be in the DOM after an error
    // (component hides it, shows fallback instead).
    expect(screen.queryByTestId(FULLRES_IMAGE)).not.toBeInTheDocument();
  });

  it("fallback 'Open in default app' button calls store.revealInExplorer", () => {
    useAppStore.setState({
      preview: { selectedFilePath: null, fullResPath: FILE_PATH, selectedGroupId: null },
    });
    const spy = vi.fn().mockResolvedValue(undefined);
    useAppStore.setState({ revealInExplorer: spy } as Partial<ReturnType<typeof useAppStore.getState>>);

    render(<FullResViewer />);

    const img = screen.getByTestId(FULLRES_IMAGE);
    act(() => {
      fireEvent.error(img);
    });

    const revealBtn = screen.getByRole("button", { name: /open in default app/i });
    act(() => {
      fireEvent.click(revealBtn);
    });

    expect(spy).toHaveBeenCalledWith(FILE_PATH);
  });
});
