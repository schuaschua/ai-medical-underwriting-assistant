import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { PROGRESS_POLL_MS } from "../cases/caseProgress";
import { CASES_STORAGE_KEY } from "../cases/sessionCases";
import { ROLE_STORAGE_KEY } from "../role/roleStore";
import {
  caseProgress,
  classification,
  decisionRecorded,
  errorBody,
  fakeServer,
  json,
  pageProgress,
  UPLOADED,
  type RecordedCall,
} from "../test/server";

const CASE = UPLOADED.case_id;
const PROGRESS_PATH = `/api/cases/${CASE}/progress`;
const CLASSIFICATIONS_PATH = `/api/cases/${CASE}/classifications`;
const TRACE_ID = "0af7651916cd43dd8448eb211c80319c";

function pageId(pageNumber: number): string {
  return pageProgress(pageNumber, "classified").page_id;
}

function decisionsPath(pageNumber: number): string {
  return `/api/cases/${CASE}/pages/${pageId(pageNumber)}/decisions`;
}

/**
 * A stand-in for the server that holds one case: its pages, what the
 * classifier said of them, and what a decision does to a page. `decide` may
 * answer a decision itself; otherwise it is stored as the server would.
 */
function caseServer(
  pages: Record<number, string>,
  readings: Record<number, [string, number]>,
  decide?: (call: RecordedCall, pageNumber: number) => Response | undefined,
) {
  const statuses = { ...pages };
  const server = fakeServer((call) => {
    if (call.path === PROGRESS_PATH) {
      const list = Object.entries(statuses).map(([number, status]) =>
        pageProgress(Number(number), status),
      );
      const waiting = list.some((page) =>
        page.page_status.startsWith("awaiting_"),
      );
      const inWork = list.some((page) => page.page_status === "extracting");
      return json(
        200,
        caseProgress(
          CASE,
          waiting ? "awaiting_human" : inWork ? "running" : "completed",
          "done",
          list,
        ),
      );
    }
    if (call.path === CLASSIFICATIONS_PATH) {
      return json(200, {
        case_id: CASE,
        classifications: Object.entries(readings).map(
          ([number, [pageType, confidence]]) =>
            classification(CASE, Number(number), pageType, confidence),
        ),
      });
    }
    const number = Object.keys(statuses)
      .map(Number)
      .find((candidate) => call.path === decisionsPath(candidate));
    if (number !== undefined && call.method === "POST") {
      const answer = decide?.(call, number);
      if (answer !== undefined) {
        return answer;
      }
      const sent = JSON.parse(String(call.body)) as { decision: string };
      const recorded = decisionRecorded(
        CASE,
        pageId(number),
        sent.decision,
        call.role,
      );
      statuses[number] = recorded.page_status;
      return json(200, recorded);
    }
    return undefined;
  });
  return { ...server, statuses };
}

function openCase() {
  window.localStorage.setItem(ROLE_STORAGE_KEY, "customer");
  window.sessionStorage.setItem(
    CASES_STORAGE_KEY,
    JSON.stringify([{ case_id: CASE, document_id: UPLOADED.document_id }]),
  );
  return render(
    <MemoryRouter initialEntries={["/customer/upload"]}>
      <App />
    </MemoryRouter>,
  );
}

function badges(): string[] {
  const list = screen.getByRole("list", { name: `Pages of case ${CASE}` });
  return within(list)
    .getAllByRole("listitem")
    .map((item) => item.firstElementChild?.textContent ?? "");
}

function prompt(pageNumber: number): HTMLElement {
  return screen.getByRole("group", {
    name: `Your answer for page ${pageNumber}`,
  });
}

function queryPrompt(pageNumber: number): HTMLElement | null {
  return screen.queryByRole("group", {
    name: `Your answer for page ${pageNumber}`,
  });
}

/** Every prompt on the screen. */
function prompts(): HTMLElement[] {
  return screen.queryAllByRole("group", { name: /^Your answer for page/ });
}

