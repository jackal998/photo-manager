// FsBrowser tests — real picker behaviour, no padding.
//
// Covers the three modes' distinct confirm semantics (the part most likely to
// regress): directory → returns the current folder; file → returns the
// selected file; save → returns <cwd><sep><filename>. Plus navigation into a
// subfolder and the manifest tag.

import { type ComponentProps } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { browseFs } from "@/api/client";
import { FsBrowser } from "./FsBrowser";
import {
  FS_BROWSER,
  FS_BROWSER_CONFIRM,
  FS_BROWSER_CANCEL,
  FS_BROWSER_ENTRY,
  FS_BROWSER_FILENAME,
  FS_BROWSER_UP,
  FS_BROWSER_ERROR,
} from "@/testids";

vi.mock("@/api/client", () => ({
  browseFs: vi.fn(),
}));

// A two-level fake filesystem: roots → C:\ → {photos/, scan.db, readme.txt}.
const ROOTS = {
  path: "",
  parent: null,
  entries: [{ name: "C:\\", path: "C:\\", is_dir: true, is_manifest: false }],
};
const CDRIVE = {
  path: "C:\\",
  parent: null,
  entries: [
    { name: "photos", path: "C:\\photos", is_dir: true, is_manifest: false },
    { name: "scan.db", path: "C:\\scan.db", is_dir: false, is_manifest: true },
    { name: "readme.txt", path: "C:\\readme.txt", is_dir: false, is_manifest: false },
  ],
};

beforeEach(() => {
  vi.mocked(browseFs).mockReset();
  vi.mocked(browseFs).mockImplementation(async (p?: string) => {
    if (p === undefined || p === "") return ROOTS;
    if (p === "C:\\") return CDRIVE;
    throw new Error(`not found: ${p ?? "<roots>"}`);
  });
});

function renderBrowser(props: Partial<ComponentProps<typeof FsBrowser>> = {}) {
  const onConfirm = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <FsBrowser
      open
      mode="directory"
      title="Pick"
      onConfirm={onConfirm}
      onOpenChange={onOpenChange}
      {...props}
    />
  );
  return { onConfirm, onOpenChange };
}

