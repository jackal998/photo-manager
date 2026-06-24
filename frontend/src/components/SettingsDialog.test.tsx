// SettingsDialog tests (#686) — the ui.prune_singletons 3-value control.
//
// Pins that the select round-trips the string enum (not the old boolean) and
// that a legacy/unknown stored value normalizes to "ask".

import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { useAppStore } from "@/store/useAppStore";
import type { AppStore } from "@/store/types";
import { SettingsDialog } from "./SettingsDialog";
import { DLGE_SETTINGS_PRUNE_SELECT, DLGE_SETTINGS_SAVE } from "@/testids";

let saveSettingsMock: ReturnType<typeof vi.fn>;

function seedValues(pruneValue: unknown) {
  act(() => {
    useAppStore.setState((s) => ({
      settings: {
        ...s.settings,
        loading: false,
        values: {
          "sorting.defaults": null,
          "ui.prune_singletons": pruneValue,
          "ui.scan_dialog.autotune_read_knee": null,
        },
      },
    }));
  });
}

beforeEach(() => {
  saveSettingsMock = vi.fn().mockResolvedValue(undefined);
  act(() => {
    useAppStore.setState({
      loadSettings: vi.fn().mockResolvedValue(undefined),
      saveSettings: saveSettingsMock,
    } as Partial<AppStore>);
  });
});

describe("SettingsDialog — prune_singletons 3-value control", () => {
  it("reflects the stored enum value", () => {
    seedValues("always");
    render(<SettingsDialog open onOpenChange={vi.fn()} />);
    expect(screen.getByTestId(DLGE_SETTINGS_PRUNE_SELECT)).toHaveValue("always");
  });

  it("normalizes a legacy boolean / unknown value to 'ask'", () => {
    seedValues(true);
    render(<SettingsDialog open onOpenChange={vi.fn()} />);
    expect(screen.getByTestId(DLGE_SETTINGS_PRUNE_SELECT)).toHaveValue("ask");
  });

  it("saves the chosen enum string (not a boolean)", async () => {
    const user = userEvent.setup();
    seedValues("ask");
    render(<SettingsDialog open onOpenChange={vi.fn()} />);

    await user.selectOptions(
      screen.getByTestId(DLGE_SETTINGS_PRUNE_SELECT),
      "never"
    );
    await user.click(screen.getByTestId(DLGE_SETTINGS_SAVE));

    expect(saveSettingsMock).toHaveBeenCalledTimes(1);
    expect(saveSettingsMock.mock.calls[0][0]["ui.prune_singletons"]).toBe(
      "never"
    );
  });
});
