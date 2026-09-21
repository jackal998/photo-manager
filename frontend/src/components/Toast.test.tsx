// The undo toast (#878 slice G, open questions Q5).
//
// Q5 removed the confirm dialog from a bulk decision write and put this in its
// place, so every behaviour below is the safety net, not decoration: it says
// what happened, it says what it refused to touch, it can be reversed, and it
// goes away on its own without taking the reversal with it while the user is
// still reaching for it.

import { render, screen, act, fireEvent } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

import { Toast, TOAST_TIMEOUT_MS } from "./Toast";
import { useAppStore } from "@/store/useAppStore";
import { useI18nStore } from "@/i18n/useI18nStore";
import type { UndoToast } from "@/store/types";
import { MAIN_TOAST, MAIN_TOAST_UNDO } from "@/testids";

function seedToast(overrides: Partial<UndoToast> = {}) {
  act(() => {
    useAppStore.setState({
      toast: {
        id: 1,
        kind: "keep-best",
        groupNumber: 12,
        decision: null,
        affectedCount: 4,
        lockedCount: 0,
        snapshot: [],
        undoing: false,
        ...overrides,
      },
    } as never);
  });
}

describe("Toast", () => {
  let undoMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    useI18nStore.setState({ locale: "en", catalog: {} });
    undoMock = vi.fn().mockResolvedValue(undefined);
    act(() => {
      useAppStore.setState({ toast: null, undoKeepBest: undoMock } as never);
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders nothing when there is no toast", () => {
    render(<Toast />);
    expect(screen.queryByTestId(MAIN_TOAST)).toBeNull();
  });

  it("names the group and the number of files it marked", () => {
    seedToast();
    render(<Toast />);
    expect(screen.getByTestId(MAIN_TOAST)).toHaveTextContent(
      "Group 12 · 4 files marked for deletion"
    );
  });

  it("uses the singular noun for a one-file write", () => {
    seedToast({ affectedCount: 1 });
    render(<Toast />);
    const toast = screen.getByTestId(MAIN_TOAST);
    expect(toast).toHaveTextContent("1 file marked for deletion");
    expect(toast.textContent).not.toContain("1 files");
  });

  it("reports locked files it left alone — Q5's promise, singular and plural", () => {
    seedToast({ lockedCount: 1 });
    const { unmount } = render(<Toast />);
    expect(screen.getByTestId(MAIN_TOAST)).toHaveTextContent(
      "1 locked file unchanged"
    );
    unmount();

    seedToast({ id: 2, lockedCount: 3 });
    render(<Toast />);
    expect(screen.getByTestId(MAIN_TOAST)).toHaveTextContent(
      "3 locked files unchanged"
    );
  });

  it("says nothing about locks when it skipped none", () => {
    seedToast({ lockedCount: 0 });
    render(<Toast />);
    expect(screen.getByTestId(MAIN_TOAST).textContent).not.toContain("locked");
  });

  it("renders the zh_TW copy", () => {
    useI18nStore.setState({
      locale: "zh_TW",
      catalog: {
        "web.toast.keep_best": "群組 {n} · 已標記 {count} 個{fileWord}待刪除",
        "web.toast.file_plural": "檔案",
        "web.toast.locked_unchanged_singular": "{count} 個鎖定的檔案未變更",
        "web.toast.undo": "復原",
      },
    });
    seedToast({ lockedCount: 1 });
    render(<Toast />);
    const toast = screen.getByTestId(MAIN_TOAST);
    expect(toast).toHaveTextContent("群組 12 · 已標記 4 個檔案待刪除");
    expect(toast).toHaveTextContent("1 個鎖定的檔案未變更");
    expect(toast).toHaveTextContent("復原");
    expect(toast.textContent).not.toContain("Undo");
  });

  it("Undo dispatches undoKeepBest", () => {
    seedToast();
    render(<Toast />);
    fireEvent.click(screen.getByTestId(MAIN_TOAST_UNDO));
    expect(undoMock).toHaveBeenCalledTimes(1);
  });

  it("disables Undo while one is in flight, so it cannot fire twice", () => {
    seedToast({ undoing: true });
    render(<Toast />);
    const undo = screen.getByTestId(MAIN_TOAST_UNDO);
    expect(undo).toBeDisabled();
    fireEvent.click(undo);
    expect(undoMock).not.toHaveBeenCalled();
  });

  it("dismisses itself after the timeout", () => {
    seedToast();
    render(<Toast />);
    act(() => {
      vi.advanceTimersByTime(TOAST_TIMEOUT_MS - 1);
    });
    expect(useAppStore.getState().toast).not.toBeNull();
    act(() => {
      vi.advanceTimersByTime(2);
    });
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("pauses its timer while hovered, and resumes with the REMAINDER", () => {
    seedToast();
    render(<Toast />);
    const toast = screen.getByTestId(MAIN_TOAST);

    act(() => {
      vi.advanceTimersByTime(TOAST_TIMEOUT_MS - 1000);
    });
    fireEvent.mouseEnter(toast);
    act(() => {
      vi.advanceTimersByTime(TOAST_TIMEOUT_MS * 3);
    });
    // Still there: the six seconds are not running while the pointer is on it.
    expect(useAppStore.getState().toast).not.toBeNull();

    fireEvent.mouseLeave(toast);
    act(() => {
      vi.advanceTimersByTime(1100);
    });
    // Gone after the ~1s that was left — not after a fresh six.
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("a NEW keep-best replaces the toast and restarts the clock", () => {
    seedToast({ id: 1, groupNumber: 12 });
    render(<Toast />);
    act(() => {
      vi.advanceTimersByTime(TOAST_TIMEOUT_MS - 500);
    });
    seedToast({ id: 2, groupNumber: 13, affectedCount: 2 });
    expect(screen.getByTestId(MAIN_TOAST)).toHaveTextContent("Group 13");

    // The first toast's remaining 500ms must NOT dismiss the second one.
    act(() => {
      vi.advanceTimersByTime(600);
    });
    expect(useAppStore.getState().toast).not.toBeNull();
    act(() => {
      vi.advanceTimersByTime(TOAST_TIMEOUT_MS);
    });
    expect(useAppStore.getState().toast).toBeNull();
  });
});
