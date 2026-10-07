import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ROLE_STORAGE_KEY } from "./role/roleStore";
import { errorBody, fakeServer, json } from "./test/server";

function openApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

function navigationLinks(): string[] {
  const navigation = screen.getByRole("navigation", { name: "Screens" });
  return within(navigation)
    .getAllByRole("link")
    .map((link) => link.getAttribute("href") ?? "");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("1.3 role switcher", () => {
  it("asks for a role on a first visit, before any role screen or API call", () => {
    const server = fakeServer();

    openApp("/underwriter");

    expect(
      screen.getByRole("heading", { name: "Who are you acting as?" }),
    ).toBeVisible();
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByText(/home/i)).not.toBeInTheDocument();
    expect(server.calls).toEqual([]);
  });

  it("shows the underwriter's screens and sends the underwriter role once chosen", async () => {
    const server = fakeServer();
    const user = userEvent.setup();
    openApp();

    await user.click(
      screen.getByRole("button", { name: "Continue as Underwriter" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Underwriter home" }),
    ).toBeVisible();
    expect(
      await screen.findByText("The server sees you as: Underwriter."),
    ).toBeVisible();
    expect(navigationLinks()).toEqual([
      "/underwriter",
      "/underwriter/triage",
      "/underwriter/cases",
      "/underwriter/audit",
    ]);
    expect(server.calls.length).toBeGreaterThan(0);
    expect(server.calls.every((call) => call.role === "underwriter")).toBe(
      true,
    );
  });

  it("switches role: later calls carry the new role and navigation follows", async () => {
    const server = fakeServer();
    const user = userEvent.setup();
    openApp();
    await user.click(
      screen.getByRole("button", { name: "Continue as Customer" }),
    );
    await screen.findByText("The server sees you as: Customer.");
    expect(navigationLinks()).toEqual(["/customer", "/customer/upload"]);
    const callsAsCustomer = server.calls.length;

    await user.click(screen.getByRole("radio", { name: "Underwriter" }));

    expect(
      await screen.findByText("The server sees you as: Underwriter."),
    ).toBeVisible();
    expect(navigationLinks()).toEqual([
      "/underwriter",
      "/underwriter/triage",
      "/underwriter/cases",
      "/underwriter/audit",
    ]);
    expect(
      screen.queryByRole("heading", { name: "Customer home" }),
    ).not.toBeInTheDocument();
    const later = server.calls.slice(callsAsCustomer);
    expect(later.length).toBeGreaterThan(0);
    expect(later.every((call) => call.role === "underwriter")).toBe(true);
    expect(screen.getByRole("radio", { name: "Underwriter" })).toBeChecked();
  });

  it("keeps the chosen role across a reload", async () => {
    fakeServer();
    const user = userEvent.setup();
    const firstLoad = openApp();
    await user.click(
      screen.getByRole("button", { name: "Continue as Underwriter" }),
    );
    await screen.findByRole("heading", { name: "Underwriter home" });
    expect(window.localStorage.getItem(ROLE_STORAGE_KEY)).toBe("underwriter");

    // A reload: the page is built again from nothing but the browser's storage.
    firstLoad.unmount();
    const server = fakeServer();
    openApp();

    expect(
      await screen.findByRole("heading", { name: "Underwriter home" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Who are you acting as?" }),
    ).not.toBeInTheDocument();
    await screen.findByText("The server sees you as: Underwriter.");
    expect(server.calls.every((call) => call.role === "underwriter")).toBe(
      true,
    );
  });

  it("does not show another role's screen on a deep link", async () => {
    fakeServer();
    window.localStorage.setItem(ROLE_STORAGE_KEY, "customer");

    openApp("/underwriter");

    expect(
      await screen.findByRole("heading", { name: "Page not found" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Underwriter home" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Go to your home screen" }),
    ).toHaveAttribute("href", "/customer");
  });

  it("shows a plain message and the trace reference when a call fails", async () => {
    const traceId = "0af7651916cd43dd8448eb211c80319c";
    fakeServer(() =>
      json(500, errorBody("internal_error", "<b>server text</b>", traceId)),
    );
    window.localStorage.setItem(ROLE_STORAGE_KEY, "customer");

    openApp("/customer");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).toHaveTextContent(`Reference: ${traceId}`);
    // Server text is never rendered as HTML.
    expect(alert.querySelector("b")).toBeNull();
  });

  it("goes to the role's home when the role is chosen on a deep link", async () => {
    fakeServer();
    const user = userEvent.setup();
    openApp("/triage");

    await user.click(
      screen.getByRole("button", { name: "Continue as Underwriter" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Underwriter home" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Page not found" }),
    ).not.toBeInTheDocument();
  });

  it("follows a role switch made in another tab", async () => {
    const server = fakeServer();
    window.localStorage.setItem(ROLE_STORAGE_KEY, "customer");
    openApp("/customer");
    await screen.findByText("The server sees you as: Customer.");
    const callsAsCustomer = server.calls.length;

    // What the browser does in this tab when another tab stores a new role.
    act(() => {
      window.localStorage.setItem(ROLE_STORAGE_KEY, "underwriter");
      window.dispatchEvent(
        new StorageEvent("storage", { key: ROLE_STORAGE_KEY }),
      );
    });

    expect(
      await screen.findByRole("heading", { name: "Underwriter home" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "Page not found" }),
    ).not.toBeInTheDocument();
    expect(navigationLinks()).toEqual([
      "/underwriter",
      "/underwriter/triage",
      "/underwriter/cases",
      "/underwriter/audit",
    ]);
    await screen.findByText("The server sees you as: Underwriter.");
    expect(
      server.calls
        .slice(callsAsCustomer)
        .every((call) => call.role === "underwriter"),
    ).toBe(true);
  });

  it("shows the message for the error's code, without a reference when there is no trace", async () => {
    fakeServer(() =>
      json(
        403,
        errorBody("role_not_allowed", "server wording", "0".repeat(32)),
      ),
    );
    window.localStorage.setItem(ROLE_STORAGE_KEY, "customer");

    openApp("/customer");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This action is not open to your role.");
    expect(alert).not.toHaveTextContent("server wording");
    expect(alert).not.toHaveTextContent("Reference");
  });

  it.each(["constructor", "toString", "__proto__"])(
    "shows the general message for the unknown code %s",
    async (code) => {
      fakeServer(() => json(500, errorBody(code, "server wording")));
      window.localStorage.setItem(ROLE_STORAGE_KEY, "customer");

      openApp("/customer");

      expect(await screen.findByRole("alert")).toHaveTextContent(
        /^Something went wrong\. Please try again\.$/,
      );
    },
  );

  it("shows a plain message when a success carries no JSON", async () => {
    fakeServer(() => new Response(null, { status: 204 }));
    window.localStorage.setItem(ROLE_STORAGE_KEY, "customer");

    openApp("/customer");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );
    expect(
      screen.getByRole("heading", { name: "Customer home" }),
    ).toBeVisible();
  });

  it("says so when the server cannot be reached", async () => {
    fakeServer(() => Promise.reject(new TypeError("Failed to fetch")));
    window.localStorage.setItem(ROLE_STORAGE_KEY, "underwriter");

    openApp("/underwriter");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The server could not be reached. Please try again.",
    );
  });
});
