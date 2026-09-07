// Zustand store for locale + translation catalog.
// Follows the same immer+create pattern as useAppStore.
//
// Actions:
//   initI18n()       — called once on boot; reads ui.locale from settings,
//                       fetches the catalog, populates store.
//   setLocale(code)  — persist locale via PATCH /api/settings, re-fetch catalog.

import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

import { getI18n, getSettings, patchSettings } from "../api/client";

// ---------------------------------------------------------------------------
// State + action types
// ---------------------------------------------------------------------------

export interface I18nState {
  locale: string;
  catalog: Record<string, string>;
}

export interface I18nActions {
  initI18n(): Promise<void>;
  setLocale(code: string): Promise<void>;
}

export type I18nStore = I18nState & I18nActions;

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useI18nStore = create<I18nStore>()(
  immer((set) => ({
    locale: "en",
    catalog: {},

    async initI18n() {
      let locale = "en";
      try {
        const settings = await getSettings();
        const settingsLocale = settings["ui.locale"];
        if (typeof settingsLocale === "string" && settingsLocale.length > 0) {
          locale = settingsLocale;
        }
      } catch {
        // Fall back to "en" if settings can't be fetched.
      }

      try {
        const response = await getI18n(locale);
        set((state) => {
          state.locale = response.locale;
          state.catalog = response.strings;
        });
      } catch {
        // Fall back: keep default "en" with empty catalog (fallbacks serve en text).
        set((state) => {
          state.locale = locale;
          state.catalog = {};
        });
      }
    },

    async setLocale(code: string) {
      // Persist to settings first — best-effort. A failure here (e.g. a 422
      // on disk write) does NOT block the in-session switch, but it must NOT
      // be swallowed silently: an unlogged failure means the next reload
      // quietly forgets the choice with no trace (peer review B3).
      try {
        await patchSettings({ "ui.locale": code });
      } catch (err) {
        console.warn("Failed to persist ui.locale; switch is session-only:", err);
      }

      try {
        const response = await getI18n(code);
        set((state) => {
          state.locale = response.locale;
          state.catalog = response.strings;
        });
      } catch (err) {
        // If the locale fetch fails, flip the locale code with an empty catalog
        // (fallbacks render English) and surface the failure for traceability.
        console.warn("Failed to load catalog for locale", code, err);
        set((state) => {
          state.locale = code;
          state.catalog = {};
        });
      }
    },
  }))
);
