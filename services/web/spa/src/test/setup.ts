import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { clearSessionCases } from "../cases/sessionCases";
import { forgetUploadKey } from "../cases/uploadKey";
import { clearRole } from "../role/roleStore";
import { setStandInPageCount } from "./pdfStandIn";

// The PDF renderer needs a canvas and a worker, which jsdom has not: every
// test gets the stand-in in its place.
vi.mock("react-pdf", () => import("./pdfStandIn"));

afterEach(() => {
  cleanup();
  clearRole();
  clearSessionCases();
  forgetUploadKey();
  setStandInPageCount(3);
});
