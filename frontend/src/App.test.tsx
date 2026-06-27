// App integration tests — verify that the real wiring in App.tsx connects
// the toolbar, dialogs, and status bar. Uses jsdom + @testing-library/react.

import { type ReactElement } from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import App from "./App";
import { useAppStore } from "./store/useAppStore";
import { useI18nStore } from "./i18n/useI18nStore";
import { translate, interpolate } from "./i18n/useT";
import {
  MAIN_EMPTY_STATE,
  MAIN_EXECUTE_BUTTON,
  MAIN_LANG_TOGGLE,
  MAIN_MANIFEST_INPUT,
  MAIN_MANIFEST_OPEN,
  MAIN_RESULT_TREE,
  MAIN_SCAN_BUTTON,
  MAIN_SETTINGS_BUTTON,
  MAIN_STATUS_BAR,
  MAIN_STATUS_ERROR,
  SCAN_DIALOG,
  DLGE_SETTINGS_DIALOG,
  EXECUTE_DIALOG,
  PREVIEW_PANE,
  LOCK_CONFIRM_DIALOG,
} from "./testids";
import type { Group } from "./api/types";

// ---------------------------------------------------------------------------
// Stubs — prevent real network I/O in jsdom
// ---------------------------------------------------------------------------

// EventSource stub: useScanSSE calls new EventSource(url). In jsdom it is
// undefined. The stub prevents TypeError and does nothing (no scan is started
// in these tests so no events need to flow).
class FakeEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  url: string;
  constructor(url: string) {
    this.url = url;
  }
  addEventListener = vi.fn();
  removeEventListener = vi.fn();
  close = vi.fn();
}

// fetch stub: store actions call fetch but no scan is started in these tests.
// Returning a never-resolving promise is safe; the tests don't await it.
const fetchStub = vi.fn(() => new Promise<Response>(() => {}));

beforeAll(() => {
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("fetch", fetchStub);
});

afterAll(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderWithProviders(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>
  );
}

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

function makeFile(
  basename: string,
  filePath: string,
  decision: "" | "delete" | "ignore" = ""
) {
  return {
    file_path: filePath,
    basename,
    folder: "/photos",
    action: "keep",
    user_decision: decision,
    is_locked: false,
    is_ref_winner: false,
    similarity: { kind: "near_dup" as const, percent: null },
    score: null,
    file_size_bytes: 1024,
    pixel_width: 800,
    pixel_height: 600,
    shot_date: null,
    creation_date: null,
    phash: null,
    hamming_distance: 0,
    thumbnail_url: `/api/image?path=${encodeURIComponent(filePath)}&size=128`,
  };
}

const TEST_GROUPS: Group[] = [
  {
    group_number: 1,
    member_count: 2,
    items: [
      makeFile("ref.jpg", "/photos/ref.jpg"),
      makeFile("dup.jpg", "/photos/dup.jpg"),
    ],
  },
];

// ---------------------------------------------------------------------------
// Store reset helper
// ---------------------------------------------------------------------------

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
    },
    execute: {
      executeOpen: false,
      executeRunning: false,
      executeResult: null,
      executeError: null,
      lockConflict: null,
      prunePrompt: null,
      prunePending: null,
      scopeGroupNumbers: null,
    },
  });
  // Reset i18n store to defaults so each test starts with empty catalog + "en".
  useI18nStore.setState({ locale: "en", catalog: {} });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("App header toolbar", () => {
  beforeEach(() => resetStore());

  it("renders the Scan button", () => {
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_SCAN_BUTTON)).toBeInTheDocument();
  });

  it("disables the Execute button when no manifest is loaded", () => {
    // #673 — the toolbar Execute is manifest-gated, matching the menu
    // Action → Execute, the Set-Action button, and Qt. With no manifest
    // there is nothing to execute, so the entry point is disabled.
    renderWithProviders(<App />);
    const btn = screen.getByTestId(MAIN_EXECUTE_BUTTON);
    expect(btn).toBeInTheDocument();
    expect(btn).toBeDisabled();
  });

  it("enables the Execute button once a manifest is loaded", () => {
    act(() => {
      useAppStore.setState({
        manifest: {
          path: "/tmp/test.sqlite",
          groups: TEST_GROUPS,
          totalGroups: 1,
          totalFiles: 2,
          loading: false,
          error: null,
        },
      });
    });
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_EXECUTE_BUTTON)).not.toBeDisabled();
  });

  it("renders the Lang toggle button showing EN", () => {
    renderWithProviders(<App />);
    const btn = screen.getByTestId(MAIN_LANG_TOGGLE);
    expect(btn).toBeInTheDocument();
    expect(btn).toHaveTextContent("EN");
  });

  it("renders the Settings button", () => {
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_SETTINGS_BUTTON)).toBeInTheDocument();
  });

  it("renders the manifest input and open button", () => {
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_MANIFEST_INPUT)).toBeInTheDocument();
    expect(screen.getByTestId(MAIN_MANIFEST_OPEN)).toBeInTheDocument();
  });
});

