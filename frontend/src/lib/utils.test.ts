import { describe, it, expect } from "vitest";
import { cn } from "./utils";

describe("cn()", () => {
  it("merges duplicate Tailwind classes, keeping the last one", () => {
    // tailwind-merge deduplicates conflicting utility classes
    const result = cn("px-2", "px-4");
    expect(result).toBe("px-4");
  });

  it("handles conditional class values (falsy values are ignored)", () => {
    const falsyFlag: boolean = false;
    const result = cn("base", falsyFlag && "hidden", "text-sm");
    expect(result).toBe("base text-sm");
  });

  it("handles undefined and null values gracefully", () => {
    const result = cn("flex", undefined, null, "gap-2");
    expect(result).toBe("flex gap-2");
  });

  it("returns an empty string when called with no arguments", () => {
    expect(cn()).toBe("");
  });

  it("merges multiple class strings into one", () => {
    const result = cn("flex", "items-center", "justify-between");
    expect(result).toBe("flex items-center justify-between");
  });

  it("handles object syntax for conditional classes", () => {
    const isActive = true;
    const result = cn("btn", { "btn-active": isActive, "btn-disabled": false });
    expect(result).toContain("btn");
    expect(result).toContain("btn-active");
    expect(result).not.toContain("btn-disabled");
  });
});
