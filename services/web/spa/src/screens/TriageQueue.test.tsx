import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { backoffMs } from "../polling/backoff";
import { ROLE_STORAGE_KEY } from "../role/roleStore";
import {
  decisionRecorded,
  errorBody,
  fakeServer,
  json,
  triagePage,
  type RecordedCall,
} from "../test/server";
import { TRIAGE_POLL_MS } from "../triage/triageQueue";

const FIRST_CASE = "019a0000-0000-7000-8000-000000010000";
const SECOND_CASE = "019a0000-0000-7000-8000-000000020000";
const TRACE_ID = "0af7651916cd43dd8448eb211c80319c";
const QUEUE_PATH = "/api/triage";

type Page = ReturnType<typeof triagePage>;

/**
 * A stand-in for the server that holds the queue: a decision takes its page
 * out, as the real one does. `decide` may answer a decision itself.
 */
function queueServer(
  pages: Page[],
  decide?: (
    call: RecordedCall,
    page: Page,
  ) => Response | Promise<Response> | undefined,
  hasMore = false,
) {
  const waiting = [...pages];
  const server = fakeServer((call) => {
    if (call.path === QUEUE_PATH) {
      return call.role === "underwriter"
        ? json(200, { pages: [...waiting], has_more: hasMore })
        : undefined;
    }
    const page = pages.find(
      (candidate) =>
        call.path ===
        `/api/cases/${candidate.case_id}/pages/${candidate.page_id}/decisions`,
    );
    if (page !== undefined && call.method === "POST") {
      const answer = decide?.(call, page);
      if (answer !== undefined) {
        return answer;
      }
      const sent = JSON.parse(String(call.body)) as { decision: string };
      const index = waiting.indexOf(page);
      if (index >= 0) {
        waiting.splice(index, 1);
      }
      return json(
        200,
        decisionRecorded(page.case_id, page.page_id, sent.decision, call.role),
      );
    }
    return undefined;
  });
  return { ...server, waiting };
}

function openQueue(role = "underwriter") {
  window.localStorage.setItem(ROLE_STORAGE_KEY, role);
  return render(
    <MemoryRouter initialEntries={["/underwriter/triage"]}>
      <App />
    </MemoryRouter>,
  );
}

/** The rows of the queue, as the cells of each. */
function rows(): HTMLElement[] {
  const table = screen.getByRole("table", {
    name: "Pages waiting for a decision",
  });
  return within(table).getAllByRole("row").slice(1);
}

function rowOf(page: Page): HTMLElement {
  return screen.getByRole("group", {
    name: `Decision for page ${page.page_number} of case ${page.case_id}`,
  });
}

function queryRowOf(page: Page): HTMLElement | null {
  return screen.queryByRole("group", {
    name: `Decision for page ${page.page_number} of case ${page.case_id}`,
  });
}

function button(action: string, page: Page): HTMLElement {
  return screen.getByRole("button", {
    name: `${action} page ${page.page_number} of case ${page.case_id}`,
  });
}

function decisions(server: { calls: RecordedCall[] }): RecordedCall[] {
  return server.calls.filter((call) => call.path.endsWith("/decisions"));
}

function reads(server: { calls: RecordedCall[] }): number {
  return server.calls.filter((call) => call.path === QUEUE_PATH).length;
}

const drawn: unknown[] = [];

