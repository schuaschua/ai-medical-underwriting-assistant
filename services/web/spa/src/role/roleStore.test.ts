import { describe, expect, it } from "vitest";
import { getRole, ROLE_STORAGE_KEY, setRole } from "./roleStore";

describe("1.3 role store", () => {
  it("keeps the chosen role in the browser's storage", () => {
    setRole("underwriter");

    expect(window.localStorage.getItem(ROLE_STORAGE_KEY)).toBe("underwriter");
    expect(getRole()).toBe("underwriter");
  });

  it("ignores a stored value that is not a demo role", () => {
    window.localStorage.setItem(ROLE_STORAGE_KEY, "admin");

    expect(getRole()).toBeNull();
  });
});
