import { describe, it, expect } from "vitest";
import { ApiError } from "./api";

describe("ApiError", () => {
  it("creates an error with status and detail", () => {
    const error = new ApiError(401, "Not authenticated");
    expect(error.status).toBe(401);
    expect(error.detail).toBe("Not authenticated");
    expect(error.name).toBe("ApiError");
    expect(error.message).toBe("Not authenticated");
  });

  it("is an instance of Error", () => {
    const error = new ApiError(500, "Server error");
    expect(error).toBeInstanceOf(Error);
  });
});
