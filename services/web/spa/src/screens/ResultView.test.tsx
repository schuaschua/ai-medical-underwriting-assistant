import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { CASE_LIST_PATH } from "../cases/caseList";
import { PAGE_WIDTH_PX } from "../components/PdfDocument";
import { AWAITED_RUN_READS } from "../result/compare";
import { FINAL_CASE_RETRIES, RESULT_POLL_MS } from "../result/result";
import { resultPath } from "../result/resultPath";
import { ROLE_STORAGE_KEY } from "../role/roleStore";
import {
  caseProgress,
  caseSummary,
  errorBody,
  fakeServer,
  json,
  pageProgress,
  type RecordedCall,
} from "../test/server";

const CASE = "019a0000-0000-7000-8000-000000010000";
const DOCUMENT = "019a0000-0000-7000-8000-000000000002";
const LABEL = "AI suggestion, not a decision";
const RULE = "UW-DM-002";

function pageId(pageNumber: number): string {
  return pageProgress(pageNumber, "extracted").page_id;
}

function factId(number: number): string {
  return `019a0000-0000-7000-8000-0000000004${String(number).padStart(2, "0")}`;
}

/** One fact, as `extraction` lists it: found on its page unless said otherwise. */
function fact(
  number: number,
  pageNumber: number,
  statement: string,
  quote: string,
  verified = true,
) {
  return {
    fact_id: factId(number),
    case_id: CASE,
    page_id: pageId(pageNumber),
    page_number: pageNumber,
    statement,
    quote,
    quote_verified: verified,
    quote_start: verified ? 120 : null,
    quote_end: verified ? 131 : null,
  };
}

/** One verdict run, as `verdict` lists it: loaded by 75 unless changed. */
function run(changes: Record<string, unknown> = {}) {
  return {
    verdict_run_id: "019a0000-0000-7000-8000-000000000501",
    case_id: CASE,
    retriever_config: "r3",
    status: "done",
    label: LABEL,
    verdict: "loaded" as string | null,
    loading_pct: 75 as number | null,
    confidence: 0.9 as number | null,
    reasons: [
      {
        rule_id: RULE,
        fact_ids: [factId(2)],
        effect: "debit",
        debit_pct: 50 as number | null,
      },
      {
        rule_id: "UW-TOB-001",
        fact_ids: [factId(1), factId(3)],
        effect: "debit",
        debit_pct: 25 as number | null,
      },
    ],
    system_reasons: [] as string[],
    error_code: null as string | null,
    ...changes,
  };
}

const FACTS = [
  fact(1, 1, "Smokes 10 cigarettes a day", "10 cigarettes daily"),
  fact(2, 3, "HbA1c 7.4 %", "HbA1c\n7.4\n%"),
  fact(3, 3, "Stopped smoking in 2020", "quit in 2020", false),
];

const BOXES = {
  page_id: pageId(3),
  page_number: 3,
  page_width: 600,
  page_height: 800,
  boxes: [
    { char_start: 120, char_end: 125, x0: 60, y0: 200, x1: 120, y1: 216 },
    { char_start: 126, char_end: 131, x0: 300, y0: 200, x1: 330, y1: 216 },
  ],
};

interface Held {
  status: string;
  errorCode: string | null;
  pages: number;
  facts: unknown;
  /** Answers the read of the facts in place of the facts, when set. */
  factsFault: (() => Response) | null;
  runs: unknown[];
  moreRuns: boolean;
  file: () => Response;
  boxes: () => Response;
  rule: () => Response;
  /** Story 2.8: the steps of a run, by the address they were asked for with. */
  steps: (address: string) => Response;
  /** Story 3.6: the pairs of rows of Compare, as `web` holds them. */
  pairs: () => Response;
  /** Story 3.6: the answer to a request for one more run with that row. */
  requestRun: (row: string) => Response;
}

/** The second run of a case, made with `r5`: standard rates, no reasons, unless changed. */
function otherRun(changes: Record<string, unknown> = {}) {
  return run({
    verdict_run_id: "019a0000-0000-7000-8000-000000000502",
    retriever_config: "r5",
    verdict: "standard",
    loading_pct: null,
    reasons: [],
    ...changes,
  });
}

/** One search of the agent's log that returned those rules, as `verdict` lists it. */
function step(runId: string, stepNo: number, ruleIds: string[]) {
  return {
    verdict_run_id: runId,
    case_id: CASE,
    step_no: stepNo,
    tool: "search_rules",
    arguments: { query: "HbA1c 7.4 %" },
    fact_id: null,
    rule_ids: ruleIds,
    outcome: "done",
    error_code: null,
    latency_ms: 19,
    occurred_at: "2026-10-08T09:00:00Z",
  };
}

/** What `workflow` answers a request for one more run with. */
function runRequested(row: string, changes: Record<string, unknown> = {}) {
  return {
    case_id: CASE,
    retriever_config: row,
    status: "running",
    verdict_run_id: null,
    error_code: null,
    ...changes,
  };
}

