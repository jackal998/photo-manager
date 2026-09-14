// Copy audit S2 — the status bar used to render the backend's developer text
// verbatim ("HTTP 422: Execute failed: [WinError 32] …"), in English, in both
// locales. These tests pin the two things that matters to a user: a failure
// always produces a sentence they can read, and the raw text is never lost.

import { describe, it, expect, vi } from "vitest";

import { describeApiError } from "./apiErrorText";

/** Identity translator: returns the English fallback, like a cold catalog. */
const en = (_key: string, fallback: string, params?: Record<string, string | number>) => {
  if (params === undefined) return fallback;
  return fallback.replace(/\{(\w+)\}/g, (m, k: string) =>
    params[k] !== undefined ? String(params[k]) : m
  );
};

describe("describeApiError", () => {
  it("maps a 409 locked_paths conflict onto its own sentence with no detail", () => {
    const { message, detail } = describeApiError("409 Conflict: locked_paths", en);
    expect(message).toBe("Some of the rows in scope are locked.");
    expect(detail).toBeNull();
  });

  it("maps a 409 execute_already_running conflict onto the busy sentence", () => {
    const { message } = describeApiError("409 Conflict: execute_already_running", en);
    expect(message).toBe("An action is already running — wait for it to finish.");
  });

  it("splits a route's '<verb> failed: {exc}' detail into sentence + detail", () => {
    // The real shape raised by app/web/routes/execute.py.
    const { message, detail } = describeApiError(
      "HTTP 422: Execute failed: [WinError 32] The process cannot access the file",
      en
    );
    expect(message).toBe("The action could not be completed.");
    expect(detail).toBe("[WinError 32] The process cannot access the file");
  });

  it("recognises the reveal failure raised by Open folder", () => {
    const { message, detail } = describeApiError(
      "HTTP 500: Failed to launch Explorer: [WinError 2]",
      en
    );
    expect(message).toBe("The file manager could not be opened.");
    expect(detail).toBe("[WinError 2]");
  });

  it("maps a 404 onto the not-found sentence, keeping the server's text", () => {
    const { message, detail } = describeApiError(
      "HTTP 404: Manifest not found: '/bad.db'",
      en
    );
    expect(message).toBe("The file or manifest could not be found.");
    expect(detail).toBe("Manifest not found: '/bad.db'");
  });

  it("never swallows an unrecognised string — it becomes the detail", () => {
    // The failure mode this guards: a new backend shape silently rendering
    // as an EMPTY alert. It must always be readable, even unrecognised.
    const { message, detail } = describeApiError("something nobody mapped", en);
    expect(message).toBe("The request could not be completed.");
    expect(detail).toBe("something nobody mapped");
  });

  it("renders the catalog's translation, not the English fallback", () => {
    const zh = vi.fn(() => "無法完成這個動作。");
    expect(describeApiError("HTTP 422: Execute failed: boom", zh).message).toBe(
      "無法完成這個動作。"
    );
    expect(zh).toHaveBeenCalledWith(
      "web.error.execute_failed",
      "The action could not be completed."
    );
  });

  it("returns a null detail rather than an empty one when there is no text left", () => {
    const { detail } = describeApiError("HTTP 422: Save failed: ", en);
    expect(detail).toBeNull();
  });
});
