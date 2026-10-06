import { describe, expect, it, vi } from "vitest";
import { getRole, ROLE_STORAGE_KEY, setRole } from "./roleStore";

describe("1.3 role store", () => {
  it("has no role on a first visit", () => {
    expect(getRole()).toBeNull();
  });

  it("keeps the chosen role in the browser's storage", () => {
    setRole("underwriter");

    expect(window.localStorage.getItem(ROLE_STORAGE_KEY)).toBe("underwriter");
    expect(getRole()).toBe("underwriter");
  });

  it("ignores a stored value that is not a demo role", () => {
    window.localStorage.setItem(ROLE_STORAGE_KEY, "admin");

    expect(getRole()).toBeNull();
  });

  it("still holds the role for the page view when storage is refused", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });

    setRole("customer");

    expect(getRole()).toBe("customer");
  });
});