/** A stand-in for the server that holds one case's result; it can change while the screen is open. */
function resultServer(changes: Partial<Held> = {}) {
  const held: Held = {
    status: "completed",
    errorCode: null,
    pages: 3,
    facts: FACTS,
    factsFault: null,
    runs: [run()],
    moreRuns: false,
    file: () =>
      new Response(new TextEncoder().encode("%PDF-1.7 synthetic"), {
        status: 200,
        headers: { "Content-Type": "application/pdf" },
      }),
    boxes: () => json(200, BOXES),
    rule: () =>
      json(200, {
        rule_id: RULE,
        chunk_id: `smart-${RULE}`,
        chunk_set: "smart",
        text: `Rule ${RULE}: HbA1c from 7.0 % to 7.9 %: <b>+50 %</b>.`,
        manual_page: 31,
        impairment: "Type 2 diabetes mellitus",
        reference_rule_ids: [],
      }),
    steps: () => json(200, { steps: [], has_more: false }),
    pairs: () =>
      json(200, {
        default_pair: { first: "r4", second: "r5" },
        fallback_pair: { first: "r3", second: "r5" },
      }),
    // As long as story 3.7 has not built `r4`: every other row can be run.
    requestRun: (row) =>
      row === "r4"
        ? json(
            409,
            errorBody("retriever_not_available", "That row is not available."),
          )
        : json(200, runRequested(row)),
    ...changes,
  };
  const server = fakeServer((call) => {
    const path = call.path.split("?")[0];
    if (path === "/api/cases" && call.method === "GET") {
      return json(200, {
        cases: [caseSummary(CASE, held.status, held.pages, 0)],
        has_more: false,
      });
    }
    if (path === `/api/cases/${CASE}/progress`) {
      return json(200, {
        ...caseProgress(
          CASE,
          held.status,
          "done",
          Array.from({ length: held.pages }, (_, index) =>
            pageProgress(index + 1, "extracted"),
          ),
        ),
        error_code: held.errorCode,
      });
    }
    if (path === `/api/cases/${CASE}/audit`) {
      return json(200, { case_id: CASE, events: [], has_more: false });
    }
    if (call.role !== "underwriter") {
      return undefined;
    }
    if (path === `/api/cases/${CASE}/pages`) {
      return json(200, {
        case_id: CASE,
        pages: Array.from({ length: held.pages }, (_, index) => ({
          page_id: pageId(index + 1),
          case_id: CASE,
          document_id: DOCUMENT,
          page_number: index + 1,
        })),
      });
    }
    if (path === `/api/cases/${CASE}/facts`) {
      return (
        held.factsFault?.() ?? json(200, { case_id: CASE, facts: held.facts })
      );
    }
    if (path === "/api/compare-pairs") {
      return held.pairs();
    }
    if (path === `/api/cases/${CASE}/verdict-runs` && call.method === "POST") {
      const wanted = JSON.parse(String(call.body)) as {
        retriever_config: string;
      };
      return held.requestRun(wanted.retriever_config);
    }
    if (path === `/api/cases/${CASE}/verdict-runs`) {
      return json(200, {
        case_id: CASE,
        verdict_runs: held.runs,
        has_more: held.moreRuns,
      });
    }
    if (path === `/api/documents/${DOCUMENT}/file`) {
      return held.file();
    }
    if (path?.startsWith("/api/pages/") && path.endsWith("/boxes")) {
      return held.boxes();
    }
    if (path?.startsWith("/api/rules/")) {
      return held.rule();
    }
    if (path?.startsWith("/api/verdict-runs/")) {
      return held.steps(call.path);
    }
    return undefined;
  });
  return { ...server, held };
}

function openResult(address = resultPath(CASE), role = "underwriter") {
  window.localStorage.setItem(ROLE_STORAGE_KEY, role);
  return render(
    <MemoryRouter initialEntries={[address]}>
      <App />
    </MemoryRouter>,
  );
}

function callsTo(server: { calls: RecordedCall[] }, path: string) {
  return server.calls.filter((call) => call.path.split("?")[0] === path);
}

async function verdictPane() {
  return within(
    await screen.findByRole("region", { name: "Suggested verdict" }),
  );
}

async function documentPane() {
  return within(
    await screen.findByRole("region", { name: "Redacted document" }),
  );
}

/** The rows of the facts table, each as the text of its cells. */
async function factRows(): Promise<string[][]> {
  const table = await screen.findByRole("table", {
    name: "Facts, in page order",
  });
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) =>
      [
        within(row).getByRole("rowheader"),
        ...within(row).getAllByRole("cell"),
      ].map((cell) => cell.textContent ?? ""),
    );
}

/** The citation control of a fact in the facts table. */
function citation(statement: string, pageNumber: number): HTMLElement {
  return within(
    screen.getByRole("table", { name: "Facts, in page order" }),
  ).getByRole("button", {
    name: `Show the quote for “${statement}” on page ${pageNumber}`,
  });
}

/** Stands in for the browser's say on what is in view; a test shows a page by hand. */
class InView {
  static observers: InView[] = [];
  readonly watched = new Set<Element>();
  constructor(
    private readonly arrived: (entries: IntersectionObserverEntry[]) => void,
  ) {
    InView.observers.push(this);
  }
  observe(element: Element) {
    this.watched.add(element);
  }
  unobserve(element: Element) {
    this.watched.delete(element);
  }
  disconnect() {
    this.watched.clear();
  }
  static show(pageNumber: number) {
    for (const observer of InView.observers) {
      const entries = [...observer.watched]
        .filter(
          (element) =>
            (element as HTMLElement).dataset.pageNumber === String(pageNumber),
        )
        .map(
          (target) =>
            ({ target, isIntersecting: true }) as IntersectionObserverEntry,
        );
      if (entries.length > 0) {
        observer.arrived(entries);
      }
    }
  }
}

function drawnPages(): string[] {
  return screen
    .queryAllByRole("img", { name: /^Drawn page/ })
    .map((page) => page.getAttribute("aria-label") ?? "");
}

/** The pane the document's pages scroll in. */
function pagesPane(): HTMLElement {
  return screen.getByRole("region", { name: "Pages of the redacted document" });
}

