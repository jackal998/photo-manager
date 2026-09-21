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

// Copy audit ST1 + ST2 — the visible labels carried the raw settings-file
// keys ("Prune singletons (ui.prune_singletons)"), and the JSON textarea's
// placeholder was the literal word "null", which reads as a bug.
describe("SettingsDialog — labels are copy, not config keys", () => {
  beforeEach(() => {
    seedValues("ask");
  });

  it("shows no raw config key in any visible label", () => {
    // Radix portals the dialog outside the render container, so the visible
    // copy lives on document.body.
    render(<SettingsDialog open onOpenChange={vi.fn()} />);
    const text = document.body.textContent ?? "";
    expect(text).toContain("Prune singletons");
    expect(text).toContain("Sorting defaults");
    expect(text).not.toContain("ui.prune_singletons");
    expect(text).not.toContain("ui.scan_dialog.autotune_read_knee");
    expect(text).not.toContain("sorting.defaults");
  });

  it("keeps each config key discoverable in the row's hover description", () => {
    render(<SettingsDialog open onOpenChange={vi.fn()} />);
    const withKey = Array.from(document.querySelectorAll("[title]")).map(
      (el) => el.getAttribute("title") ?? ""
    );
    expect(withKey.some((v) => v.includes("ui.prune_singletons"))).toBe(true);
    expect(
      withKey.some((v) => v.includes("ui.scan_dialog.autotune_read_knee"))
    ).toBe(true);
    expect(withKey.some((v) => v.includes("sorting.defaults"))).toBe(true);
  });

  it("uses the same wording as the Scan dialog for the auto-tune toggle", () => {
    render(<SettingsDialog open onOpenChange={vi.fn()} />);
    expect(document.body.textContent).toContain(
      "Auto-tune reader concurrency (experimental)"
    );
  });

  it("hints the JSON field with an empty array, not the word 'null'", () => {
    render(<SettingsDialog open onOpenChange={vi.fn()} />);
    const textarea = document.getElementById("settings-sorting-defaults");
    expect(textarea).toHaveAttribute("placeholder", "[]");
  });
});
