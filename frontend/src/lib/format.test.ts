import { describe, it, expect, vi, afterEach } from "vitest";
import { relativeDate } from "./format";

describe("relativeDate()", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns 'just now' for timestamps less than 60 seconds ago", () => {
    vi.useFakeTimers();
    const now = new Date("2026-04-10T12:00:00Z");
    vi.setSystemTime(now);

    const thirtySecondsAgo = new Date("2026-04-10T11:59:35Z").toISOString();
    expect(relativeDate(thirtySecondsAgo)).toBe("just now");
  });

  it("returns minutes ago for timestamps between 1 and 59 minutes ago", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-10T12:00:00Z"));

    const fifteenMinutesAgo = new Date("2026-04-10T11:45:00Z").toISOString();
    expect(relativeDate(fifteenMinutesAgo)).toBe("15m ago");
  });

  it("returns hours ago for timestamps between 1 and 23 hours ago", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-10T12:00:00Z"));

    const threeHoursAgo = new Date("2026-04-10T09:00:00Z").toISOString();
    expect(relativeDate(threeHoursAgo)).toBe("3h ago");
  });

  it("returns days ago for timestamps between 1 and 29 days ago", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-10T12:00:00Z"));

    const fiveDaysAgo = new Date("2026-04-05T12:00:00Z").toISOString();
    expect(relativeDate(fiveDaysAgo)).toBe("5d ago");
  });

  it("falls back to locale date string for timestamps older than 30 days", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-10T12:00:00Z"));

    const oldDate = "2026-01-01T00:00:00Z";
    const result = relativeDate(oldDate);
    // Should be a locale date string, not a relative format
    expect(result).not.toMatch(/ago$/);
    expect(result).not.toBe("just now");
    // Should be a non-empty string representing the date
    expect(result.length).toBeGreaterThan(0);
  });
});
