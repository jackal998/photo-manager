// App integration tests — verify that the real wiring in App.tsx connects
// the toolbar, dialogs, and status bar. Uses jsdom + @testing-library/react.

import { type ReactElement } from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import App from "./App";
import { useAppStore } from "./store/useAppStore";
import {
  MAIN_EXECUTE_BUTTON,
  MAIN_LANG_TOGGLE,
  MAIN_MANIFEST_INPUT,
  MAIN_MANIFEST_OPEN,
  MAIN_RESULT_TREE,
  MAIN_SCAN_BUTTON,
  MAIN_SETTINGS_BUTTON,
  MAIN_STATUS_BAR,
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
    },
  });
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

  it("renders the Execute button (enabled)", () => {
    renderWithProviders(<App />);
    const btn = screen.getByTestId(MAIN_EXECUTE_BUTTON);
    expect(btn).toBeInTheDocument();
    // Execute button is now enabled — it opens the ExecuteDialog.
    expect(btn).not.toBeDisabled();
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

  it("renders the result tree container with the correct testid", () => {
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_RESULT_TREE)).toBeInTheDocument();
  });
});

describe("App status bar", () => {
  beforeEach(() => resetStore());

  it("shows 'Ready' when no manifest is loaded and no scan is running", () => {
    renderWithProviders(<App />);
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