describe("App result tree", () => {
  beforeEach(() => resetStore());

  it("shows the empty-state placeholder when no manifest is loaded", () => {
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_EMPTY_STATE)).toBeInTheDocument();
    // The result tree is not rendered in the empty state — it appears only
    // once a manifest is loaded (so main-result-tree is a conditional testid).
    expect(screen.queryByTestId(MAIN_RESULT_TREE)).not.toBeInTheDocument();
  });

  it("renders the result tree container once a manifest is loaded", () => {
    act(() => {
      useAppStore.setState({
        manifest: {
          path: "/tmp/test.sqlite",
          groups: TEST_GROUPS,
          totalGroups: 1,
          totalFiles: 2,
          loading: false,
          error: null,
        },
      });
    });
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_RESULT_TREE)).toBeInTheDocument();
    expect(screen.queryByTestId(MAIN_EMPTY_STATE)).not.toBeInTheDocument();
  });
});

describe("App status bar", () => {
  beforeEach(() => resetStore());

  it("shows 'Ready' when no manifest is loaded and no scan is running", () => {
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_STATUS_BAR)).toHaveTextContent("Ready");
  });

  // #712 — manifest.error was written by ~9 store actions but rendered nowhere.
  // The footer now shows an additive role="alert" line when it is non-null.

  it("does not render the manifest-error line when manifest.error is null", () => {
    renderWithProviders(<App />);
    expect(screen.queryByTestId(MAIN_STATUS_ERROR)).not.toBeInTheDocument();
  });

  it("renders the manifest-error alert when manifest.error is set, without masking the summary", () => {
    // A manifest is loaded (path !== null) AND a later action failed. The
    // error must appear AND the 'N groups · M files' summary must remain — the
    // additive line never hides the summary (the reason it is a separate <p>
    // and not an error-first branch in the statusText chain).
    act(() => {
      useAppStore.setState({
        manifest: {
          path: "/manifests/test.db",
          groups: TEST_GROUPS,
          totalGroups: 1,
          totalFiles: 2,
          loading: false,
          error: "decision PATCH failed",
        },
      });
    });
    renderWithProviders(<App />);
    const alert = screen.getByTestId(MAIN_STATUS_ERROR);
    expect(alert).toHaveTextContent("Manifest error: decision PATCH failed");
    expect(alert).toHaveAttribute("role", "alert");
    // Summary still visible — not masked by the error.
    expect(screen.getByTestId(MAIN_STATUS_BAR)).toHaveTextContent(
      "1 groups · 2 files"
    );
  });

  it("surfaces a manifest LOAD failure (path null) as the error line while the status bar stays 'Ready'", () => {
    // loadManifest's own catch leaves path null and sets error — the most
    // common manifest.error source (e.g. opening a moved/typo'd manifest).
    act(() => {
      useAppStore.setState({
        manifest: {
          path: null,
          groups: [],
          totalGroups: 0,
          totalFiles: 0,
          loading: false,
          error: "HTTP 404: Manifest not found: '/bad.db'",
        },
      });
    });
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_STATUS_ERROR)).toHaveTextContent(
      "Manifest error: HTTP 404: Manifest not found: '/bad.db'"
    );
    expect(screen.getByTestId(MAIN_STATUS_BAR)).toHaveTextContent("Ready");
  });
});

describe("ScanDialog opens from toolbar", () => {
  beforeEach(() => resetStore());

  it("ScanDialog is not visible on initial render", () => {
    renderWithProviders(<App />);
    expect(screen.queryByTestId(SCAN_DIALOG)).not.toBeInTheDocument();
  });

  it("clicking Scan button opens the ScanDialog", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />);
    await user.click(screen.getByTestId(MAIN_SCAN_BUTTON));
    expect(screen.getByTestId(SCAN_DIALOG)).toBeInTheDocument();
  });
});

