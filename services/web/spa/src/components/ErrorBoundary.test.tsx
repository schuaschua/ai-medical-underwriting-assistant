import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

function Broken(): never {
  throw new Error("render failed");
}

describe("1.3 error boundary", () => {
  it("shows a plain message when a screen fails to render", () => {
    // React reports the caught error on the console; keep the test output clean.
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <>
        <p>outside</p>
        <ErrorBoundary>
          <Broken />
        </ErrorBoundary>
      </>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "This screen could not be shown. Reload the page to try again.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("render failed");
    // What is outside the boundary stays on the page.
    expect(screen.getByText("outside")).toBeVisible();
  });
});
