import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { CASE_LIST_PATH, CASE_LIST_POLL_MS } from "../cases/caseList";
import { backoffMs } from "../polling/backoff";
import { ROLE_STORAGE_KEY } from "../role/roleStore";
import {
  caseProgress,
  caseSummary,
  errorBody,
  fakeServer,
  json,
  type RecordedCall,
} from "../test/server";

const NEWEST = "019a0000-0000-7000-8000-000000030000";
const WAITING = "019a0000-0000-7000-8000-000000020000";
const OLDEST = "019a0000-0000-7000-8000-000000010000";
const TRACE_ID = "0af7651916cd43dd8448eb211c80319c";
const LIST_PATH = "/api/cases";

type Summary = ReturnType<typeof caseSummary>;

/** A stand-in for the server that holds the list; it can change while the screen is open. */
function listServer(cases: Summary[], hasMore = false) {
  const held = { cases, hasMore };
  const server = fakeServer((call) => {
    if (call.path === LIST_PATH && call.method === "GET") {
      return call.role === "underwriter"
        ? json(200, { cases: held.cases, has_more: held.hasMore })
        : undefined;
    }
    const listed = held.cases.find(
      (candidate) => call.path === `/api/cases/${candidate.case_id}/progress`,
    );
    if (listed !== undefined) {
      return json(200, caseProgress(listed.case_id, listed.case_status));
    }
    const trailOf = held.cases.find(
      (candidate) => call.path === `/api/cases/${candidate.case_id}/audit`,
    );
    if (trailOf !== undefined && call.role === "underwriter") {
      return json(200, {
        case_id: trailOf.case_id,
        events: [],
        has_more: false,
      });
    }
    return undefined;
  });
  return { ...server, held };
}

function openList(role = "underwriter") {
  window.localStorage.setItem(ROLE_STORAGE_KEY, role);
  return render(
    <MemoryRouter initialEntries={[CASE_LIST_PATH]}>
      <App />
    </MemoryRouter>,
  );
}

/** The rows of the list, each as the text of its cells, the case id first. */
async function rows(): Promise<string[][]> {
  const table = await screen.findByRole("table", {
    name: "Cases, newest first",
  });
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) => [
      within(row).getByRole("rowheader").textContent ?? "",
      ...within(row)
        .getAllByRole("cell")
        .map((cell) => cell.textContent ?? ""),
    ]);
}