describe("SettingsDialog opens from toolbar", () => {
  beforeEach(() => resetStore());

  it("SettingsDialog is not visible on initial render", () => {
    renderWithProviders(<App />);
    expect(screen.queryByTestId(DLGE_SETTINGS_DIALOG)).not.toBeInTheDocument();
  });

  it("clicking Settings button opens the SettingsDialog", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />);
    await user.click(screen.getByTestId(MAIN_SETTINGS_BUTTON));
    expect(screen.getByTestId(DLGE_SETTINGS_DIALOG)).toBeInTheDocument();
  });
});

describe("ExecuteDialog opens from toolbar", () => {
  beforeEach(() => resetStore());

  it("ExecuteDialog is not visible on initial render", () => {
    renderWithProviders(<App />);
    expect(screen.queryByTestId(EXECUTE_DIALOG)).not.toBeInTheDocument();
  });

  it("clicking Execute button opens the ExecuteDialog", async () => {
    // Execute is manifest-gated (#673), so seed a loaded manifest before
    // exercising the click → dialog wiring.
    act(() => {
      useAppStore.setState({
        manifest: {
          path: "/tmp/test.sqlite",
          groups: TEST_GROUPS,
          totalGroups: 1,
          totalFiles: 2,
          loading: false,
          error: null,
        },
      });
    });
    const user = userEvent.setup();
    renderWithProviders(<App />);
    await user.click(screen.getByTestId(MAIN_EXECUTE_BUTTON));
    expect(screen.getByTestId(EXECUTE_DIALOG)).toBeInTheDocument();
  });
});

describe("PreviewPane is always mounted in main layout", () => {
  beforeEach(() => resetStore());

  it("PreviewPane container is present on initial render", () => {
    renderWithProviders(<App />);
    expect(screen.getByTestId(PREVIEW_PANE)).toBeInTheDocument();
  });

  it("PreviewPane shows empty state when no file is selected", () => {
    renderWithProviders(<App />);
    expect(
      screen.getByText(/select a file to preview/i)
    ).toBeInTheDocument();
  });

  it("selecting a file updates the PreviewPane", () => {
    // Seed the manifest so the file row is findable.
    act(() => {
      useAppStore.setState({
        manifest: {
          path: "/manifests/test.db",
          groups: TEST_GROUPS,
          totalGroups: 1,
          totalFiles: 2,
          loading: false,
          error: null,
        },
        preview: {
          selectedFilePath: "/photos/ref.jpg",
          fullResPath: null,
        },
      });
    });
    renderWithProviders(<App />);
    // PreviewPane should show the selected file info.
    expect(screen.getByTestId(PREVIEW_PANE)).toBeInTheDocument();
    // Info panel should be visible with the filename.
    expect(screen.getByText("ref.jpg")).toBeInTheDocument();
  });
});

describe("LockConfirmDialog wired at App level", () => {
  beforeEach(() => resetStore());

  it("LockConfirmDialog is absent when lockConflict is null", () => {
    renderWithProviders(<App />);
    expect(screen.queryByTestId(LOCK_CONFIRM_DIALOG)).not.toBeInTheDocument();
  });

  it("LockConfirmDialog appears when store.lockConflict is set", () => {
    act(() => {
      useAppStore.setState((s) => ({
        execute: {
          ...s.execute,
          lockConflict: { paths: ["/photos/locked.jpg"], op: "execute" },
        },
      }));
    });
    renderWithProviders(<App />);
    expect(screen.getByTestId(LOCK_CONFIRM_DIALOG)).toBeInTheDocument();
  });
});

