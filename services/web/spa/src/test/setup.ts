import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { clearSessionCases } from "../cases/sessionCases";
import { clearRole } from "../role/roleStore";

afterEach(() => {
  cleanup();
  clearRole();
  clearSessionCases();
});