describe("FsBrowser", () => {
  it("loads roots on open and shows entries", async () => {
    renderBrowser();
    expect(screen.getByTestId(FS_BROWSER)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getAllByTestId(FS_BROWSER_ENTRY).length).toBeGreaterThan(0)
    );
    expect(screen.getByText("C:\\")).toBeInTheDocument();
  });

  it("directory mode: confirm is disabled at roots, returns the folder after navigating", async () => {
    const user = userEvent.setup();
    const { onConfirm, onOpenChange } = renderBrowser({ mode: "directory" });

    // At roots cwd === "" → confirm disabled.
    await waitFor(() => expect(screen.getByTestId(FS_BROWSER_CONFIRM)).toBeDisabled());

    // Navigate into C:\.
    await user.click(await screen.findByText("C:\\"));
    await waitFor(() => expect(screen.getByTestId(FS_BROWSER_CONFIRM)).not.toBeDisabled());

    // directory mode hides files — only the "photos" subdir is shown.
    expect(screen.queryByText("readme.txt")).not.toBeInTheDocument();
    expect(screen.getByText("photos")).toBeInTheDocument();

    await user.click(screen.getByTestId(FS_BROWSER_CONFIRM));
    expect(onConfirm).toHaveBeenCalledWith("C:\\");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("file mode: selecting a file then confirming returns the file path", async () => {
    const user = userEvent.setup();
    const { onConfirm } = renderBrowser({ mode: "file" });

    await user.click(await screen.findByText("C:\\"));
    // Files are visible in file mode; the manifest carries a tag.
    const dbRow = await screen.findByText("scan.db");
    expect(screen.getByText("manifest")).toBeInTheDocument();

    // Confirm is disabled until a file is selected.
    expect(screen.getByTestId(FS_BROWSER_CONFIRM)).toBeDisabled();
    await user.click(dbRow);
    await waitFor(() => expect(screen.getByTestId(FS_BROWSER_CONFIRM)).not.toBeDisabled());

    await user.click(screen.getByTestId(FS_BROWSER_CONFIRM));
    expect(onConfirm).toHaveBeenCalledWith("C:\\scan.db");
  });

  it("save mode: joins cwd + separator + filename", async () => {
    const user = userEvent.setup();
    const { onConfirm } = renderBrowser({ mode: "save", defaultFilename: "out.db" });

    await user.click(await screen.findByText("C:\\"));
    expect(screen.getByTestId(FS_BROWSER_FILENAME)).toHaveValue("out.db");

    await user.click(screen.getByTestId(FS_BROWSER_CONFIRM));
    expect(onConfirm).toHaveBeenCalledWith("C:\\out.db");
  });

  it("Cancel closes without confirming", async () => {
    const user = userEvent.setup();
    const { onConfirm, onOpenChange } = renderBrowser();
    await user.click(screen.getByTestId(FS_BROWSER_CANCEL));
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("falls back to roots when the initialPath hint cannot be listed", async () => {
    // A partial/invalid hint must not strand the user on an error screen.
    renderBrowser({ initialPath: "C:\\nonexistent" });
    await waitFor(() => expect(screen.getByText("C:\\")).toBeInTheDocument());
    // browseFs was called with the bad hint first, then with roots.
    expect(vi.mocked(browseFs)).toHaveBeenCalledWith("C:\\nonexistent");
    expect(vi.mocked(browseFs)).toHaveBeenCalledWith(undefined);
  });

  it("a file-path hint opens at the file's parent folder", async () => {
    // initialPath points at a FILE → listing it fails → fall back to its parent
    // directory (NOT all the way to roots). This is the manifest second-open path.
    vi.mocked(browseFs).mockReset();
    vi.mocked(browseFs).mockImplementation(async (p?: string) => {
      if (p === "C:\\drive\\scan.db") throw new Error("not a directory");
      if (p === "C:\\drive")
        return {
          path: "C:\\drive",
          parent: "C:\\",
          entries: [
            { name: "scan.db", path: "C:\\drive\\scan.db", is_dir: false, is_manifest: true },
          ],
        };
      if (p === undefined || p === "") return ROOTS;
      throw new Error(`nf: ${p ?? "<roots>"}`);
    });
    renderBrowser({ mode: "file", initialPath: "C:\\drive\\scan.db" });
    await waitFor(() => expect(screen.getByText("scan.db")).toBeInTheDocument());
    expect(vi.mocked(browseFs)).toHaveBeenCalledWith("C:\\drive\\scan.db");
    expect(vi.mocked(browseFs)).toHaveBeenCalledWith("C:\\drive");
  });

  it("Up from a drive root returns to the filesystem roots", async () => {
    const user = userEvent.setup();
    renderBrowser({ mode: "file" });
    await user.click(await screen.findByText("C:\\"));
    await waitFor(() => expect(screen.getByText("photos")).toBeInTheDocument());
    // CDRIVE has parent=null (drive root) → Up must fall back to roots.
    await user.click(screen.getByTestId(FS_BROWSER_UP));
    await waitFor(() => expect(screen.queryByText("photos")).not.toBeInTheDocument());
    expect(screen.getByTestId(FS_BROWSER_UP)).toBeDisabled();
  });

  it("shows an error when no directory (hint, parent, or roots) can be listed", async () => {
    vi.mocked(browseFs).mockReset();
    vi.mocked(browseFs).mockRejectedValue(new Error("Permission denied"));
    renderBrowser({ initialPath: "C:\\locked\\deep" });
    await waitFor(() =>
      expect(screen.getByTestId(FS_BROWSER_ERROR)).toBeInTheDocument()
    );
    expect(screen.getByTestId(FS_BROWSER_ERROR)).toHaveTextContent("Permission denied");
  });

  it("save mode joins with a POSIX separator", async () => {
    const user = userEvent.setup();
    vi.mocked(browseFs).mockReset();
    vi.mocked(browseFs).mockImplementation(async (p?: string) => {
      if (p === undefined || p === "")
        return {
          path: "",
          parent: null,
          entries: [{ name: "home", path: "/home", is_dir: true, is_manifest: false }],
        };
      if (p === "/home") return { path: "/home", parent: "/", entries: [] };
      throw new Error(`nf: ${p ?? "<roots>"}`);
    });
    const onConfirm = vi.fn();
    render(
      <FsBrowser
        open
        mode="save"
        title="P"
        defaultFilename="out.db"
        onConfirm={onConfirm}
        onOpenChange={vi.fn()}
      />
    );
    await user.click(await screen.findByText("home"));
    await user.click(screen.getByTestId(FS_BROWSER_CONFIRM));
    expect(onConfirm).toHaveBeenCalledWith("/home/out.db");
  });
});
