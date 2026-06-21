// App integration tests — verify that the real wiring in App.tsx connects
// the toolbar, dialogs, and status bar. Uses jsdom + @testing-library/react.

import { type ReactElement } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeAll, afterAll } from "vitest";
import App from "./App";
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
} from "./testids";

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
// Tests
// ---------------------------------------------------------------------------

describe("App header toolbar", () => {
  it("renders the Scan button", () => {
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_SCAN_BUTTON)).toBeInTheDocument();
  });

  it("renders the Execute button (disabled, coming-later)", () => {
    renderWithProviders(<App />);
    const btn = screen.getByTestId(MAIN_EXECUTE_BUTTON);
    expect(btn).toBeInTheDocument();
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute("title", "Coming in a later phase");
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
  it("renders the result tree container with the correct testid", () => {
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_RESULT_TREE)).toBeInTheDocument();
  });
});

describe("App status bar", () => {
  it("shows 'Ready' when no manifest is loaded and no scan is running", () => {
    renderWithProviders(<App />);
    expect(screen.getByTestId(MAIN_STATUS_BAR)).toHaveTextContent("Ready");
  });
});

describe("ScanDialog opens from toolbar", () => {
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
