// Unit tests for the previewMode discriminator and the setSelectedGroup /
// setSelectedFile mutual-exclusion invariant.
//
// These are contract pins for the grid feature store slice:
//  - selectPreviewMode returns 'empty' / 'single' / 'grid' correctly
//  - setSelectedFile clears selectedGroupId (mutual exclusion)
//  - setSelectedGroup clears selectedFilePath (mutual exclusion)
//
// Kept in a separate file from the main useAppStore.test.ts so the mock
// boundary for api/client (which the main test needs for network actions)
// does not need to cover this purely-synchronous slice.

import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "./useAppStore";
import { selectPreviewMode } from "./useAppStore";

// ---------------------------------------------------------------------------
// Reset to a clean baseline before each test.
// ---------------------------------------------------------------------------

beforeEach(() => {
  useAppStore.setState((s) => ({
    ...s,
    preview: {
      selectedFilePath: null,
      fullResPath: null,
      selectedGroupId: null,
    },
  }));
});

// ---------------------------------------------------------------------------
// selectPreviewMode — the derived discriminator
// ---------------------------------------------------------------------------

describe("selectPreviewMode", () => {
  it("returns 'empty' when nothing is selected", () => {
    expect(selectPreviewMode(useAppStore.getState())).toBe("empty");
  });

  it("returns 'single' when a file path is selected", () => {
    useAppStore.getState().setSelectedFile("/photos/a.jpg");
    expect(selectPreviewMode(useAppStore.getState())).toBe("single");
  });

  it("returns 'grid' when a group id is selected", () => {
    useAppStore.getState().setSelectedGroup(42);
    expect(selectPreviewMode(useAppStore.getState())).toBe("grid");
  });

  it("returns 'empty' after setSelectedFile(null) clears the file selection", () => {
    useAppStore.getState().setSelectedFile("/photos/a.jpg");
    useAppStore.getState().setSelectedFile(null);
    expect(selectPreviewMode(useAppStore.getState())).toBe("empty");
  });

  it("returns 'empty' after setSelectedGroup(null) clears the group selection", () => {
    useAppStore.getState().setSelectedGroup(1);
    useAppStore.getState().setSelectedGroup(null);
    expect(selectPreviewMode(useAppStore.getState())).toBe("empty");
  });
});

// ---------------------------------------------------------------------------
// Mutual exclusion invariant
// ---------------------------------------------------------------------------

describe("setSelectedFile / setSelectedGroup mutual exclusion", () => {
  it("setSelectedFile clears selectedGroupId (file wins, grid dismissed)", () => {
    // Selecting a group first, then a file should leave us in 'single' mode.
    useAppStore.getState().setSelectedGroup(7);
    expect(useAppStore.getState().preview.selectedGroupId).toBe(7);

    useAppStore.getState().setSelectedFile("/photos/b.jpg");

    const { preview } = useAppStore.getState();
    expect(preview.selectedFilePath).toBe("/photos/b.jpg");
    expect(preview.selectedGroupId).toBeNull();
    // Confirm the mode discriminator agrees.
    expect(selectPreviewMode(useAppStore.getState())).toBe("single");
  });

  it("setSelectedGroup clears selectedFilePath (group wins, single dismissed)", () => {
    // Selecting a file first, then a group should leave us in 'grid' mode.
    useAppStore.getState().setSelectedFile("/photos/a.jpg");
    expect(useAppStore.getState().preview.selectedFilePath).toBe("/photos/a.jpg");

    useAppStore.getState().setSelectedGroup(3);

    const { preview } = useAppStore.getState();
    expect(preview.selectedGroupId).toBe(3);
    expect(preview.selectedFilePath).toBeNull();
    // Confirm the mode discriminator agrees.
    expect(selectPreviewMode(useAppStore.getState())).toBe("grid");
  });

  it("setSelectedFile(null) with no group → 'empty', not 'grid'", () => {
    // Regression: if null-file didn't clear group, mode would read from stale group.
    useAppStore.getState().setSelectedFile("/photos/a.jpg");
    useAppStore.getState().setSelectedFile(null);

    const { preview } = useAppStore.getState();
    expect(preview.selectedFilePath).toBeNull();
    expect(preview.selectedGroupId).toBeNull();
    expect(selectPreviewMode(useAppStore.getState())).toBe("empty");
  });

  it("a sequence of alternating file/group selections always produces the correct mode", () => {
    // Simulate a user clicking: file → group → file → null (clear)
    const store = useAppStore.getState();

    store.setSelectedFile("/photos/a.jpg");
    expect(selectPreviewMode(useAppStore.getState())).toBe("single");

    store.setSelectedGroup(5);
    expect(selectPreviewMode(useAppStore.getState())).toBe("grid");

    store.setSelectedFile("/photos/b.jpg");
    expect(selectPreviewMode(useAppStore.getState())).toBe("single");

    store.setSelectedFile(null);
    expect(selectPreviewMode(useAppStore.getState())).toBe("empty");
  });
});