function reads(server: { calls: RecordedCall[] }): number {
  return server.calls.filter(
    (call) => call.path === LIST_PATH && call.method === "GET",
  ).length;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("1.13 the underwriter's list of cases", () => {
  it("lists the cases in the server's order, with status, start, page count and waiting count", async () => {
    const server = listServer([
      caseSummary(NEWEST, "running", 0, 0, "2026-10-07T09:02:00Z"),
      caseSummary(WAITING, "awaiting_human", 6, 3, "2026-10-07T09:01:00Z"),
      caseSummary(OLDEST, "completed", 3, 0, "2026-10-07T09:00:00Z"),
    ]);

    openList();

    const listed = await rows();
    // The start is shown in the viewer's own time; the rest as the server said.
    expect(
      listed.map((cells) => [...cells.slice(0, 2), ...cells.slice(3)]),
    ).toEqual([
      [NEWEST, "Running", "0", "0", "Result", "Audit trail"],
      [WAITING, "Waiting for a decision", "6", "3", "Result", "Audit trail"],
      [OLDEST, "Completed", "3", "0", "Result", "Audit trail"],
    ]);
    expect(
      screen.getAllByRole("columnheader").map((header) => header.textContent),
    ).toEqual([
      "Case",
      "Status",
      "Started",
      "Pages",
      "Pages waiting for a person",
      "Result",
      "Audit trail",
    ]);
    // Read as the underwriter, through the one client module.
    expect(reads(server)).toBeGreaterThan(0);
    expect(
      server.calls
        .filter((call) => call.path === LIST_PATH)
        .every((call) => call.role === "underwriter"),
    ).toBe(true);
  });

  it("shows when a case was started in the viewer's local time, with the UTC time as its title", async () => {
    listServer([caseSummary(OLDEST, "failed", 0, 0, "2026-10-07T09:00:05Z")]);

    openList();
    await rows();

    const started = document.querySelector("time");
    expect(started).toHaveAttribute("datetime", "2026-10-07T09:00:05Z");
    expect(started).toHaveAttribute("title", "2026-10-07 09:00:05 UTC");
    expect(started).toHaveTextContent(
      new Date("2026-10-07T09:00:05Z").toLocaleString(),
    );
  });

  it("opens the audit trail of the case whose row is followed", async () => {
    const server = listServer([
      caseSummary(WAITING, "awaiting_human", 6, 3),
      caseSummary(OLDEST, "completed", 3, 0),
    ]);
    openList();
    await rows();

    const link = screen.getByRole("link", {
      name: `Audit trail of case ${OLDEST}`,
    });
    expect(link).toHaveAttribute("href", `/underwriter/audit?case=${OLDEST}`);
    await userEvent.click(link);

    expect(
      await screen.findByRole("heading", { name: "Audit trail" }),
    ).toBeVisible();
    await waitFor(() =>
      expect(
        server.calls.some((call) => call.path === `/api/cases/${OLDEST}/audit`),
      ).toBe(true),
    );
    // A finished case is found this way: nothing of the other case was read.
    expect(
      server.calls.some((call) => call.path.includes(`${WAITING}/audit`)),
    ).toBe(false);
  });

  it("works out no order, status or count in the browser", async () => {
    // Out of order, and with counts no rule of the SPA would give: shown as sent.
    listServer([
      caseSummary(OLDEST, "completed", 2, 2, "2026-10-07T09:00:00Z"),
      caseSummary(NEWEST, "running", 5, 0, "2026-10-07T09:02:00Z"),
    ]);

    openList();

    const listed = await rows();
    expect(
      listed.map((cells) => [cells[0], cells[1], cells[3], cells[4]]),
    ).toEqual([
      [OLDEST, "Completed", "2", "2"],
      [NEWEST, "Running", "5", "0"],
    ]);
  });

  it("reads the list again on every poll, and a new case appears without a reload", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const server = listServer([]);
    openList();
    await screen.findByText("No cases yet.");
    const before = reads(server);

    server.held.cases = [caseSummary(NEWEST, "running")];
    await act(() => vi.advanceTimersByTimeAsync(CASE_LIST_POLL_MS));

    expect(await rows()).toHaveLength(1);
    expect(reads(server)).toBe(before + 1);

    // The case moves on: its row follows at the next read.
    server.held.cases = [caseSummary(NEWEST, "completed", 3, 0)];
    await act(() => vi.advanceTimersByTimeAsync(CASE_LIST_POLL_MS));
    await waitFor(async () => expect((await rows())[0]![1]).toBe("Completed"));
  });

  it("reads nothing while the tab is hidden, and catches up when it is shown", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const server = listServer([]);
    let visibility: DocumentVisibilityState = "visible";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(
      () => visibility,
    );
    openList();
    await screen.findByText("No cases yet.");
    const before = reads(server);

    visibility = "hidden";
    document.dispatchEvent(new Event("visibilitychange"));
    await act(() => vi.advanceTimersByTimeAsync(CASE_LIST_POLL_MS * 2));
    expect(reads(server)).toBe(before);

    visibility = "visible";
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    await waitFor(() => expect(reads(server)).toBe(before + 1));
  });

  it("shows why the list could not be read, with a way to check again", async () => {
    let down = true;
    const server = fakeServer((call) => {
      if (call.path !== LIST_PATH) {
        return undefined;
      }
      return down
        ? json(
            502,
            errorBody(
              "upstream_unavailable",
              "The service is not available.",
              TRACE_ID,
            ),
          )
        : json(200, { cases: [caseSummary(NEWEST)], has_more: false });
    });

    openList();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The service is not available right now. Please try again.",
    );
    expect(screen.getByText(`Reference: ${TRACE_ID}`)).toBeVisible();
    const before = reads(server);

    down = false;
    await userEvent.click(screen.getByRole("button", { name: "Check again" }));

    expect(await rows()).toHaveLength(1);
    expect(reads(server)).toBe(before + 1);
  });

  it("keeps the list shown when a later read fails, says so, and reads further apart", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let down = false;
    const server = fakeServer((call) => {
      if (call.path !== LIST_PATH) {
        return undefined;
      }
      return down
        ? json(502, errorBody("upstream_unavailable", "Not available."))
        : json(200, { cases: [caseSummary(NEWEST)], has_more: false });
    });
    openList();
    await rows();

    down = true;
    await act(() => vi.advanceTimersByTimeAsync(CASE_LIST_POLL_MS));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The cases could not be read again. What is shown may be out of date.",
    );
    expect(await rows()).toHaveLength(1);
    const after = reads(server);
    // The next poll comes too soon after a failure: nothing is read.
    await act(() => vi.advanceTimersByTimeAsync(CASE_LIST_POLL_MS));
    expect(reads(server)).toBe(after);
    await act(() => vi.advanceTimersByTimeAsync(backoffMs(1)));
    expect(reads(server)).toBeGreaterThan(after);
  });
});

describe("1.13 the list is the underwriter's", () => {
  it("is neither in the customer's navigation nor one of the customer's screens", async () => {
    const server = listServer([caseSummary(NEWEST)]);

    openList("customer");

    expect(
      await screen.findByRole("heading", { name: "Page not found" }),
    ).toBeVisible();
    const navigation = screen.getByRole("navigation", { name: "Screens" });
    expect(
      within(navigation).queryByRole("link", { name: "Cases" }),
    ).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(reads(server)).toBe(0);
  });

  it("shows the server's refusal when the list is not open to the role", async () => {
    fakeServer((call) =>
      call.path === LIST_PATH
        ? json(
            403,
            errorBody(
              "role_not_allowed",
              "This action is not open to your role.",
            ),
          )
        : undefined,
    );

    openList();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This action is not open to your role.",
    );
  });
});
