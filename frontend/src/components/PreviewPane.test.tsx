// Tests for PreviewPane and FullResViewer.
// Seeds the Zustand store directly (same pattern as ResultTree.test.tsx).

import { act, render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { PreviewPane } from "./PreviewPane";
import { FullResViewer } from "./FullResViewer";
import { useAppStore } from "@/store/useAppStore";
import { PREVIEW_PANE, PREVIEW_SINGLE_IMAGE, PREVIEW_INFO, FULLRES_DIALOG, FULLRES_IMAGE } from "@/testids";
import type { Group } from "@/api/types";

// #787 — the HEVC capability probe. Mocked here so these tests exercise the
// WIRING (does the surface consult it, with the right path, for the INITIAL
// src?) independently of the probe's own logic, which is tested against a fake
// navigator.mediaCapabilities in lib/videoCapabilities.test.ts. The default
// `false` is what the real module reports under jsdom (no MediaCapabilities
// API → "unknown"), so every pre-existing test below is unaffected.
const capabilities = vi.hoisted(() => ({
  canPlayHevc: vi.fn(() => Promise.resolve(false)),
  prefersTranscodedVideo: vi.fn<(filePath: string) => boolean>(() => false),
}));
vi.mock("@/lib/videoCapabilities", () => capabilities);

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
    capabilities.prefersTranscodedVideo.mockReturnValue(false);
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

  // -------------------------------------------------------------------------
  // #787 — HEVC capability pre-check picks the INITIAL src
  // -------------------------------------------------------------------------

  function seedVideo(path: string): void {
    useAppStore.setState({
      manifest: {
        path: "/manifests/test.db",
        groups: [
          {
            group_number: 1,
            member_count: 1,
            items: [
              {
                ...testRow,
                file_path: path,
                basename: path.split("/").pop() ?? path,
                media_type: "video" as const,
              },
            ],
          },
        ],
        totalGroups: 1,
        totalFiles: 1,
        loading: false,
        error: null,
      },
      preview: { selectedFilePath: path, fullResPath: null, selectedGroupId: null },
    });
  }

  it("starts on the transcode src when the engine is known HEVC-incapable (#787)", () => {
    // Without this the user pays a guaranteed-to-fail original-bytes fetch and
    // decode before the transcode wait even begins.
    capabilities.prefersTranscodedVideo.mockReturnValue(true);
    seedVideo("/photos/clip.mov");
    render(<PreviewPane />);

    expect(capabilities.prefersTranscodedVideo).toHaveBeenCalledWith("/photos/clip.mov");
    expect(screen.getByTestId(PREVIEW_SINGLE_IMAGE).getAttribute("src") ?? "").toContain(
      "transcode=h264"
    );
  });

  it("never mounts the original-bytes src when switching to a preempted video (#787)", () => {
    // The regression this pins was REAL and shipped in the first draft of
    // #787: resetting the transcode choice from a useEffect commits one render
    // carrying /api/media?path=… before swapping, so the browser starts the
    // very fetch the pre-check exists to skip. Caught in headless Chromium,
    // where the <video> was observed on the original-bytes src after the swap
    // should already have happened. Asserting only the FINAL src cannot see
    // this — every src the element ever had has to be checked.
    capabilities.prefersTranscodedVideo.mockImplementation((path: string) =>
      path.endsWith(".mov")
    );
    seedManifest();
    useAppStore.setState({
      preview: { selectedFilePath: FILE_PATH, fullResPath: null, selectedGroupId: null },
    });
    const { container } = render(<PreviewPane />);

    const observer = new MutationObserver(() => {});
    observer.observe(container, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["src"],
      attributeOldValue: true,
    });

    act(() => {
      seedVideo("/photos/clip.mov");
    });

    // takeRecords() drains synchronously — no reliance on the observer's
    // async callback having run.
    const records = observer.takeRecords();
    observer.disconnect();

    const srcs: string[] = [];
    for (const record of records) {
      if (record.type === "attributes" && record.oldValue !== null) {
        const target = record.target as HTMLElement;
        if (target.tagName === "VIDEO") srcs.push(record.oldValue);
      }
      record.addedNodes.forEach((node) => {
        if (!(node instanceof HTMLElement)) return;
        const video =
          node.tagName === "VIDEO" ? node : node.querySelector("video");
        if (video !== null) srcs.push(video.getAttribute("src") ?? "");
      });
    }

    // Guard against the test silently proving nothing: if no <video> was ever
    // observed, the assertion below would pass vacuously.
    expect(srcs.length).toBeGreaterThan(0);
    expect(srcs.filter((src) => !src.includes("transcode=h264"))).toEqual([]);
  });

  // The two-attempt contract, both directions. The hint chooses which source
  // we START on; the single allowed swap then goes to the OTHER one. Reading
  // "have we swapped?" off useTranscode alone made a hint-started transcode
  // terminal on its first error — which on a packaged build (no ffmpeg → the
  // route 501s) killed every .mov that would have played natively.

  function fireVideoError(): void {
    act(() => {
      fireEvent.error(screen.getByTestId(PREVIEW_SINGLE_IMAGE));
    });
  }

  function currentSrc(): string {
    return screen.getByTestId(PREVIEW_SINGLE_IMAGE).getAttribute("src") ?? "";
  }

  it("hint → error → falls back to the ORIGINAL bytes (#787 round 2)", () => {
    capabilities.prefersTranscodedVideo.mockReturnValue(true);
    seedVideo("/photos/clip.mov");
    render(<PreviewPane />);
    expect(currentSrc()).toContain("transcode=h264");

    fireVideoError();

    // The transcode 501'd (or failed); the original bytes have never been
    // tried, so they must be, not a terminal message.
    expect(screen.queryByText(/video cannot be played/i)).not.toBeInTheDocument();
    expect(currentSrc()).not.toContain("transcode=h264");
    expect(currentSrc()).toContain("/api/media?path=");
  });

  it("native → error → swaps to the transcode (unchanged behaviour)", () => {
    capabilities.prefersTranscodedVideo.mockReturnValue(false);
    seedVideo("/photos/clip.mp4");
    render(<PreviewPane />);
    expect(currentSrc()).not.toContain("transcode=h264");

    fireVideoError();

    expect(screen.queryByText(/video cannot be played/i)).not.toBeInTheDocument();
    expect(currentSrc()).toContain("transcode=h264");
  });

  it("hint → error → native → error → terminal (#787 round 2)", () => {
    capabilities.prefersTranscodedVideo.mockReturnValue(true);
    seedVideo("/photos/clip.mov");
    render(<PreviewPane />);

    fireVideoError();
    expect(currentSrc()).not.toContain("transcode=h264");
    fireVideoError();

    // Both sources have now failed — exactly one swap is allowed either way.
    expect(screen.getByText(/video cannot be played/i)).toBeInTheDocument();
    expect(screen.queryByTestId(PREVIEW_SINGLE_IMAGE)).not.toBeInTheDocument();
  });

  it("native → error → transcode → error → terminal (unchanged behaviour)", () => {
    capabilities.prefersTranscodedVideo.mockReturnValue(false);
    seedVideo("/photos/clip.mp4");
    render(<PreviewPane />);

    fireVideoError();
    expect(currentSrc()).toContain("transcode=h264");
    fireVideoError();

    expect(screen.getByText(/video cannot be played/i)).toBeInTheDocument();
    expect(screen.queryByTestId(PREVIEW_SINGLE_IMAGE)).not.toBeInTheDocument();
  });

  it("starts on the original bytes when the probe has no verdict (#787)", () => {
    // The default in jsdom, in any browser before the probe resolves, and for
    // every container the gate excludes — i.e. the pre-#787 behaviour, which
    // the reactive error swap still backs up.
    capabilities.prefersTranscodedVideo.mockReturnValue(false);
    seedVideo("/photos/clip.mp4");
    render(<PreviewPane />);

    expect(screen.getByTestId(PREVIEW_SINGLE_IMAGE).getAttribute("src") ?? "").not.toContain(
      "transcode=h264"
    );
  });
});