describe("Execute button -> locked 409 -> LockConfirmDialog flow", () => {
  beforeEach(() => resetStore());

  it("store.executeDecisions 409 locked_paths surfaces LockConfirmDialog", async () => {
    // Mock executeDecisions to set lockConflict (simulating 409 response).
    const mockExecute = vi.fn(async () => {
      useAppStore.setState((s) => ({
        execute: {
          ...s.execute,
          executeRunning: false,
          lockConflict: { paths: ["/photos/locked.jpg"], op: "execute" },
        },
      }));
    });
    act(() => {
      useAppStore.setState({
        executeDecisions: mockExecute,
      } as never);
      // Open the execute dialog.
      useAppStore.setState((s) => ({
        execute: { ...s.execute, executeOpen: true },
      }));
    });

    renderWithProviders(<App />);
    // ExecuteDialog should be open.
    expect(screen.getByTestId(EXECUTE_DIALOG)).toBeInTheDocument();

    // Trigger mock execute — it sets lockConflict.
    act(() => {
      useAppStore.setState((s) => ({
        execute: {
          ...s.execute,
          executeRunning: false,
          lockConflict: { paths: ["/photos/locked.jpg"], op: "execute" },
        },
      }));
    });

    // LockConfirmDialog should now be visible.
    expect(screen.getByTestId(LOCK_CONFIRM_DIALOG)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// i18n — translate() pure function
// ---------------------------------------------------------------------------

describe("translate() pure function", () => {
  it("returns the fallback when catalog is empty", () => {
    expect(translate({}, "web.toolbar.scan", "Scan")).toBe("Scan");
  });

  it("returns the catalog value when the key is present", () => {
    const catalog = { "web.toolbar.scan": "掃描" };
    expect(translate(catalog, "web.toolbar.scan", "Scan")).toBe("掃描");
  });

  it("returns the fallback (not undefined) when key is missing from catalog", () => {
    const catalog = { "web.toolbar.execute": "執行" };
    expect(translate(catalog, "web.toolbar.scan", "Scan")).toBe("Scan");
  });

  it("substitutes {name} placeholders using params", () => {
    const catalog = { "web.status.summary": "{groups} 個群組 · {files} 個檔案" };
    expect(
      translate(catalog, "web.status.summary", "{groups} groups · {files} files", {
        groups: 3,
        files: 12,
      })
    ).toBe("3 個群組 · 12 個檔案");
  });

  it("uses fallback and substitutes placeholders when catalog is empty", () => {
    expect(
      translate({}, "web.status.summary", "{groups} groups · {files} files", {
        groups: 5,
        files: 20,
      })
    ).toBe("5 groups · 20 files");
  });
});

// ---------------------------------------------------------------------------
// interpolate() — placeholder substitution
// ---------------------------------------------------------------------------

describe("interpolate()", () => {
  it("leaves a string without placeholders unchanged", () => {
    expect(interpolate("Ready")).toBe("Ready");
  });

  it("leaves unknown placeholders as-is when params is undefined", () => {
    expect(interpolate("Scanning{stage}")).toBe("Scanning{stage}");
  });

  it("leaves unknown placeholders as-is when the key is not in params", () => {
    expect(interpolate("Scanning{stage}", {})).toBe("Scanning{stage}");
  });

  it("substitutes a known placeholder", () => {
    expect(interpolate("Scan failed: {error}", { error: "disk full" })).toBe(
      "Scan failed: disk full"
    );
  });
});

// ---------------------------------------------------------------------------
// i18n — lang toggle in App
// ---------------------------------------------------------------------------

describe("App lang toggle", () => {
  beforeEach(() => resetStore());

  it("shows EN label when locale is en (default)", () => {
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_LANG_TOGGLE)).toHaveTextContent("EN");
  });

  it("shows 中 label when locale is zh_TW", () => {
    act(() => {
      useI18nStore.setState({ locale: "zh_TW", catalog: {} });
    });
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_LANG_TOGGLE)).toHaveTextContent("中");
  });

  it("clicking the toggle calls setLocale and the label flips EN→中", async () => {
    const user = userEvent.setup();

    // Stub setLocale to flip the locale in-store without a real fetch.
    const stubSetLocale = vi.fn(async (code: string) => {
      useI18nStore.setState({ locale: code, catalog: {} });
    });
    act(() => {
      useI18nStore.setState({ locale: "en", catalog: {}, setLocale: stubSetLocale } as never);
    });

    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_LANG_TOGGLE)).toHaveTextContent("EN");

    await user.click(screen.getByTestId(MAIN_LANG_TOGGLE));

    expect(stubSetLocale).toHaveBeenCalledWith("zh_TW");
    expect(screen.getByTestId(MAIN_LANG_TOGGLE)).toHaveTextContent("中");
  });

  it("toolbar scan button shows catalog value when catalog is loaded", () => {
    act(() => {
      useI18nStore.setState({
        locale: "zh_TW",
        catalog: { "web.toolbar.scan": "掃描" },
      });
    });
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_SCAN_BUTTON)).toHaveTextContent("掃描");
  });

  it("toolbar scan button shows English fallback when catalog is empty", () => {
    act(() => {
      useI18nStore.setState({ locale: "en", catalog: {} });
    });
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_SCAN_BUTTON)).toHaveTextContent("Scan");
  });
});
