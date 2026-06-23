// MenuBar tests — real wiring + manifest-gating, no padding.
//
// The behaviours that matter: each menu item dispatches the right callback,
// and the manifest-gated Action items are disabled until a manifest loads
// (the second ActionDialog entry point must obey the same gate as the
// toolbar button).

import { type ComponentProps } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";

import { MenuBar } from "./MenuBar";
import {
  MAIN_MENU_BAR,
  MENU_FILE,
  MENU_FILE_SCAN,
  MENU_FILE_OPEN,
  MENU_ACTION,
  MENU_ACTION_SET,
  MENU_ACTION_EXECUTE,
  MENU_ACTION_EXECUTE_SELECTED,
  MENU_VIEW,
  MENU_VIEW_LANG_ZH,
} from "@/testids";

function setup(overrides: Partial<ComponentProps<typeof MenuBar>> = {}) {
  const handlers = {
    onScan: vi.fn(),
    onOpenManifest: vi.fn(),
    onSetAction: vi.fn(),
    onExecute: vi.fn(),
    onExecuteSelected: vi.fn(),
    onSetLocale: vi.fn(),
  };
  render(
    <MenuBar
      manifestLoaded={false}
      hasSelection={false}
      locale="en"
      {...handlers}
      {...overrides}
    />
  );
  return handlers;
}

describe("MenuBar", () => {
  it("renders the three top-level menus", () => {
    setup();
    expect(screen.getByTestId(MAIN_MENU_BAR)).toBeInTheDocument();
    expect(screen.getByTestId(MENU_FILE)).toBeInTheDocument();
    expect(screen.getByTestId(MENU_ACTION)).toBeInTheDocument();
    expect(screen.getByTestId(MENU_VIEW)).toBeInTheDocument();
  });

  it("File → Scan Sources dispatches onScan", async () => {
    const user = userEvent.setup();
    const h = setup();
    await user.click(screen.getByTestId(MENU_FILE));
    await user.click(await screen.findByTestId(MENU_FILE_SCAN));
    expect(h.onScan).toHaveBeenCalledOnce();
  });

  it("File → Open Manifest dispatches onOpenManifest", async () => {
    const user = userEvent.setup();
    const h = setup();
    await user.click(screen.getByTestId(MENU_FILE));
    await user.click(await screen.findByTestId(MENU_FILE_OPEN));
    expect(h.onOpenManifest).toHaveBeenCalledOnce();
  });

  it("Action items are disabled with no manifest and do not dispatch", async () => {
    const user = userEvent.setup();
    const h = setup({ manifestLoaded: false });
    await user.click(screen.getByTestId(MENU_ACTION));
    const setItem = await screen.findByTestId(MENU_ACTION_SET);
    expect(setItem).toHaveAttribute("data-disabled");
    await user.click(setItem);
    expect(h.onSetAction).not.toHaveBeenCalled();
  });

  it("Action → Set Action dispatches onSetAction when a manifest is loaded", async () => {
    const user = userEvent.setup();
    const h = setup({ manifestLoaded: true });
    await user.click(screen.getByTestId(MENU_ACTION));
    await user.click(await screen.findByTestId(MENU_ACTION_SET));
    expect(h.onSetAction).toHaveBeenCalledOnce();
  });

  it("Action → Execute dispatches onExecute when a manifest is loaded", async () => {
    const user = userEvent.setup();
    const h = setup({ manifestLoaded: true });
    await user.click(screen.getByTestId(MENU_ACTION));
    await user.click(await screen.findByTestId(MENU_ACTION_EXECUTE));
    expect(h.onExecute).toHaveBeenCalledOnce();
  });

  it("Execute (only selected) is disabled without a main-tree selection", async () => {
    const user = userEvent.setup();
    // Manifest loaded but nothing selected → the entry must stay gated.
    const h = setup({ manifestLoaded: true, hasSelection: false });
    await user.click(screen.getByTestId(MENU_ACTION));
    const item = await screen.findByTestId(MENU_ACTION_EXECUTE_SELECTED);
    expect(item).toHaveAttribute("data-disabled");
    await user.click(item);
    expect(h.onExecuteSelected).not.toHaveBeenCalled();
  });

  it("Execute (only selected) is disabled without a manifest even with a selection", async () => {
    const user = userEvent.setup();
    const h = setup({ manifestLoaded: false, hasSelection: true });
    await user.click(screen.getByTestId(MENU_ACTION));
    const item = await screen.findByTestId(MENU_ACTION_EXECUTE_SELECTED);
    expect(item).toHaveAttribute("data-disabled");
    await user.click(item);
    expect(h.onExecuteSelected).not.toHaveBeenCalled();
  });

  it("Execute (only selected) dispatches onExecuteSelected with manifest + selection", async () => {
    const user = userEvent.setup();
    const h = setup({ manifestLoaded: true, hasSelection: true });
    await user.click(screen.getByTestId(MENU_ACTION));
    await user.click(await screen.findByTestId(MENU_ACTION_EXECUTE_SELECTED));
    expect(h.onExecuteSelected).toHaveBeenCalledOnce();
  });

  it("View → 中文 dispatches onSetLocale('zh_TW')", async () => {
    const user = userEvent.setup();
    const h = setup({ locale: "en" });
    await user.click(screen.getByTestId(MENU_VIEW));
    await user.click(await screen.findByTestId(MENU_VIEW_LANG_ZH));
    expect(h.onSetLocale).toHaveBeenCalledWith("zh_TW");
  });
});