beforeEach(() => {
  drawn.length = 0;
  // What a browser does with the bytes of a picture; jsdom draws nothing.
  vi.stubGlobal(
    "createImageBitmap",
    vi.fn(async (picture: Blob) => ({
      width: 320,
      height: 453,
      close: () => undefined,
      picture,
    })),
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    () =>
      ({
        drawImage: (bitmap: unknown) => drawn.push(bitmap),
      }) as unknown as CanvasRenderingContext2D,
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("1.11 the underwriter's triage queue", () => {
  it("lists every waiting page with thumbnail, type, percentage and reason", async () => {
    const unsure = triagePage(FIRST_CASE, 2, {
      pageType: "lab_report",
      confidence: 0.6,
      reason: "Lists values, but the heading is unclear.",
    });
    const kept = triagePage(
      SECOND_CASE,
      1,
      { pageType: "invoice", confidence: 0.96, reason: "Shows an amount due." },
      "customer",
    );
    const server = queueServer([unsure, kept]);

    openQueue();

    expect(
      await screen.findByRole("heading", { name: "Triage queue" }),
    ).toBeVisible();
    await waitFor(() => expect(rows()).toHaveLength(2));
    const [first, second] = rows();
    // In the server's order, each with its case and page number.
    expect(first).toHaveTextContent(FIRST_CASE);
    expect(first).toHaveTextContent("Page 2");
    expect(first).toHaveTextContent("Looks like a laboratory report (60%).");
    expect(first).toHaveTextContent(
      "Reason: Lists values, but the heading is unclear.",
    );
    expect(first).toHaveTextContent(
      "Here because the classifier was not sure.",
    );
    expect(second).toHaveTextContent(SECOND_CASE);
    expect(second).toHaveTextContent("Looks like an invoice or bill (96%).");
    expect(second).toHaveTextContent("Reason: Shows an amount due.");
    expect(second).toHaveTextContent("Here because the customer kept it.");
    // Accept and Deny for each page.
    expect(button("Accept", unsure)).toBeEnabled();
    expect(button("Deny", kept)).toBeEnabled();
    expect(
      within(rowOf(unsure))
        .getAllByRole("button")
        .map((item) => item.textContent),
    ).toEqual(["Accept", "Deny"]);
    // Each thumbnail is read from the address the server gave, as the
    // underwriter, and drawn.
    const thumbnails = await screen.findAllByRole("img");
    expect(thumbnails.map((item) => item.getAttribute("aria-label"))).toEqual([
      `Thumbnail of page 2 of case ${FIRST_CASE}`,
      `Thumbnail of page 1 of case ${SECOND_CASE}`,
    ]);
    expect(drawn).toHaveLength(2);
    expect(
      server.calls
        .filter((call) => call.path.endsWith("/thumbnail"))
        .map((call) => [call.path, call.role]),
    ).toEqual([
      [unsure.thumbnail_path, "underwriter"],
      [kept.thumbnail_path, "underwriter"],
    ]);
    expect(decisions(server)).toEqual([]);
  });

  it("shows the reason as text, never as HTML", async () => {
    const markup = '<img src="x" onerror="alert(1)"><b>bold</b>';
    queueServer([
      triagePage(FIRST_CASE, 1, {
        pageType: "other",
        confidence: 0.5,
        reason: markup,
      }),
    ]);

    openQueue();

    await waitFor(() => expect(rows()).toHaveLength(1));
    const [row] = rows();
    expect(row).toHaveTextContent(markup);
    expect(row!.querySelector("b")).toBeNull();
    expect(row!.querySelector("img")).toBeNull();
  });

  it("lists a page whose classification could not be read, and lets it be decided", async () => {
    const unread = triagePage(FIRST_CASE, 4, null, null);
    const server = queueServer([unread]);
    const user = userEvent.setup();

    openQueue();

    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(rows()[0]).toHaveTextContent(
      "What the classifier said of this page could not be read.",
    );
    expect(rows()[0]).not.toHaveTextContent("%");
    await user.click(button("Deny", unread));
    await waitFor(() => expect(decisions(server)).toHaveLength(1));
    expect(JSON.parse(String(decisions(server)[0]!.body))).toEqual({
      decision: "deny",
    });
  });

  it("says when a picture could not be shown, and keeps the row", async () => {
    const page = triagePage(FIRST_CASE, 1);
    fakeServer((call) => {
      if (call.path === QUEUE_PATH) {
        return json(200, { pages: [page], has_more: false });
      }
      if (call.path === page.thumbnail_path) {
        return json(
          404,
          errorBody("not_found", "That page could not be found."),
        );
      }
      return undefined;
    });

    openQueue();

    expect(
      await screen.findByText("The picture could not be shown."),
    ).toBeVisible();
    expect(screen.queryByRole("img")).toBeNull();
    expect(button("Accept", page)).toBeEnabled();
  });

  it("accepts one page and denies another as the underwriter, and both leave the queue", async () => {
    const first = triagePage(FIRST_CASE, 2);
    const second = triagePage(SECOND_CASE, 1, {
      pageType: "invoice",
      confidence: 0.96,
    });
    const server = queueServer([first, second]);
    const user = userEvent.setup();
    openQueue();
    await waitFor(() => expect(rows()).toHaveLength(2));

    await user.click(button("Accept", first));

    // The queue is read again at once: the page is gone, the other stays.
    await waitFor(() => expect(queryRowOf(first)).toBeNull());
    expect(rowOf(second)).toBeVisible();

    await user.click(button("Deny", second));

    expect(await screen.findByText("Nothing is waiting.")).toBeVisible();
    expect(
      decisions(server).map((call) => [
        call.path,
        call.method,
        call.role,
        JSON.parse(String(call.body)),
      ]),
    ).toEqual([
      [
        `/api/cases/${FIRST_CASE}/pages/${first.page_id}/decisions`,
        "POST",
        "underwriter",
        { decision: "accept" },
      ],
      [
        `/api/cases/${SECOND_CASE}/pages/${second.page_id}/decisions`,
        "POST",
        "underwriter",
        { decision: "deny" },
      ],
    ]);
  });

  it("shows a refused decision on its row, with both buttons to try again", async () => {
    const page = triagePage(FIRST_CASE, 1);
    const other = triagePage(FIRST_CASE, 2);
    let refuse = true;
    const server = queueServer([page, other], () =>
      refuse
        ? json(
            404,
            errorBody("not_found", "That page could not be found.", TRACE_ID),
          )
        : undefined,
    );
    const user = userEvent.setup();
    openQueue();
    await waitFor(() => expect(rows()).toHaveLength(2));

    await user.click(button("Accept", page));

    const alert = await within(rowOf(page)).findByRole("alert");
    expect(alert).toHaveTextContent("That could not be found.");
    expect(alert).toHaveTextContent(`Reference: ${TRACE_ID}`);
    // The failure is on its own row only.
    expect(within(rowOf(other)).queryByRole("alert")).toBeNull();
    // Nothing was stored: either decision may be sent.
    expect(button("Accept", page)).toBeEnabled();
    expect(button("Deny", page)).toBeEnabled();

    refuse = false;
    await user.click(button("Deny", page));

    await waitFor(() => expect(queryRowOf(page)).toBeNull());
    expect(decisions(server)).toHaveLength(2);
  });

  it("after a call that may have been stored, offers only the same decision again", async () => {
    const page = triagePage(FIRST_CASE, 1);
    let failing = true;
    const server = queueServer([page], () =>
      failing
        ? json(
            502,
            errorBody(
              "upstream_unavailable",
              "Your decision was saved, but the case could not be told. Please try again.",
            ),
          )
        : undefined,
    );
    const user = userEvent.setup();
    openQueue();
    await waitFor(() => expect(rows()).toHaveLength(1));

    await user.click(button("Accept", page));

    const group = rowOf(page);
    expect(await within(group).findByRole("alert")).toHaveTextContent(
      "The service is not available right now. Please try again.",
    );
    expect(
      within(group)
        .getAllByRole("button")
        .map((item) => item.textContent),
    ).toEqual(["Try again"]);

    failing = false;
    await user.click(
      screen.getByRole("button", {
        name: `Try again to save your decision for page 1 of case ${FIRST_CASE}`,
      }),
    );

    expect(await screen.findByText("Nothing is waiting.")).toBeVisible();
    expect(
      decisions(server).map((call) => JSON.parse(String(call.body))),
    ).toEqual([{ decision: "accept" }, { decision: "accept" }]);
  });

  it("says plainly when a page was decided elsewhere, and the row goes at the next read", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const page = triagePage(FIRST_CASE, 1);
    const server = queueServer([page], () =>
      json(
        409,
        errorBody(
          "not_awaiting_decision",
          "That page is not waiting for this decision.",
        ),
      ),
    );
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    openQueue();
    await waitFor(() => expect(rows()).toHaveLength(1));

    await user.click(button("Accept", page));

    // A plain note, not an error, and nothing left to press. The read that
    // follows the refusal still lists the page.
    const note = await within(rowOf(page)).findByRole("status");
    expect(note).toHaveTextContent(
      "This page was already decided. It will leave the queue shortly.",
    );
    expect(within(rowOf(page)).queryByRole("alert")).toBeNull();
    expect(within(rowOf(page)).queryByRole("button")).toBeNull();
    expect(decisions(server)).toHaveLength(1);

    // The next read no longer lists it.
    server.waiting.length = 0;
    await act(() => vi.advanceTimersByTimeAsync(TRIAGE_POLL_MS));

    expect(await screen.findByText("Nothing is waiting.")).toBeVisible();
  });

  it("keeps the row while a decision is out, though a read in between lists the page no longer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const page = triagePage(FIRST_CASE, 1);
    let answer: (response: Response) => void = () => undefined;
    let held = true;
    // The decision is stored and the page leaves the queue; the answer waits.
    const server = queueServer([page], () => {
      server.waiting.length = 0;
      return held
        ? new Promise<Response>((resolve) => {
            answer = resolve;
          })
        : undefined;
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    openQueue();
    await waitFor(() => expect(rows()).toHaveLength(1));

    await user.click(button("Accept", page));
    const before = reads(server);
    await act(() => vi.advanceTimersByTimeAsync(TRIAGE_POLL_MS));

    // The server lists nothing by now, and the row is still there.
    expect(reads(server)).toBeGreaterThan(before);
    expect(rows()).toHaveLength(1);
    expect(within(rowOf(page)).getByRole("status")).toHaveTextContent(
      "Saving your decision…",
    );

    // The call fails after all: the failure is shown, with the way on.
    await act(async () => {
      answer(json(502, null));
      await Promise.resolve();
    });

    expect(await within(rowOf(page)).findByRole("alert")).toBeVisible();
    expect(
      within(rowOf(page))
        .getAllByRole("button")
        .map((item) => item.textContent),
    ).toEqual(["Try again"]);
    held = false;
    await user.click(within(rowOf(page)).getByRole("button"));

    expect(await screen.findByText("Nothing is waiting.")).toBeVisible();
    expect(
      decisions(server).map((call) => JSON.parse(String(call.body))),
    ).toEqual([{ decision: "accept" }, { decision: "accept" }]);
  });

  it.each([["gets no answer", () => Promise.reject(new TypeError("offline"))]])(
    "offers only the same decision again after a call that %s",
    async (_name, fail) => {
      const page = triagePage(FIRST_CASE, 1);
      let failing = true;
      const server = queueServer([page], () => (failing ? fail() : undefined));
      const user = userEvent.setup();
      openQueue();
      await waitFor(() => expect(rows()).toHaveLength(1));

      await user.click(button("Deny", page));

      await within(rowOf(page)).findByRole("alert");
      expect(
        within(rowOf(page))
          .getAllByRole("button")
          .map((item) => item.textContent),
      ).toEqual(["Try again"]);

      failing = false;
      await user.click(within(rowOf(page)).getByRole("button"));

      expect(await screen.findByText("Nothing is waiting.")).toBeVisible();
      expect(
        decisions(server).map((call) => JSON.parse(String(call.body))),
      ).toEqual([{ decision: "deny" }, { decision: "deny" }]);
    },
  );

  it("still owes the decision when sending it again is refused", async () => {
    const page = triagePage(FIRST_CASE, 1);
    const answers = [
      () => json(502, null),
      // The repeat is refused: the first call may have stored it all the same.
      () => json(404, errorBody("not_found", "That page could not be found.")),
    ];
    const server = queueServer([page], () => answers.shift()?.());
    const user = userEvent.setup();
    openQueue();
    await waitFor(() => expect(rows()).toHaveLength(1));

    await user.click(button("Accept", page));
    await within(rowOf(page)).findByRole("alert");
    await user.click(within(rowOf(page)).getByRole("button"));

    await waitFor(() =>
      expect(within(rowOf(page)).getByRole("alert")).toHaveTextContent(
        "That could not be found.",
      ),
    );
    // Not both buttons: only the decision that is owed.
    expect(
      within(rowOf(page))
        .getAllByRole("button")
        .map((item) => item.textContent),
    ).toEqual(["Try again"]);

    await user.click(within(rowOf(page)).getByRole("button"));

    expect(await screen.findByText("Nothing is waiting.")).toBeVisible();
    expect(
      decisions(server).map((call) => JSON.parse(String(call.body))),
    ).toEqual([
      { decision: "accept" },
      { decision: "accept" },
      { decision: "accept" },
    ]);
  });

  it("reads the queue again on every poll, and a new page appears without a reload", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const server = queueServer([]);
    openQueue();
    await screen.findByText("Nothing is waiting.");
    const before = reads(server);

    server.waiting.push(triagePage(FIRST_CASE, 3));
    await act(() => vi.advanceTimersByTimeAsync(TRIAGE_POLL_MS));

    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(reads(server)).toBe(before + 1);
  });

  it("keeps the queue shown when a later read fails, says so, and reads further apart", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let down = false;
    const server = fakeServer((call) => {
      if (call.path !== QUEUE_PATH) {
        return undefined;
      }
      return down
        ? json(500, null)
        : json(200, { pages: [triagePage(FIRST_CASE, 1)], has_more: false });
    });
    openQueue();
    await waitFor(() => expect(rows()).toHaveLength(1));

    down = true;
    await act(() => vi.advanceTimersByTimeAsync(TRIAGE_POLL_MS));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The queue could not be read again. What is shown may be out of date.",
    );
    expect(rows()).toHaveLength(1);
    // After a failure the next read waits longer than one poll.
    const after = reads(server);
    await act(() => vi.advanceTimersByTimeAsync(TRIAGE_POLL_MS));
    expect(reads(server)).toBe(after);

    down = false;
    await act(() => vi.advanceTimersByTimeAsync(backoffMs(1)));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(rows()).toHaveLength(1);
  });
});

describe("1.11 the queue is the underwriter's", () => {
  it("is neither in the customer's navigation nor one of the customer's screens", async () => {
    const server = queueServer([triagePage(FIRST_CASE, 1)]);

    openQueue("customer");

    expect(
      await screen.findByRole("heading", { name: "Page not found" }),
    ).toBeVisible();
    const navigation = screen.getByRole("navigation", { name: "Screens" });
    expect(
      within(navigation).queryByRole("link", { name: /triage/i }),
    ).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(reads(server)).toBe(0);
  });
});