function decisions(server: { calls: RecordedCall[] }): RecordedCall[] {
  return server.calls.filter((call) => call.path.endsWith("/decisions"));
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("1.10 the customer's prompt for a page", () => {
  it("names the predicted type in plain words and the confidence as a percentage, with Discard and Keep", async () => {
    caseServer(
      { 1: "extracting", 2: "awaiting_customer" },
      { 1: ["lab_report", 1], 2: ["other", 0.96] },
    );

    openCase();

    const asked = await screen.findByRole("group", {
      name: "Your answer for page 2",
    });
    await within(asked).findByText(
      "This looks like a page that is not a medical document (96%). Discard or keep?",
    );
    expect(
      within(asked).getByRole("button", { name: "Discard page 2" }),
    ).toBeEnabled();
    expect(
      within(asked).getByRole("button", { name: "Keep page 2" }),
    ).toBeEnabled();
    // What the buttons show: the two words.
    expect(
      within(asked)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["Discard", "Keep"]);
  });

  it("shows the prompt only for a page the server reports as waiting for the customer", async () => {
    const server = caseServer(
      {
        1: "extracting",
        2: "awaiting_triage",
        3: "awaiting_customer",
        4: "discarded",
        5: "classified",
      },
      {
        1: ["lab_report", 1],
        2: ["invoice", 0.6],
        3: ["invoice", 1],
        4: ["other", 1],
        5: ["other", 1],
      },
    );

    openCase();

    await screen.findByRole("group", { name: "Your answer for page 3" });
    expect(prompts()).toHaveLength(1);
    expect(
      screen
        .getAllByRole("button")
        .map((button) => button.getAttribute("aria-label"))
        .filter((label) => label?.includes("page")),
    ).toEqual(["Discard page 3", "Keep page 3"]);
    // No accept and no deny on the customer's screen (story 1.11).
    expect(screen.queryByRole("button", { name: /accept|deny/i })).toBeNull();
    expect(decisions(server)).toEqual([]);
  });

  it("discards a page with a POST that carries the role header and names the decision only, and the prompt goes", async () => {
    const server = caseServer(
      { 1: "extracting", 2: "awaiting_customer" },
      { 2: ["invoice", 1] },
    );
    const user = userEvent.setup();
    openCase();

    await user.click(
      await screen.findByRole("button", { name: "Discard page 2" }),
    );

    // The page's new status is the server's: the case is read again at once.
    await waitFor(() =>
      expect(badges()).toEqual(["Page 1: Being read", "Page 2: Discarded"]),
    );
    expect(queryPrompt(2)).toBeNull();
    expect(decisions(server)).toEqual([
      {
        path: decisionsPath(2),
        method: "POST",
        // The custom header every non-GET call carries (security rule 24).
        role: "customer",
        contentType: "application/json",
        body: JSON.stringify({ decision: "discard" }),
      },
    ]);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps a page, which then waits for the underwriter", async () => {
    const server = caseServer(
      { 1: "awaiting_customer" },
      { 1: ["id_document", 0.9] },
    );
    const user = userEvent.setup();
    openCase();

    await screen.findByText(
      "This looks like an identity document (90%). Discard or keep?",
    );
    await user.click(screen.getByRole("button", { name: "Keep page 1" }));

    await waitFor(() =>
      expect(badges()).toEqual(["Page 1: Waiting for the underwriter"]),
    );
    expect(queryPrompt(1)).toBeNull();
    expect(decisions(server).map((call) => call.body)).toEqual([
      JSON.stringify({ decision: "keep" }),
    ]);
  });

  it("answers one page and leaves the prompts of the others", async () => {
    caseServer(
      { 1: "awaiting_customer", 2: "awaiting_customer" },
      { 1: ["invoice", 1], 2: ["other", 0.96] },
    );
    const user = userEvent.setup();
    openCase();

    await user.click(
      await screen.findByRole("button", { name: "Discard page 1" }),
    );

    await waitFor(() => expect(badges()[0]).toBe("Page 1: Discarded"));
    expect(queryPrompt(1)).toBeNull();
    expect(
      within(prompt(2)).getByRole("button", { name: "Keep page 2" }),
    ).toBeEnabled();
  });

  it("keeps the prompt, with the error and a way to try again, when the decision fails", async () => {
    let fail = true;
    const server = caseServer(
      { 1: "awaiting_customer" },
      { 1: ["other", 0.96] },
      () =>
        fail
          ? json(
              502,
              errorBody(
                "upstream_unavailable",
                "The service is not available right now.",
                TRACE_ID,
              ),
            )
          : undefined,
    );
    const user = userEvent.setup();
    openCase();

    await user.click(
      await screen.findByRole("button", { name: "Discard page 1" }),
    );

    // The error, in plain words with its reference; nothing was decided.
    const alert = await within(prompt(1)).findByRole("alert");
    expect(alert).toHaveTextContent(
      "The service is not available right now. Please try again.",
    );
    expect(alert).toHaveTextContent(`Reference: ${TRACE_ID}`);
    expect(badges()).toEqual(["Page 1: Needs your answer"]);
    expect(
      within(prompt(1)).getByText(
        "This looks like a page that is not a medical document (96%). Discard or keep?",
      ),
    ).toBeVisible();

    // The answer may have been saved: only the same answer can be sent
    // again, so the other one cannot turn it into a refusal.
    expect(
      within(prompt(1)).queryByRole("button", { name: "Discard page 1" }),
    ).toBeNull();
    expect(
      within(prompt(1)).queryByRole("button", { name: "Keep page 1" }),
    ).toBeNull();

    fail = false;
    await user.click(
      within(prompt(1)).getByRole("button", {
        name: "Try again to save your answer for page 1",
      }),
    );

    await waitFor(() => expect(badges()).toEqual(["Page 1: Discarded"]));
    expect(queryPrompt(1)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(decisions(server).map((call) => call.body)).toEqual([
      JSON.stringify({ decision: "discard" }),
      JSON.stringify({ decision: "discard" }),
    ]);
  });

  it("still offers the retry when the answer was saved but the call failed, and sends the same answer again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let told = false;
    const server = caseServer(
      { 1: "awaiting_customer", 2: "extracting" },
      { 1: ["invoice", 1] },
      () => {
        if (told) {
          return undefined;
        }
        // The server stored the keep and could not tell the case of it.
        server.statuses[1] = "awaiting_triage";
        return json(
          502,
          errorBody("upstream_unavailable", "Saved, but not told."),
        );
      },
    );
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    openCase();

    await user.click(
      await screen.findByRole("button", { name: "Keep page 1" }),
    );
    await within(prompt(1)).findByRole("alert");
    // The next read shows the page moved on; the way to try again stays,
    // without the two choices: only the same answer is sent again.
    await act(() => vi.advanceTimersByTimeAsync(PROGRESS_POLL_MS));
    await waitFor(() =>
      expect(badges()[0]).toBe("Page 1: Waiting for the underwriter"),
    );
    expect(
      within(prompt(1)).queryByRole("button", { name: "Discard page 1" }),
    ).toBeNull();

    told = true;
    await user.click(
      within(prompt(1)).getByRole("button", {
        name: "Try again to save your answer for page 1",
      }),
    );

    await waitFor(() => expect(queryPrompt(1)).toBeNull());
    expect(decisions(server).map((call) => call.body)).toEqual([
      JSON.stringify({ decision: "keep" }),
      JSON.stringify({ decision: "keep" }),
    ]);
  });

  it("drops the prompt when the server refused the answer and the page has moved on", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const server = caseServer(
      { 1: "awaiting_customer", 2: "extracting" },
      { 1: ["invoice", 1] },
      () => {
        // Answered in another tab in the meantime.
        server.statuses[1] = "discarded";
        return json(
          409,
          errorBody(
            "not_awaiting_decision",
            "That page is not waiting for this decision.",
          ),
        );
      },
    );
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    openCase();

    await user.click(
      await screen.findByRole("button", { name: "Keep page 1" }),
    );

    expect(await within(prompt(1)).findByRole("alert")).toHaveTextContent(
      "This page is no longer waiting for that answer.",
    );
    await act(() => vi.advanceTimersByTimeAsync(PROGRESS_POLL_MS));
    await waitFor(() => expect(badges()[0]).toBe("Page 1: Discarded"));
    expect(queryPrompt(1)).toBeNull();
    expect(decisions(server)).toHaveLength(1);
  });

  it("sends one decision when the answer is pressed twice, and says it is being saved", async () => {
    let release: (response: Response) => void = () => undefined;
    const server = caseServer(
      { 1: "awaiting_customer" },
      { 1: ["invoice", 1] },
      () =>
        new Promise<Response>((resolveAnswer) => {
          release = resolveAnswer;
        }) as unknown as Response,
    );
    const user = userEvent.setup();
    openCase();

    await user.click(
      await screen.findByRole("button", { name: "Discard page 1" }),
    );

    expect(
      await within(prompt(1)).findByText("Saving your answer…"),
    ).toBeVisible();
    const buttons = within(prompt(1)).getAllByRole("button");
    buttons.forEach((button) => expect(button).toBeDisabled());
    await user.click(buttons[1]!);
    expect(decisions(server)).toHaveLength(1);

    server.statuses[1] = "discarded";
    await act(async () => {
      release(
        json(200, decisionRecorded(CASE, pageId(1), "discard", "customer")),
      );
      await Promise.resolve();
    });
    await waitFor(() => expect(badges()).toEqual(["Page 1: Discarded"]));
  });

  it("says the answer was saved until the server shows the page's new status", async () => {
    // The server stores the decision, but its next progress is still the old one.
    let stale = true;
    const server = fakeServer((call) => {
      if (call.path === PROGRESS_PATH) {
        return json(
          200,
          caseProgress(CASE, "awaiting_human", "done", [
            pageProgress(1, stale ? "awaiting_customer" : "awaiting_triage"),
          ]),
        );
      }
      return undefined;
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    openCase();

    await user.click(
      await screen.findByRole("button", { name: "Keep page 1" }),
    );

    expect(await screen.findByText("Your answer was saved.")).toBeVisible();
    // No second answer can be given for the page.
    expect(screen.queryByRole("button", { name: "Keep page 1" })).toBeNull();
    stale = false;
    await act(() => vi.advanceTimersByTimeAsync(PROGRESS_POLL_MS));
    await waitFor(() =>
      expect(badges()).toEqual(["Page 1: Waiting for the underwriter"]),
    );
    expect(screen.queryByText("Your answer was saved.")).toBeNull();
    expect(decisions(server)).toHaveLength(1);
  });

  it("asks without naming a type when the classification cannot be read, and reads it again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let broken = true;
    const server = fakeServer((call) => {
      if (call.path === PROGRESS_PATH) {
        // Story 4.2: the progress names the classifier the case was
        // started with.
        return json(200, {
          ...caseProgress(CASE, "awaiting_human", "done", [
            pageProgress(1, "awaiting_customer"),
          ]),
          classifier_contender: "doc-intelligence",
        });
      }
      if (call.path === CLASSIFICATIONS_PATH) {
        // Both classifiers read the page, and the other one's reading is
        // listed first: the prompt shows the case's own.
        return broken
          ? json(502, errorBody("upstream_unavailable", "Not available."))
          : json(200, {
              case_id: CASE,
              classifications: [
                classification(CASE, 1, "other", 1),
                {
                  ...classification(CASE, 1, "invoice", 0.955),
                  contender: "doc-intelligence",
                },
              ],
            });
      }
      return undefined;
    });

    openCase();

    // The customer can still answer.
    const asked = await screen.findByRole("group", {
      name: "Your answer for page 1",
    });
    expect(
      within(asked).getByText("This page needs your answer. Discard or keep?"),
    ).toBeVisible();
    expect(
      within(asked).getByRole("button", { name: "Keep page 1" }),
    ).toBeEnabled();

    broken = false;
    await act(() => vi.advanceTimersByTimeAsync(PROGRESS_POLL_MS));
    await screen.findByText(
      "This looks like an invoice or bill (95%). Discard or keep?",
    );
    // Once it is known it is not read again with every poll.
    const reads = () =>
      server.calls.filter((call) => call.path === CLASSIFICATIONS_PATH).length;
    const before = reads();
    await act(() => vi.advanceTimersByTimeAsync(PROGRESS_POLL_MS * 2));
    expect(reads()).toBe(before);
  });

  it("keeps its text in the strings module and decides nothing in the browser", () => {
    const sources = [
      "src/components/PagePrompt.tsx",
      "src/cases/classifications.ts",
    ]
      .map((path) => readFileSync(resolve(process.cwd(), path), "utf8"))
      .join("\n");

    // No wording of its own, no fetch of its own, and no rule about who may
    // decide what: the server refuses what is not allowed.
    expect(sources).not.toMatch(/Discard or keep|This looks like/);
    expect(sources).not.toMatch(/fetch\(/);
    expect(sources).not.toMatch(/accept|deny|underwriter/i);
    expect(sources).not.toMatch(/dangerouslySetInnerHTML/);
  });
});