// ---------------------------------------------------------------------------
// FullResViewer tests
// ---------------------------------------------------------------------------

describe("FullResViewer", () => {
  beforeEach(() => {
    resetStore();
    capabilities.prefersTranscodedVideo.mockReturnValue(false);
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

  it("shows 'Preparing video…' after a decode error swaps to the transcode fallback (#737)", () => {
    // A video row must be in the manifest so isVideo resolves true.
    const videoPath = "/photos/clip.mov";
    useAppStore.setState({
      manifest: {
        path: "/manifests/test.db",
        groups: [
          {
            group_number: 1,
            member_count: 1,
            items: [
              { ...testRow, file_path: videoPath, basename: "clip.mov", media_type: "video" },
            ],
          },
        ],
        totalGroups: 1,
        totalFiles: 1,
        loading: false,
        error: null,
      },
      preview: { selectedFilePath: null, fullResPath: videoPath, selectedGroupId: null },
    });
    render(<FullResViewer />);

    // Before any error the native <video> plays — no "Preparing video…".
    expect(screen.queryByText(/preparing video/i)).not.toBeInTheDocument();

    // First decode error swaps to the H.264 transcode fallback; while the
    // transcode is in flight the "Preparing video…" indicator is shown.
    const video = screen.getByTestId(FULLRES_IMAGE);
    act(() => {
      fireEvent.error(video);
    });
    expect(screen.getByText(/preparing video/i)).toBeInTheDocument();
    expect(screen.getByTestId(FULLRES_IMAGE).getAttribute("src") ?? "").toContain(
      "transcode=h264"
    );
  });

  it("starts on the transcode src when the engine is known HEVC-incapable (#787)", () => {
    capabilities.prefersTranscodedVideo.mockReturnValue(true);
    const videoPath = "/photos/clip.mov";
    useAppStore.setState({
      manifest: {
        path: "/manifests/test.db",
        groups: [
          {
            group_number: 1,
            member_count: 1,
            items: [
              { ...testRow, file_path: videoPath, basename: "clip.mov", media_type: "video" },
            ],
          },
        ],
        totalGroups: 1,
        totalFiles: 1,
        loading: false,
        error: null,
      },
      preview: { selectedFilePath: null, fullResPath: videoPath, selectedGroupId: null },
    });
    render(<FullResViewer />);

    expect(capabilities.prefersTranscodedVideo).toHaveBeenCalledWith(videoPath);
    expect(screen.getByTestId(FULLRES_IMAGE).getAttribute("src") ?? "").toContain(
      "transcode=h264"
    );
    // The transcode is being prepared from the start, so the same indicator
    // the reactive path shows must already be up — not a blank player.
    expect(screen.getByText(/preparing video/i)).toBeInTheDocument();
  });

  // Two-attempt contract, both directions — same rationale as PreviewPane's.

  function seedFullResVideo(path: string): void {
    useAppStore.setState({
      manifest: {
        path: "/manifests/test.db",
        groups: [
          {
            group_number: 1,
            member_count: 1,
            items: [
              {
                ...testRow,
                file_path: path,
                basename: path.split("/").pop() ?? path,
                media_type: "video" as const,
              },
            ],
          },
        ],
        totalGroups: 1,
        totalFiles: 1,
        loading: false,
        error: null,
      },
      preview: { selectedFilePath: null, fullResPath: path, selectedGroupId: null },
    });
  }

  function fireFullResVideoError(): void {
    act(() => {
      fireEvent.error(screen.getByTestId(FULLRES_IMAGE));
    });
  }

  function fullResSrc(): string {
    return screen.getByTestId(FULLRES_IMAGE).getAttribute("src") ?? "";
  }

  it("hint → error → falls back to the ORIGINAL bytes (#787 round 2)", () => {
    capabilities.prefersTranscodedVideo.mockReturnValue(true);
    seedFullResVideo("/photos/clip.mov");
    render(<FullResViewer />);
    expect(fullResSrc()).toContain("transcode=h264");

    fireFullResVideoError();

    expect(screen.queryByText(/video cannot be played/i)).not.toBeInTheDocument();
    expect(fullResSrc()).not.toContain("transcode=h264");
    expect(fullResSrc()).toContain("/api/media?path=");
  });

  it("hint → error → native → error → terminal (#787 round 2)", () => {
    capabilities.prefersTranscodedVideo.mockReturnValue(true);
    seedFullResVideo("/photos/clip.mov");
    render(<FullResViewer />);

    fireFullResVideoError();
    expect(fullResSrc()).not.toContain("transcode=h264");
    fireFullResVideoError();

    expect(screen.getByText(/video cannot be played/i)).toBeInTheDocument();
    expect(screen.queryByTestId(FULLRES_IMAGE)).not.toBeInTheDocument();
  });

  it("native → error → transcode → error → terminal (unchanged behaviour)", () => {
    capabilities.prefersTranscodedVideo.mockReturnValue(false);
    seedFullResVideo("/photos/clip.mp4");
    render(<FullResViewer />);

    fireFullResVideoError();
    expect(fullResSrc()).toContain("transcode=h264");
    fireFullResVideoError();

    expect(screen.getByText(/video cannot be played/i)).toBeInTheDocument();
    expect(screen.queryByTestId(FULLRES_IMAGE)).not.toBeInTheDocument();
  });
});
