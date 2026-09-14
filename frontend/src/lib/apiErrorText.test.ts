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

  // Round-2 review finding: a body that already carries a human-readable
  // sentence must NEVER be demoted to the faint technical line. Reviewer's
  // scenario: a non-Windows host right-clicks Open folder, gets 501
  // platform_unsupported, and the only actionable words end up in
  // `text-xs text-ink-faint` — strictly worse than before the PR.
  describe("code-carrying bodies keep their meaning in the primary line", () => {
    it("maps the flattened platform_unsupported sentence onto its own key", () => {
      const { message, detail } = describeApiError(
        "HTTP 501: reveal only supported on Windows",
        en
      );
      expect(message).toBe("That action is not available on this operating system.");
      expect(detail).toBe("reveal only supported on Windows");
    });

    it("maps permission_denied from either route that raises it", () => {
      expect(
        describeApiError("HTTP 403: reveal only allowed from localhost", en).message
      ).toBe("This app is not allowed to reach that location.");
      expect(
        describeApiError("HTTP 400: path is outside all allowed roots", en).message
      ).toBe("This app is not allowed to reach that location.");
    });

    it("maps bad_request, including the malformed-path variant", () => {
      expect(describeApiError("HTTP 400: path must not be empty", en).message).toBe(
        "That path is not valid."
      );
      expect(
        describeApiError("HTTP 400: malformed path: embedded null byte", en).message
      ).toBe("That path is not valid.");
    });

    it("reads the code out of a JSON-stringified dict body (invalid_pattern)", () => {
      // action.py:86 uses `detail` as its second key, so checkResponse cannot
      // flatten it and JSON.stringify's the whole dict — the user used to be
      // shown that raw blob.
      const { message, detail } = describeApiError(
        'HTTP 400: {"code":"invalid_pattern","detail":"unterminated character set at position 3"}',
        en
      );
      expect(message).toBe("That pattern is not a valid regular expression.");
      expect(detail).toBe("unterminated character set at position 3");
    });

    it("reads a {code, message} dict body the same way", () => {
      const { message, detail } = describeApiError(
        'HTTP 403: {"code":"permission_denied","message":"path is outside all allowed roots"}',
        en
      );
      expect(message).toBe("This app is not allowed to reach that location.");
      expect(detail).toBe("path is outside all allowed roots");
    });

    it("uses the catalog translation for a mapped code", () => {
      const zh = vi.fn(() => "此作業系統不支援這個動作。");
      expect(
        describeApiError("HTTP 501: reveal only supported on Windows", zh).message
      ).toBe("此作業系統不支援這個動作。");
      expect(zh).toHaveBeenCalledWith(
        "web.error.platform_unsupported",
        "That action is not available on this operating system."
      );
    });

    it("shows an UNMAPPED server sentence as the primary line, not the detail", () => {
      // The regression guard: a future backend message with no mapping must
      // still lead with the server's own words (pre-PR behaviour), never with
      // the generic "The request could not be completed."
      const { message, detail } = describeApiError(
        "HTTP 409: the widget refused to widget",
        en
      );
      expect(message).toBe("the widget refused to widget");
      expect(detail).toBeNull();
    });

    it("falls back to the server text when the code itself is unknown", () => {
      const { message, detail } = describeApiError(
        'HTTP 418: {"code":"teapot","message":"short and stout"}',
        en
      );
      expect(message).toBe("short and stout");
      expect(detail).toBeNull();
    });
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
