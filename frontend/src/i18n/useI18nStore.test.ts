// Unit tests for the REAL useI18nStore actions (initI18n / setLocale).
//
// App.test.tsx stubs setLocale, so the actual persist+refetch logic — the
// heart of the i18n feature — had no coverage (peer review A2/B3). These tests
// mock only the network boundary (the api/client module) and exercise the
// store's real branching: persisted-locale read, fallback paths, and the
// best-effort persistence that must log rather than swallow on failure.

import { beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../api/client";
import { useI18nStore } from "./useI18nStore";

vi.mock("../api/client", () => ({
  getI18n: vi.fn(),
  getSettings: vi.fn(),
  patchSettings: vi.fn(),
}));

const mockGetI18n = vi.mocked(client.getI18n);
const mockGetSettings = vi.mocked(client.getSettings);
const mockPatchSettings = vi.mocked(client.patchSettings);

const ZH_CATALOG = {
  locale: "zh_TW",
  strings: { "web.toolbar.scan": "掃描" },
  available: [
    ["en", "English"],
    ["zh_TW", "繁體中文"],
  ],
} as Awaited<ReturnType<typeof client.getI18n>>;

const EN_CATALOG = {
  locale: "en",
  strings: { "web.toolbar.scan": "Scan" },
  available: [["en", "English"]],
} as Awaited<ReturnType<typeof client.getI18n>>;

function resetStore(): void {
  useI18nStore.setState({ locale: "en", catalog: {} });
}

describe("useI18nStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetStore();
  });

  describe("initI18n", () => {
    it("reads ui.locale from settings and loads that catalog", async () => {
      mockGetSettings.mockResolvedValue({ "ui.locale": "zh_TW" } as never);
      mockGetI18n.mockResolvedValue(ZH_CATALOG);

      await useI18nStore.getState().initI18n();

      expect(mockGetSettings).toHaveBeenCalledOnce();
      expect(mockGetI18n).toHaveBeenCalledWith("zh_TW");
      expect(useI18nStore.getState().locale).toBe("zh_TW");
      expect(useI18nStore.getState().catalog["web.toolbar.scan"]).toBe("掃描");
    });

    it("defaults to en when no ui.locale is persisted", async () => {
      mockGetSettings.mockResolvedValue({} as never);
      mockGetI18n.mockResolvedValue(EN_CATALOG);

      await useI18nStore.getState().initI18n();

      expect(mockGetI18n).toHaveBeenCalledWith("en");
      expect(useI18nStore.getState().locale).toBe("en");
    });

    it("falls back to en when the settings fetch throws", async () => {
      mockGetSettings.mockRejectedValue(new Error("network"));
      mockGetI18n.mockResolvedValue(EN_CATALOG);

      await useI18nStore.getState().initI18n();

      expect(mockGetI18n).toHaveBeenCalledWith("en");
      expect(useI18nStore.getState().locale).toBe("en");
    });

    it("keeps an empty catalog when the catalog fetch throws", async () => {
      mockGetSettings.mockResolvedValue({ "ui.locale": "zh_TW" } as never);
      mockGetI18n.mockRejectedValue(new Error("500"));

      await useI18nStore.getState().initI18n();

      // locale resolved from settings, catalog empty → fallbacks serve English
      expect(useI18nStore.getState().locale).toBe("zh_TW");
      expect(useI18nStore.getState().catalog).toEqual({});
    });
  });

  describe("setLocale", () => {
    it("persists via patchSettings then loads the new catalog", async () => {
      mockPatchSettings.mockResolvedValue({ updated: 1 } as never);
      mockGetI18n.mockResolvedValue(ZH_CATALOG);

      await useI18nStore.getState().setLocale("zh_TW");

      expect(mockPatchSettings).toHaveBeenCalledWith({ "ui.locale": "zh_TW" });
      expect(mockGetI18n).toHaveBeenCalledWith("zh_TW");
      expect(useI18nStore.getState().locale).toBe("zh_TW");
      expect(useI18nStore.getState().catalog["web.toolbar.scan"]).toBe("掃描");
    });

    it("still switches in-session AND logs when persistence fails", async () => {
      mockPatchSettings.mockRejectedValue(new Error("422"));
      mockGetI18n.mockResolvedValue(ZH_CATALOG);
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

      await useI18nStore.getState().setLocale("zh_TW");

      // The switch still applies — persistence is best-effort...
      expect(mockGetI18n).toHaveBeenCalledWith("zh_TW");
      expect(useI18nStore.getState().locale).toBe("zh_TW");
      // ...but the failure is surfaced, not silently swallowed (B3).
      expect(warn).toHaveBeenCalled();
      warn.mockRestore();
    });

    it("flips the locale with an empty catalog when the fetch fails", async () => {
      mockPatchSettings.mockResolvedValue({ updated: 1 } as never);
      mockGetI18n.mockRejectedValue(new Error("network"));
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

      await useI18nStore.getState().setLocale("zh_TW");

      expect(useI18nStore.getState().locale).toBe("zh_TW");
      expect(useI18nStore.getState().catalog).toEqual({});
      expect(warn).toHaveBeenCalled();
      warn.mockRestore();
    });
  });
});
