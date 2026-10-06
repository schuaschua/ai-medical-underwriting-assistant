import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { clearRole } from "../role/roleStore";

afterEach(() => {
  cleanup();
  clearRole();
});
