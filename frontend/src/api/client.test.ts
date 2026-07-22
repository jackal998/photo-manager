import { afterEach, describe, expect, it, vi } from "vitest";

import { browseFs } from "./client";

/** Stub global fetch to return a non-ok Response whose JSON body is `body`. */
function mockFetch(status: number, body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        ({
          ok: false,
          status,
          statusText: "Error",
          json: async () => body,
        }) as unknown as Response,
    ),
  );
}

describe("checkResponse error detail (#795)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("surfaces the message from an object-shaped detail, not a raw JSON blob", async () => {
    mockFetch(403, {
      detail: {
        code: "permission_denied",
        message: "reveal only allowed from localhost",
      },
    });
    const err = await browseFs("/x").then(
      () => null,
      (e: unknown) => e as Error,
    );
    expect(err).toBeInstanceOf(Error);
    expect(err!.message).toContain("reveal only allowed from localhost");
    // The old code JSON.stringify'd the object — the raw blob must not leak.
    expect(err!.message).not.toContain("{");
  });

  it("still uses a plain string detail unchanged", async () => {
    mockFetch(400, { detail: "path must not be empty" });
    await expect(browseFs("")).rejects.toThrow("path must not be empty");
  });

  it("falls back to JSON for an object detail with no message field", async () => {
    mockFetch(409, { detail: { code: "locked_paths", locked_paths: ["/a"] } });
    await expect(browseFs("/x")).rejects.toThrow("locked_paths");
  });
});