beforeEach(() => {
  InView.observers = [];
  vi.stubGlobal("IntersectionObserver", InView);
  // jsdom lays nothing out. The test gives each page a place 1000 px under
  // the one before it, and a highlight a place 400 px down its page.
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(
    function (this: Element) {
      const sheet = this.closest<HTMLElement>("[data-page-number]");
      const page = Number(sheet?.dataset.pageNumber ?? 1);
      const top = this.classList.contains("result-highlight")
        ? (page - 1) * 1000 + 400
        : this === sheet
          ? (page - 1) * 1000
          : 0;
      return { top } as DOMRect;
    },
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("2.7 the underwriter's result view", () => {
  it("shows the redacted document beside the verdict with its label, the reasons and the facts", async () => {
    const server = resultServer();

    openResult();

    // The verdict, with the loading the server gave and the exact label.
    const verdict = await verdictPane();
    expect(await verdict.findByText("Loaded premium, +75 %")).toBeVisible();
    expect(verdict.getByText(LABEL)).toBeVisible();
    expect(verdict.getByText("Confidence: 90%")).toBeVisible();
    expect(verdict.getByText("Made with retrieval row r3.")).toBeVisible();

    // Each reason: its rule, its debit, and the facts it cites with their pages.
    const reasons = verdict
      .getAllByRole("listitem")
      .filter((item) => item.textContent?.startsWith("Rule "))
      .map((item) => item.textContent);
    expect(reasons).toEqual([
      "Rule UW-DM-002 Debit +50 %Facts it cites:HbA1c 7.4 % HbA1c\n7.4\n% Page 3",
      "Rule UW-TOB-001 Debit +25 %Facts it cites:Smokes 10 cigarettes a day 10 cigarettes daily Page 1Stopped smoking in 2020 quit in 2020 Page 3: quote not found on the page",
    ]);

    // The facts as the server lists them, each with its quote and its page.
    expect(await factRows()).toEqual([
      ["Smokes 10 cigarettes a day", "10 cigarettes daily", "Page 1"],
      ["HbA1c 7.4 %", "HbA1c\n7.4\n%", "Page 3"],
      [
        "Stopped smoking in 2020",
        "quit in 2020",
        "Page 3: quote not found on the page",
      ],
    ]);

    // The document: the redacted PDF, read through the client as the
    // underwriter and handed to the renderer; no element fetches it.
    const pane = await documentPane();
    expect(
      await pane.findByRole("document", { name: "A PDF of 18 bytes" }),
    ).toBeVisible();
    expect(pane.getByText("Page 1 of 3")).toBeVisible();
    const fileCalls = callsTo(server, `/api/documents/${DOCUMENT}/file`);
    expect(fileCalls).toHaveLength(1);
    expect(fileCalls[0]!.role).toBe("underwriter");
    expect(document.querySelector("iframe, embed, object, img")).toBeNull();

    // A suggestion only: nothing on the screen decides, approves or overrides.
    const controls = [
      ...screen.getAllByRole("button"),
      ...screen.queryAllByRole("combobox"),
      ...screen.queryAllByRole("textbox"),
      ...screen.queryAllByRole("checkbox"),
    ]
      .filter((control) => control.closest("header") === null)
      .map((control) => control.textContent);
    expect(controls).toEqual([
      "Check again",
      // Story 3.6: shows a second run beside this one; it decides nothing.
      "Compare two retrieval rows",
      // Story 2.8: opens the agent's steps, which are only read.
      "How was this reached?",
      "Rule UW-DM-002",
      "Page 3",
      "Rule UW-TOB-001",
      "Page 1",
      "Page 1",
      "Page 3",
    ]);
    // The case is final: nothing is read again.
    expect(screen.getByText(/The case is finished/)).toBeVisible();
    const before = server.calls.length;
    await act(() => new Promise((done) => setTimeout(done, 20)));
    expect(server.calls.length).toBe(before);
  });

  it("follows a verified fact's citation to its page and draws the boxes the server returns", async () => {
    const server = resultServer();
    const user = userEvent.setup();
    openResult();
    const pane = await documentPane();
    await pane.findByText("Page 3 of 3");

    // A long document: no page is drawn before it comes into view.
    expect(drawnPages()).toEqual([]);
    act(() => InView.show(1));
    expect(drawnPages()).toEqual(["Drawn page 1"]);
    const first = screen.getByRole("img", { name: "Drawn page 1" });
    expect(first).toHaveAttribute("data-width", String(PAGE_WIDTH_PX));
    // The browser's own text layer is never drawn, let alone searched.
    expect(first).toHaveAttribute("data-text-layer", "false");
    expect(first).toHaveAttribute("data-annotation-layer", "false");

    await user.click(citation("HbA1c 7.4 %", 3));

    // The page is shown and said; the boxes were asked for with the fact's
    // own offsets, and are placed as shares of the page's size.
    expect(
      await pane.findByText("Page 3: the quote is highlighted."),
    ).toBeVisible();
    // The pane is scrolled, first to the page and then to the highlight on
    // it, which sits low on the page; the window is left where it is.
    expect(pagesPane().scrollTop).toBe(2000 + 2400);
    expect(window.scrollY).toBe(0);
    expect(drawnPages()).toEqual(["Drawn page 1", "Drawn page 3"]);
    const boxCalls = callsTo(server, `/api/pages/${pageId(3)}/boxes`);
    expect(boxCalls.map((call) => call.path)).toEqual([
      `/api/pages/${pageId(3)}/boxes?quote_start=120&quote_end=131`,
    ]);
    const highlight = pane.getByRole("img", {
      name: "Highlight of the quote on page 3",
    });
    expect(highlight.parentElement).toHaveAttribute("data-page-number", "3");
    expect(
      [...highlight.children].map((box) => {
        const { left, top, width, height } = (box as HTMLElement).style;
        return [left, top, width, height];
      }),
    ).toEqual([
      ["10%", "25%", "10%", "2%"],
      ["50%", "25%", "5%", "2%"],
    ]);

    // When the boxes cannot be read the page is still shown, with a plain note.
    server.held.boxes = () =>
      json(502, errorBody("upstream_unavailable", "Not available."));
    await user.click(citation("Smokes 10 cigarettes a day", 1));

    expect(
      await pane.findByText("Page 1: the highlight could not be shown."),
    ).toBeVisible();
    expect(pane.queryByRole("img", { name: /^Highlight/ })).toBeNull();
  });

  it("flags an unverified fact and offers no citation or highlight for it", async () => {
    const server = resultServer({
      facts: [fact(3, 2, "Stopped smoking in 2020", "quit in 2020", false)],
      runs: [],
    });

    openResult();

    expect(await factRows()).toEqual([
      [
        "Stopped smoking in 2020",
        "quit in 2020",
        "Page 2: quote not found on the page",
      ],
    ]);
    const table = screen.getByRole("table", { name: "Facts, in page order" });
    expect(within(table).queryByRole("button")).toBeNull();
    expect(within(table).queryByRole("link")).toBeNull();
    expect(callsTo(server, `/api/pages/${pageId(2)}/boxes`)).toEqual([]);
  });

  it("opens a reason's rule beside it: the manual's text, as text, with its impairment and page", async () => {
    const server = resultServer();
    const user = userEvent.setup();
    openResult();

    await user.click(
      await screen.findByRole("button", {
        name: "Read rule UW-DM-002 in the manual",
      }),
    );

    const panel = within(
      await screen.findByRole("complementary", {
        name: "Rule UW-DM-002 in the manual",
      }),
    );
    // The text is shown as written: its angle brackets are no markup.
    expect(
      await panel.findByText(
        "Rule UW-DM-002: HbA1c from 7.0 % to 7.9 %: <b>+50 %</b>.",
      ),
    ).toBeVisible();
    expect(document.querySelector("aside b")).toBeNull();
    expect(
      panel.getByText("Impairment: Type 2 diabetes mellitus"),
    ).toBeVisible();
    expect(panel.getByText("Manual page 31")).toBeVisible();
    expect(callsTo(server, `/api/rules/${RULE}`)).toHaveLength(1);

    // Focus went to the panel when it opened, and goes back to the rule's
    // own control when it is closed.
    expect(
      screen.getByRole("heading", { name: "Rule UW-DM-002 in the manual" }),
    ).toHaveFocus();
    await user.click(panel.getByRole("button", { name: "Close the rule" }));
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Read rule UW-DM-002 in the manual" }),
    ).toHaveFocus();

    // A rule the manual does not hold.
    server.held.rule = () => json(404, errorBody("not_found", "Not found."));
    await user.click(
      screen.getByRole("button", {
        name: "Read rule UW-TOB-001 in the manual",
      }),
    );

    expect(
      await screen.findByText("This rule is not in the manual."),
    ).toBeVisible();
  });

  it("words a referral with each of its system reasons", async () => {
    resultServer({
      runs: [
        run({
          verdict: "refer",
          loading_pct: null,
          confidence: null,
          reasons: [],
          system_reasons: ["conflicting_rules", "step_limit"],
        }),
      ],
    });

    openResult();

    const verdict = await verdictPane();
    expect(await verdict.findByText("Refer to underwriter")).toBeVisible();
    expect(verdict.getByText(LABEL)).toBeVisible();
    expect(
      verdict.getByText("The rules that matched do not agree with each other."),
    ).toBeVisible();
    expect(
      verdict.getByText(
        "The agent reached the limit on its steps before it answered.",
      ),
    ).toBeVisible();
    expect(verdict.getByText("The agent gave no confidence.")).toBeVisible();
  });

  it("names the run on screen and lets another be picked when a case has several", async () => {
    resultServer({
      runs: [
        run(),
        run({
          verdict_run_id: "019a0000-0000-7000-8000-000000000502",
          retriever_config: "r5",
          verdict: "standard",
          loading_pct: null,
          reasons: [],
        }),
      ],
    });
    const user = userEvent.setup();
    openResult();
    const verdict = await verdictPane();
    expect(await verdict.findByText("Loaded premium, +75 %")).toBeVisible();
    expect(verdict.getByText("Made with retrieval row r3.")).toBeVisible();

    const picker = verdict.getByRole("combobox", {
      name: "Suggestion on screen, by retrieval row",
    });
    expect(
      within(picker)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(["r3 (done)", "r5 (done)"]);
    await user.selectOptions(picker, "r5 (done)");

    expect(verdict.getByText("Standard rates")).toBeVisible();
    expect(verdict.getByText("Made with retrieval row r5.")).toBeVisible();
    expect(verdict.getByText(LABEL)).toBeVisible();
    expect(verdict.queryByText("Loaded premium, +75 %")).toBeNull();
  });

  it("says there is no suggestion yet, shows what exists, and reads again until the case is final", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const server = resultServer({
      status: "awaiting_human",
      facts: [FACTS[0]],
      runs: [],
    });
    openResult();

    expect(
      await (await verdictPane()).findByText("No suggestion yet."),
    ).toBeVisible();
    expect(await factRows()).toHaveLength(1);
    expect(
      await (await documentPane()).findByRole("document", { name: /A PDF of/ }),
    ).toBeVisible();
    expect(
      screen.getByText(/This screen reads again as the case moves/),
    ).toBeVisible();

    // The case moves on: the screen follows at the next read.
    server.held.status = "completed";
    server.held.facts = FACTS;
    server.held.runs = [run()];
    await act(() => vi.advanceTimersByTimeAsync(RESULT_POLL_MS));

    expect(
      await (await verdictPane()).findByText("Loaded premium, +75 %"),
    ).toBeVisible();
    expect(await factRows()).toHaveLength(3);
    // The document is read once, not with every read of the result.
    expect(callsTo(server, `/api/documents/${DOCUMENT}/file`)).toHaveLength(1);

    // Final: no further read is made.
    const before = server.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(RESULT_POLL_MS * 3));
    expect(server.calls.length).toBe(before);
  });

  it("says that a run or the case failed, with the reason in plain words, and shows what exists", async () => {
    resultServer({
      status: "failed",
      errorCode: "invalid_model_output",
      runs: [
        run({
          status: "failed",
          verdict: null,
          loading_pct: null,
          confidence: null,
          reasons: [],
          error_code: "invalid_model_output",
        }),
      ],
    });

    openResult();

    expect(
      await (
        await verdictPane()
      ).findByText("This run failed. The model's answer could not be used."),
    ).toBeVisible();
    expect(
      screen.getByText(
        "The case failed. The model's answer could not be used.",
      ),
    ).toBeVisible();
    expect((await verdictPane()).getByText(LABEL)).toBeVisible();
    expect(
      (await verdictPane()).queryByText(/Loaded|Standard|Decline|Refer/),
    ).toBeNull();
    expect(await factRows()).toHaveLength(3);
  });

  it("says the document is not ready while it is not redacted, and that a case does not exist", async () => {
    const server = resultServer({
      status: "running",
      pages: 0,
      facts: [],
      runs: [],
    });
    const view = openResult();

    expect(
      await (await documentPane()).findByText("The document is not ready yet."),
    ).toBeVisible();
    // No document is asked for before the server lists a page of it.
    expect(callsTo(server, `/api/documents/${DOCUMENT}/file`)).toEqual([]);
    view.unmount();

    // Asked for all the same, before its redaction is done: the server's refusal.
    server.held.pages = 1;
    server.held.file = () =>
      json(
        409,
        errorBody("not_redacted", "The document has not been redacted."),
      );
    const again = openResult();
    expect(
      await (await documentPane()).findByText("The document is not ready yet."),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Try to read the document again" }),
    ).toBeVisible();
    again.unmount();

    // A case that was never started.
    fakeServer();
    openResult();
    expect(await screen.findByText("No such case.")).toBeVisible();
    expect(
      screen.queryByRole("region", { name: "Suggested verdict" }),
    ).toBeNull();
  });

  it("shows a fault for the part whose answer is not the contract's shape, and the other parts still", async () => {
    resultServer({
      // A fact claimed found, without the offsets a found quote has.
      facts: [{ ...FACTS[1], quote_start: null }],
    });

    openResult();

    expect(
      await screen.findByText("The facts could not be read."),
    ).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
    // The suggestion is shown all the same. Its reasons say that the facts
    // could not be read, not that each fact is missing from a list.
    const verdict = await verdictPane();
    expect(verdict.getByText("Loaded premium, +75 %")).toBeVisible();
    expect(
      screen
        .getByRole("region", { name: "Suggested verdict" })
        .textContent?.match(
          /The facts could not be read, so the facts this reason cites cannot be shown\./g,
        ),
    ).toHaveLength(2);
    expect(screen.queryByText(/not in the list below/)).toBeNull();
    // The document, read from another service, is shown all the same.
    expect(
      await (await documentPane()).findByRole("document", { name: /A PDF of/ }),
    ).toBeVisible();
    expect(screen.queryByText(/could not be shown/)).toBeNull();
  });

  it("never says a quote is highlighted when no highlight is on screen", async () => {
    const beyond = fact(4, 5, "Weight 82 kg", "82 kg");
    const server = resultServer({ facts: [...FACTS, beyond] });
    const user = userEvent.setup();
    const view = openResult();
    await (await documentPane()).findByText("Page 3 of 3");
    const highlights = () =>
      screen.queryAllByRole("img", { name: /^Highlight/ });

    // Boxes answered for another page than the fact's are not drawn on it.
    server.held.boxes = () => json(200, { ...BOXES, page_number: 2 });
    await user.click(citation("HbA1c 7.4 %", 3));
    expect(
      await screen.findByText("Page 3: the highlight could not be shown."),
    ).toBeVisible();

    // Nor is a box that lies beside its page, or one without whole offsets.
    for (const box of [
      { ...BOXES.boxes[0], x1: 700 },
      { ...BOXES.boxes[0], char_start: 1.5 },
    ]) {
      server.held.boxes = () => json(200, { ...BOXES, boxes: [box] });
      await user.click(citation("Smokes 10 cigarettes a day", 1));
      await screen.findByText("Page 1: the highlight could not be shown.");
      await user.click(citation("HbA1c 7.4 %", 3));
      await screen.findByText("Page 3: the highlight could not be shown.");
    }

    // A fact on a page the document does not have.
    server.held.boxes = () =>
      json(200, { ...BOXES, page_id: pageId(5), page_number: 5 });
    await user.click(citation("Weight 82 kg", 5));
    expect(
      await screen.findByText("Page 5: the highlight could not be shown."),
    ).toBeVisible();
    expect(highlights()).toEqual([]);
    expect(screen.queryByText(/the quote is highlighted/)).toBeNull();
    view.unmount();

    // No document on screen at all: good boxes still highlight nothing.
    server.held.boxes = () => json(200, BOXES);
    server.held.file = () =>
      json(502, errorBody("upstream_unavailable", "Not available."));
    openResult();
    await screen.findByText("The document could not be shown.");
    await user.click(citation("HbA1c 7.4 %", 3));
    expect(
      await screen.findByText("Page 3: the highlight could not be shown."),
    ).toBeVisible();
    expect(highlights()).toEqual([]);
  });

  it("reads a failing part further apart, gives up on a final case, and never repeats a wrong-shaped answer", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const factsPath = `/api/cases/${CASE}/facts`;
    // The facts cannot be had: `extraction` is down.
    const failing = resultServer({
      factsFault: () =>
        json(502, errorBody("upstream_unavailable", "Not available.")),
    });
    const factReads = () => callsTo(failing, factsPath).length;
    const view = openResult();

    expect(
      await screen.findByText("The facts could not be read."),
    ).toBeVisible();
    // The case is final, but the screen still reads: it does not say otherwise.
    expect(screen.queryByText(/The case is finished/)).toBeNull();
    expect(factReads()).toBe(1);
    // Not at the usual pace: the next read waits longer.
    await act(() => vi.advanceTimersByTimeAsync(RESULT_POLL_MS));
    expect(factReads()).toBe(1);

    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(factReads()).toBe(FINAL_CASE_RETRIES);
    expect(screen.getByText(/has stopped trying/)).toBeVisible();
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    expect(factReads()).toBe(FINAL_CASE_RETRIES);
    expect(screen.getByRole("button", { name: "Check again" })).toBeVisible();
    view.unmount();

    // An answer of the wrong shape is the same answer every time: read once.
    const malformed = resultServer({
      facts: [{ ...FACTS[1], quote_start: null }],
    });
    openResult();
    await screen.findByText("The facts could not be read.");
    expect(await screen.findByText(/The case is finished/)).toBeVisible();
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    expect(callsTo(malformed, factsPath)).toHaveLength(1);
  });

  it("is reached from the case list and from a case's audit trail, by the underwriter only", async () => {
    resultServer();
    const user = userEvent.setup();
    const list = openResult(CASE_LIST_PATH);

    const fromList = await screen.findByRole("link", {
      name: `Result of case ${CASE}`,
    });
    expect(fromList).toHaveAttribute("href", resultPath(CASE));
    // Not a screen of its own in the navigation: it needs a case.
    expect(
      within(screen.getByRole("navigation", { name: "Screens" })).queryByRole(
        "link",
        { name: "Result" },
      ),
    ).toBeNull();
    await user.click(fromList);
    expect(
      await screen.findByRole("heading", { name: "Result" }),
    ).toBeVisible();

    await user.click(
      screen.getByRole("link", { name: "Audit trail of this case" }),
    );
    const fromTrail = await screen.findByRole("link", {
      name: "Result of this case",
    });
    expect(fromTrail).toHaveAttribute("href", resultPath(CASE));
    list.unmount();

    // AD-9: the customer has no such screen, and the server refuses its reads.
    openResult(resultPath(CASE), "customer");
    expect(
      await screen.findByRole("heading", { name: "Page not found" }),
    ).toBeVisible();
  });

  it("2.8 opens the steps of the run on screen from “How was this reached?”, each fact by its statement", async () => {
    const runId = run().verdict_run_id;
    const server = resultServer({
      steps: () =>
        json(200, {
          steps: [
            {
              verdict_run_id: runId,
              case_id: CASE,
              step_no: 1,
              tool: "search_rules",
              arguments: { query: "HbA1c 7.4 %", fact_id: factId(2) },
              fact_id: factId(2),
              rule_ids: [RULE, "UW-DM-001"],
              outcome: "done",
              error_code: null,
              latency_ms: 19,
              occurred_at: "2026-10-08T09:00:00Z",
            },
          ],
          has_more: false,
        }),
    });
    const user = userEvent.setup();
    openResult();
    const verdict = await verdictPane();
    const opener = await verdict.findByRole("button", {
      name: "How was this reached?",
    });
    expect(opener).toHaveAttribute("aria-expanded", "false");
    expect(callsTo(server, `/api/verdict-runs/${runId}/steps`)).toHaveLength(0);

    await user.click(opener);

    const table = await verdict.findByRole("table", {
      name: `Agent steps of run ${runId}`,
    });
    expect(
      within(within(table).getAllByRole("row")[1]!)
        .getAllByRole("cell")
        .slice(0, 5)
        .map((cell) => cell.textContent),
    ).toEqual([
      "Search the manual",
      `query: HbA1c 7.4 %fact_id: ${factId(2)}`,
      // The fact it was about, as the facts on this screen word it.
      "HbA1c 7.4 %",
      "UW-DM-002, UW-DM-001",
      "Done",
    ]);
    const asked = callsTo(server, `/api/verdict-runs/${runId}/steps`);
    expect(asked.map((call) => [call.path, call.method, call.role])).toEqual([
      [`/api/verdict-runs/${runId}/steps`, "GET", "underwriter"],
    ]);

    await user.click(verdict.getByRole("button", { name: "Close the steps" }));

    expect(verdict.queryByRole("table", { name: /Agent steps/ })).toBeNull();
    expect(opener).toHaveFocus();
  });

  it("works out no verdict, loading or verification in the browser, and renders no text as HTML", () => {
    const sources = [
      "src/screens/ResultView.tsx",
      "src/components/AgentSteps.tsx",
      "src/components/PdfDocument.tsx",
      "src/result/result.ts",
      "src/result/compare.ts",
      "src/result/resultPath.ts",
      "src/api/client.ts",
      "src/strings.ts",
    ].map((path) => readFileSync(resolve(process.cwd(), path), "utf8"));

    for (const source of sources) {
      // No sum of debits, no comparison of a confidence or a loading with
      // anything, no floor, and no verdict chosen from other fields.
      expect(source).not.toMatch(/reduce\(|0\.7\b|\bfloor\b(?!\()/i);
      expect(source).not.toMatch(
        /(confidence|loading_pct|debit_pct)\s*(?:[<>]=?|[+*/-]\s)/,
      );
      expect(source).not.toMatch(
        /verdict\s*[:=]\s*["'`](standard|loaded|decline|refer)["'`]/,
      );
      // A quote is never looked for in the browser: no search of any text,
      // and `quote_verified` is only read.
      expect(source).not.toMatch(
        /indexOf|includes\(|\.search\(|\.match\(|textContent|getTextContent|customTextRenderer/,
      );
      expect(source).not.toMatch(/quote_verified\s*=[^=]/);
      // security rule 22: nothing is rendered as HTML.
      expect(source).not.toMatch(/dangerouslySetInnerHTML|innerHTML|__html/);
    }
  });
});

describe("3.6 Compare two retrieval rows on one case", () => {
  const TOGGLE = "Compare two retrieval rows";
  const DIFFERS = "Differs from the other run";
  const ONLY_HERE = "Only in this run";

  function runRequests(server: { calls: RecordedCall[] }): string[] {
    return callsTo(server, `/api/cases/${CASE}/verdict-runs`)
      .filter((call) => call.method === "POST")
      .map((call) => String(call.body));
  }

  async function pane(row: string) {
    return within(
      await screen.findByRole("region", {
        name: `Run with retrieval row ${row}`,
      }),
    );
  }

  /** The rules a pane lists as retrieved, each with its mark if it has one. */
  async function retrieved(row: string) {
    const list = await (
      await pane(row)
    ).findByRole("list", { name: "Rules it retrieved" });
    return within(list)
      .getAllByRole("listitem")
      .map((item) => item.textContent);
  }

  it("asks for the run the pair lacks, uses the fallback pair while r4 is not built, and marks what differs once both runs are done", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const first = run().verdict_run_id;
    const server = resultServer({
      // The steps of the first run come in two answers; each rule is listed once.
      steps: (address) =>
        address.endsWith("after_step_no=1")
          ? json(200, {
              steps: [step(first, 2, ["UW-DM-001", "UW-TOB-001"])],
              has_more: false,
            })
          : address.startsWith(`/api/verdict-runs/${first}/`)
            ? json(200, {
                steps: [step(first, 1, [RULE, "UW-DM-001"])],
                has_more: true,
              })
            : json(200, {
                steps: [step(otherRun().verdict_run_id, 1, ["UW-DM-001"])],
                has_more: false,
              }),
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    openResult();
    const toggle = await screen.findByRole("button", { name: TOGGLE });
    expect(toggle).toHaveAttribute("aria-pressed", "false");

    await user.click(toggle);

    // `r4` is refused as not available: that is no error, the fallback pair
    // is shown. The case has a run on `r3`, so only `r5` is asked for.
    const waiting = await pane("r5");
    expect(waiting.getByText("The suggestion is being made.")).toBeVisible();
    expect((await pane("r3")).getByText("Loaded premium, +75 %")).toBeVisible();
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(runRequests(server)).toEqual([
      '{"retriever_config":"r4"}',
      '{"retriever_config":"r5"}',
    ]);
    expect(screen.queryByRole("alert")).toBeNull();
    // One run is not done: nothing is marked as a difference.
    expect(screen.queryByText(DIFFERS)).toBeNull();
    expect(screen.queryByText(ONLY_HERE)).toBeNull();

    // Turned off and on again while the run is awaited: no row is asked
    // for a second time, and what was answered for `r5` is still known.
    await user.click(toggle);
    await user.click(toggle);
    expect(
      await (await pane("r5")).findByText("The suggestion is being made."),
    ).toBeVisible();
    expect(runRequests(server)).toHaveLength(2);

    // The run ends: the screen read again by itself, and asked for nothing more.
    server.held.runs = [run(), otherRun()];
    await act(() => vi.advanceTimersByTimeAsync(RESULT_POLL_MS));

    const second = await pane("r5");
    expect(await second.findByText("Standard rates")).toBeVisible();
    expect(second.getByText("Made with retrieval row r5.")).toBeVisible();
    expect(second.getByText(LABEL)).toBeVisible();
    expect(second.getByText("Confidence: 90%")).toBeVisible();
    // `standard` beside `loaded`: both verdict lines are marked.
    expect(second.getByText(DIFFERS)).toBeVisible();
    const one = await pane("r3");
    expect(one.getByText(DIFFERS)).toBeVisible();
    // The other run cites neither rule: both reasons are only in this run.
    expect(
      one
        .getAllByRole("listitem")
        .filter((item) => item.textContent?.startsWith("Rule "))
        .map((item) => item.textContent?.split("Facts it cites:")[0]),
    ).toEqual([
      `Rule UW-DM-002 Debit +50 % ${ONLY_HERE}`,
      `Rule UW-TOB-001 Debit +25 % ${ONLY_HERE}`,
    ]);
    // The rules each run retrieved, each once in the order first seen; a
    // rule both retrieved is not marked.
    expect(await retrieved("r3")).toEqual([
      `UW-DM-002 ${ONLY_HERE}`,
      "UW-DM-001",
      `UW-TOB-001 ${ONLY_HERE}`,
    ]);
    expect(await retrieved("r5")).toEqual(["UW-DM-001"]);
    expect(runRequests(server)).toHaveLength(2);
    // Both runs are final: nothing more is read.
    const before = server.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(RESULT_POLL_MS * 3));
    expect(server.calls.length).toBe(before);

    // Turned off: the result view as it was, one run on screen.
    await user.click(toggle);

    expect(
      screen.queryByRole("region", { name: /^Run with retrieval row/ }),
    ).toBeNull();
    const verdict = await verdictPane();
    expect(verdict.getByText("Made with retrieval row r3.")).toBeVisible();
    expect(screen.queryByText(DIFFERS)).toBeNull();
    expect(screen.queryByText(ONLY_HERE)).toBeNull();
  });

  it("shows two runs that exist at once without asking for one, and marks only what differs", async () => {
    const server = resultServer({
      runs: [
        run(),
        // The same verdict and loading, and one of the two rules.
        otherRun({
          verdict: "loaded",
          loading_pct: 75,
          reasons: [run().reasons[0]],
        }),
      ],
      pairs: () =>
        json(200, {
          default_pair: { first: "r3", second: "r5" },
          fallback_pair: { first: "r1", second: "r2" },
        }),
      // The steps of neither run can be read.
      steps: () =>
        json(502, errorBody("upstream_unavailable", "Not available.")),
    });
    const user = userEvent.setup();
    openResult();
    // The second run is picked for the screen before Compare is turned on.
    await user.selectOptions(
      await screen.findByRole("combobox", {
        name: "Suggestion on screen, by retrieval row",
      }),
      "r5 (done)",
    );

    await user.click(await screen.findByRole("button", { name: TOGGLE }));

    const one = await pane("r3");
    const second = await pane("r5");
    expect(one.getByText("Loaded premium, +75 %")).toBeVisible();
    expect(second.getByText("Loaded premium, +75 %")).toBeVisible();
    expect(runRequests(server)).toEqual([]);
    expect(screen.queryByText(DIFFERS)).toBeNull();
    // Only the reason whose rule the other run does not cite is marked.
    expect(one.getAllByText(ONLY_HERE)).toHaveLength(1);
    expect(
      one
        .getAllByRole("listitem")
        .find((item) => item.textContent?.startsWith("Rule UW-TOB-001"))
        ?.textContent,
    ).toContain(ONLY_HERE);
    expect(second.queryByText(ONLY_HERE)).toBeNull();
    // Retrieved rules that could not be read are said so, and mark nothing.
    expect(
      await one.findByText("The rules it retrieved could not be read."),
    ).toBeVisible();

    // The same verdict with another loading: both verdict lines are marked.
    server.held.runs = [
      run(),
      otherRun({ verdict: "loaded", loading_pct: 50, reasons: run().reasons }),
    ];
    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(
      await (await pane("r5")).findByText("Loaded premium, +50 %"),
    ).toBeVisible();
    expect((await pane("r3")).getByText(DIFFERS)).toBeVisible();
    expect((await pane("r5")).getByText(DIFFERS)).toBeVisible();

    // Turned off: the run that was picked before is the one on screen.
    await user.click(screen.getByRole("button", { name: TOGGLE }));
    expect(
      (await verdictPane()).getByText("Made with retrieval row r5."),
    ).toBeVisible();
  });

  it("says that Compare is not available, with the rows, when a row of the fallback pair is refused too", async () => {
    const server = resultServer({
      requestRun: () =>
        json(
          409,
          errorBody("retriever_not_available", "That row is not available."),
        ),
    });
    const user = userEvent.setup();
    const view = openResult();

    await user.click(await screen.findByRole("button", { name: TOGGLE }));

    expect(
      await screen.findByText(
        "Compare is not available here. It needs retrieval rows r4 and r5, or r3 and r5, and at least one row of each pair cannot be run here.",
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("region", { name: /^Run with retrieval row/ }),
    ).toBeNull();
    // Nothing after a refused row was asked for, and no row twice.
    expect(runRequests(server)).toEqual([
      '{"retriever_config":"r4"}',
      '{"retriever_config":"r5"}',
    ]);
    view.unmount();

    // Any other failure is an error, and Compare can be tried again.
    const good = server.held.pairs;
    const failing = resultServer({
      pairs: () =>
        json(502, errorBody("upstream_unavailable", "Not available.")),
    });
    const again = openResult();
    await user.click(await screen.findByRole("button", { name: TOGGLE }));
    expect(
      await screen.findByText(
        "The service is not available right now. Please try again.",
      ),
    ).toBeVisible();
    expect(runRequests(failing)).toEqual([]);
    failing.held.pairs = good;
    await user.click(screen.getByRole("button", { name: "Try Compare again" }));
    expect(
      await (await pane("r5")).findByText("The suggestion is being made."),
    ).toBeVisible();
    again.unmount();

    // A case that is not finished has no Compare toggle.
    resultServer({ status: "awaiting_human", runs: [] });
    openResult();
    await (await verdictPane()).findByText("No suggestion yet.");
    expect(screen.queryByRole("button", { name: TOGGLE })).toBeNull();
  });

  it("shows a failed run's failure in its pane and marks nothing as a difference", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // The run ended before `verdict` stored one: `workflow` says why.
    const server = resultServer({
      requestRun: (row) =>
        row === "r4"
          ? json(
              409,
              errorBody(
                "retriever_not_available",
                "That row is not available.",
              ),
            )
          : json(
              200,
              runRequested(row, {
                status: "failed",
                error_code: "model_unavailable",
              }),
            ),
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const view = openResult();
    await user.click(await screen.findByRole("button", { name: TOGGLE }));

    const failed = await pane("r5");
    expect(failed.getByRole("alert").textContent).toMatch(/^This run failed\./);
    expect((await pane("r3")).getByText("Loaded premium, +75 %")).toBeVisible();
    expect(screen.queryByText(DIFFERS)).toBeNull();
    expect(screen.queryByText(ONLY_HERE)).toBeNull();
    // Nothing is awaited: the screen does not read again, and asks for no run again.
    const before = server.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(RESULT_POLL_MS * 3));
    expect(server.calls.length).toBe(before);
    view.unmount();

    // A failed run that `verdict` lists is shown as it is, and is not asked for.
    const listed = resultServer({
      runs: [
        run(),
        otherRun({
          status: "failed",
          verdict: null,
          confidence: null,
          error_code: "invalid_model_output",
        }),
      ],
    });
    openResult();
    await user.click(await screen.findByRole("button", { name: TOGGLE }));

    expect(
      await (
        await pane("r5")
      ).findByText("This run failed. The model's answer could not be used."),
    ).toBeVisible();
    expect(runRequests(listed)).toEqual(['{"retriever_config":"r4"}']);
    expect(screen.queryByText(DIFFERS)).toBeNull();
    expect(screen.queryByText(ONLY_HERE)).toBeNull();
    cleanup();

    // A run that was asked for and is never listed: the reading for it has
    // an end, and the pane says so.
    const never = resultServer();
    openResult();
    await user.click(await screen.findByRole("button", { name: TOGGLE }));
    await (await pane("r5")).findByText("The suggestion is being made.");
    await act(() =>
      vi.advanceTimersByTimeAsync(RESULT_POLL_MS * AWAITED_RUN_READS),
    );
    expect(
      (await pane("r5")).getByText(/^The run has not appeared/),
    ).toBeVisible();
    const after = never.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(RESULT_POLL_MS * 3));
    expect(never.calls.length).toBe(after);
    expect(runRequests(never)).toHaveLength(2);
  });
});
