/**
 * features.ts — /health feature-probe semantics.
 *
 * These are the guarantees every org UI gate depends on, so each case fails
 * loudly if the probe changes shape:
 *   - true only when /health reports organizations_enabled === true
 *   - false when the field is absent or false
 *   - false when the probe throws (fail closed = single-tenant UI)
 *   - the body is still read on a 503, because /health answers 503 whenever a
 *     dependency is degraded and keeps carrying the flag
 *   - one fetch per page load; the settled value is readable synchronously
 *
 * Strategy: the probe caches at module scope, so every case resets the module
 * registry and re-imports to get a fresh cache.
 */

import { describe, it, expect, vi, afterEach } from "vitest";

type FeaturesModule = typeof import("./features");

async function freshModule(): Promise<FeaturesModule> {
  vi.resetModules();
  return import("./features");
}

function healthResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("organizationsEnabled()", () => {
  it("is true when /health reports the flag on", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => healthResponse(200, { organizations_enabled: true })),
    );
    const features = await freshModule();
    await expect(features.organizationsEnabled()).resolves.toBe(true);
  });

  it("is false when the flag is off", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => healthResponse(200, { organizations_enabled: false })),
    );
    const features = await freshModule();
    await expect(features.organizationsEnabled()).resolves.toBe(false);
  });

  it("is false when the field is missing (older backend)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => healthResponse(200, { database: "ok" })),
    );
    const features = await freshModule();
    await expect(features.organizationsEnabled()).resolves.toBe(false);
  });

  it("is false when the probe throws", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const features = await freshModule();
    await expect(features.organizationsEnabled()).resolves.toBe(false);
  });

  it("reads the flag out of a 503 body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        healthResponse(503, {
          redis: "error: connection refused",
          organizations_enabled: true,
        }),
      ),
    );
    const features = await freshModule();
    await expect(features.organizationsEnabled()).resolves.toBe(true);
  });

  it("probes once and then answers synchronously", async () => {
    const fetchMock = vi.fn(async () =>
      healthResponse(200, { organizations_enabled: true }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const features = await freshModule();

    expect(features.organizationsEnabledSync()).toBeNull();
    await features.organizationsEnabled();
    await features.organizationsEnabled();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(features.organizationsEnabledSync()).toBe(true);
  });
});
