import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { AUDIT_POLL_MS } from "../audit/auditTrail";
import { auditTrailPath, parseCaseId } from "../audit/auditPath";
import { backoffMs } from "../polling/backoff";
import { ROLE_STORAGE_KEY } from "../role/roleStore";
import {
  caseProgress,
  errorBody,
  fakeServer,
  json,
  pageProgress,
  triagePage,
  type RecordedCall,
} from "../test/server";

const CASE = "019a0000-0000-7000-8000-000000010000";
const OTHER_CASE = "019a0000-0000-7000-8000-000000020000";
const TRACE_ID = "0af7651916cd43dd8448eb211c80319c";
const AUDIT_PATH = `/api/cases/${CASE}/audit`;
const PROGRESS_PATH = `/api/cases/${CASE}/progress`;

type Event = ReturnType<typeof auditEvent>;

let refs = 0;

/** One event of a trail, as `workflow` answers it; an AI step unless said otherwise. */
function auditEvent(
  action: string,
  actor: string,
  pageNumber: number | null,
  changes: Record<string, unknown> = {},
) {
  refs += 1;
  return {
    actor_kind:
      actor === "customer" || actor === "underwriter" ? "human" : "ai",
    actor,
    action,
    occurred_at: "2026-10-07T09:00:00Z",
    case_id: CASE,
    page_id:
      pageNumber === null ? null : pageProgress(pageNumber, "uploaded").page_id,
    ref: `019a0000-0000-7000-8000-${String(refs).padStart(12, "0")}`,
    detail: null as Record<string, unknown> | null,
    trace_id: TRACE_ID,
    eval_run_id: null,
    error_code: null as string | null,
    ...changes,
  };
}

/** The trail of a case run to decisions: one page discarded, one kept and then accepted. */
function decidedTrail(): Event[] {
  return [
    auditEvent("document.redacted", "intake:azure-ai-language", null, {
      detail: { Person: 2, PhoneNumber: 1 },
      occurred_at: "2026-10-07T09:00:01Z",
    }),
    auditEvent("page.classified", "classification:chat-main", 1),
    auditEvent("page.classified", "classification:chat-main", 2),
    auditEvent("page.routed", "workflow:gate", 1, {
      detail: { route: "awaiting_customer", threshold: 0.9 },
    }),
    auditEvent("page.routed", "workflow:gate", 2, {
      detail: { route: "awaiting_customer", threshold: 0.9 },
    }),
    auditEvent("page.discarded", "customer", 1),
    auditEvent("page.kept", "customer", 2),
    auditEvent("page.accepted", "underwriter", 2),
  ];
}

/**
 * A stand-in for the server that holds one case: its progress and its
 * trail. Both can be changed while the screen is open.
 */
function trailServer(
  events: Event[],
  options: {
    caseStatus?: string;
    pages?: ReturnType<typeof pageProgress>[];
    hasMore?: boolean;
    respond?: (call: RecordedCall) => Response | undefined;
  } = {},
) {
  const held = {
    events,
    caseStatus: options.caseStatus ?? "running",
    pages: options.pages ?? [
      pageProgress(1, "discarded"),
      pageProgress(2, "extracting"),
    ],
    hasMore: options.hasMore ?? false,
  };
  const server = fakeServer((call) => {
    const answer = options.respond?.(call);
    if (answer !== undefined) {
      return answer;
    }
    if (call.path === PROGRESS_PATH) {
      return json(200, caseProgress(CASE, held.caseStatus, "done", held.pages));
    }
    if (call.path === AUDIT_PATH && call.role === "underwriter") {
      return json(200, {
        case_id: CASE,
        events: held.events,
        has_more: held.hasMore,
      });
    }
    return undefined;
  });
  return { ...server, held };
}

/** The browser's back and forward, which a test has no other way to press. */
function History() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => void navigate(-1)}>
        test: back
      </button>
      <button type="button" onClick={() => void navigate(1)}>
        test: forward
      </button>
    </>
  );
}

function open(path: string, role = "underwriter") {
  window.localStorage.setItem(ROLE_STORAGE_KEY, role);
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
      <History />
    </MemoryRouter>,
  );
}

/** The case id field as it is now: a new address mounts a new one. */
function field(): HTMLElement {
  return screen.getByRole("textbox", { name: "Case id" });
}

function openTrail(caseId = CASE) {
  return open(auditTrailPath(caseId));
}

/** The rows of the trail, each as the text of its cells. */
async function rows(caseId = CASE): Promise<string[][]> {
  const table = await screen.findByRole("table", {
    name: `Audit trail of case ${caseId}`,
  });
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) =>
      within(row)
        .getAllByRole("cell")
        .map((cell) => cell.textContent ?? ""),
    );
}

function reads(server: { calls: RecordedCall[] }, path = AUDIT_PATH): number {
  return server.calls.filter((call) => call.path === path).length;
}

beforeEach(() => {
  refs = 0;
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("1.12 the audit trail of a case", () => {
  it("lists every step of a decided case in the server's order, with actor, action, page and time", async () => {
    const events = decidedTrail();
    const server = trailServer(events);

    openTrail();

    const listed = await rows();
    // Actor, action, page and detail of each event, as the server ordered them.
    expect(listed.map((cells) => cells.slice(1))).toEqual([
      [
        "Intake service, redaction service azure-ai-language",
        "Document redacted",
        "Whole case",
        "Redacted: Person 2, PhoneNumber 1.",
      ],
      [
        "Classification service, model deployment chat-main",
        "Page classified (page 1)",
        "Page 1",
        "",
      ],
      [
        "Classification service, model deployment chat-main",
        "Page classified (page 2)",
        "Page 2",
        "",
      ],
      [
        "Workflow service, rule gate",
        "Page sent on by the gate (page 1)",
        "Page 1",
        "Sent to the customer, to keep or discard. Confidence the gate asked for: 90%.",
      ],
      [
        "Workflow service, rule gate",
        "Page sent on by the gate (page 2)",
        "Page 2",
        "Sent to the customer, to keep or discard. Confidence the gate asked for: 90%.",
      ],
      ["Customer (a person)", "Page discarded (page 1)", "Page 1", ""],
      ["Customer (a person)", "Page kept (page 2)", "Page 2", ""],
      ["Underwriter (a person)", "Page accepted (page 2)", "Page 2", ""],
    ]);
    // Every call was made as the underwriter.
    expect(reads(server)).toBeGreaterThan(0);
    expect(server.calls.every((call) => call.role === "underwriter")).toBe(
      true,
    );
  });

  it("1.13 words the start and the completion of a case in plain language", async () => {
    const events = [
      auditEvent("case.started", "customer", null, { ref: CASE }),
      ...decidedTrail(),
      auditEvent("case.completed", "workflow:case-lifecycle", null, {
        ref: CASE,
      }),
    ];
    trailServer(events, {
      caseStatus: "completed",
      pages: [pageProgress(1, "discarded"), pageProgress(2, "denied")],
    });

    openTrail();

    const listed = (await rows()).map((cells) => cells.slice(1));
    // Who started the case comes first, and that it was completed comes last.
    expect(listed[0]).toEqual([
      "Customer (a person)",
      "Case started",
      "Whole case",
      "",
    ]);
    expect(listed.at(-1)).toEqual([
      "Workflow service, the case's lifecycle",
      "Case completed",
      "Whole case",
      "",
    ]);
    expect(listed).toHaveLength(events.length);
    expect(screen.getByText(/Case status: Completed\./)).toBeVisible();
  });

  it("1.13 names the underwriter when it was the underwriter who started the case", async () => {
    trailServer([
      auditEvent("case.started", "underwriter", null, { ref: CASE }),
    ]);

    openTrail();

    expect((await rows()).map((cells) => cells.slice(1))).toEqual([
      ["Underwriter (a person)", "Case started", "Whole case", ""],
    ]);
  });

  it("shows each time in the viewer's local time, with the UTC time as its title", async () => {
    trailServer(decidedTrail());

    openTrail();
    await rows();

    const table = screen.getByRole("table");
    const time = table.querySelector("time");
    expect(time).not.toBeNull();
    expect(time).toHaveAttribute("datetime", "2026-10-07T09:00:01Z");
    expect(time).toHaveAttribute("title", "2026-10-07 09:00:01 UTC");
    expect(time).toHaveTextContent(
      new Date("2026-10-07T09:00:01Z").toLocaleString(),
    );
  });

  it("keeps the server's order when a route names an earlier time than its classification", async () => {
    trailServer([
      auditEvent("page.classified", "classification:chat-main", 1, {
        occurred_at: "2026-10-07T09:05:00Z",
      }),
      // Another clock set this time: earlier than the event it follows.
      auditEvent("page.routed", "workflow:gate", 1, {
        occurred_at: "2026-10-07T09:00:00Z",
        detail: { route: "awaiting_triage", threshold: 0.9 },
      }),
    ]);

    openTrail();

    const listed = await rows();
    expect(listed.map((cells) => cells[2])).toEqual([
      "Page classified (page 1)",
      "Page sent on by the gate (page 1)",
    ]);
    expect(listed[1]![4]).toBe(
      "Sent to the underwriter's triage queue. Confidence the gate asked for: 90%.",
    );
    const times = [...screen.getByRole("table").querySelectorAll("time")];
    expect(times.map((time) => time.getAttribute("datetime"))).toEqual([
      "2026-10-07T09:05:00Z",
      "2026-10-07T09:00:00Z",
    ]);
  });

  it("shows a failed step with its error code in plain words, and an unknown code as it is", async () => {
    trailServer(
      [
        auditEvent("stage.failed", "classification:chat-main", 2, {
          error_code: "model_unavailable",
        }),
        auditEvent("stage.failed", "workflow:case-lifecycle", null, {
          error_code: "a_code_of_tomorrow",
        }),
        auditEvent("stage.failed", "intake:azure-ai-language", null, {
          trace_id: "0".repeat(32),
        }),
      ],
      { caseStatus: "failed", pages: [pageProgress(2, "failed")] },
    );

    openTrail();

    expect((await rows()).map((cells) => cells.slice(1))).toEqual([
      [
        "Classification service, model deployment chat-main",
        "Step failed (page 2)",
        "Page 2",
        `Reason: The model was not available. Reference: ${TRACE_ID}`,
      ],
      [
        "Workflow service, the case's lifecycle",
        "Step failed",
        "Whole case",
        `Reason: a_code_of_tomorrow Reference: ${TRACE_ID}`,
      ],
      [
        "Intake service, redaction service azure-ai-language",
        "Step failed",
        "Whole case",
        // No trace: nothing to refer to.
        "No reason was recorded.",
      ],
    ]);
    expect(screen.getByText(/Case status: Failed\./)).toBeVisible();
  });

  it("shows an action, a service or a page this build does not know as the server named it", async () => {
    trailServer(
      [
        auditEvent("page.reviewed", "pricing:chat-main", 7),
        auditEvent("document.redacted", "intake:azure-ai-language", null, {
          detail: {},
        }),
        auditEvent("page.routed", "workflow:gate", 1, { detail: null }),
        auditEvent("page.kept", "auditor", 1, { actor_kind: "human" }),
        auditEvent("page.classified", "nobody", 1, {
          occurred_at: "not a time",
        }),
      ],
      { pages: [pageProgress(1, "classified")] },
    );

    openTrail();

    const listed = await rows();
    expect(listed[0]!.slice(1)).toEqual([
      // Neither part is given a name it may not have.
      "pricing, chat-main",
      "page.reviewed",
      pageProgress(7, "uploaded").page_id,
      "",
    ]);
    expect(listed[1]![4]).toBe("Nothing was redacted.");
    expect(listed[2]![4]).toBe("");
    expect(listed[3]![1]).toBe("auditor (a person)");
    expect(listed[4]!.slice(0, 2)).toEqual(["not a time", "nobody"]);
  });

  it("says “No such case” for a case that was never started, and reads no more", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const server = fakeServer();

    openTrail();

    expect(await screen.findByRole("alert")).toHaveTextContent("No such case.");
    expect(screen.queryByRole("table")).toBeNull();
    const before = server.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(AUDIT_POLL_MS * 3));
    expect(server.calls.length).toBe(before);

    // The user may ask again: the case may have been started since.
    server.started.add(CASE);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await user.click(screen.getByRole("button", { name: "Check again" }));

    expect(await screen.findByText("No events yet.")).toBeVisible();
  });

  it("says “No such case” when only the trail answers 404", async () => {
    trailServer([], {
      respond: (call) =>
        call.path === AUDIT_PATH
          ? json(404, errorBody("not_found", "That case could not be found."))
          : undefined,
    });

    openTrail();

    expect(await screen.findByRole("alert")).toHaveTextContent("No such case.");
  });

  it("refuses text that is not a case id in the field, and makes no call", async () => {
    const server = trailServer(decidedTrail());
    const user = userEvent.setup();
    open("/underwriter/audit");
    const callsBefore = server.calls.length;

    await user.type(field(), "case-001");
    await user.click(screen.getByRole("button", { name: "Show the trail" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "That is not a case id.",
    );
    expect(field()).toHaveValue("case-001");
    expect(field()).toBeInvalid();
    expect(field()).toHaveAccessibleDescription(/That is not a case id\./);
    expect(server.calls.length).toBe(callsBefore);
    expect(screen.queryByRole("table")).toBeNull();

    // A case id, pasted with spaces around it and in capitals, is taken.
    await user.clear(field());
    await user.click(field());
    await user.paste(`  ${CASE.toUpperCase()} `);
    await user.click(screen.getByRole("button", { name: "Show the trail" }));

    expect(await rows()).toHaveLength(8);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(field()).toHaveValue(CASE);
    expect(field()).toBeValid();
  });

  it("follows the address back and forward between two cases, field and trail together", async () => {
    const server = trailServer(decidedTrail());
    // The other case is started and has no events yet.
    server.started.add(OTHER_CASE);
    const user = userEvent.setup();
    openTrail();
    expect(await rows()).toHaveLength(8);

    await user.clear(field());
    await user.type(field(), OTHER_CASE);
    await user.click(screen.getByRole("button", { name: "Show the trail" }));
    expect(await screen.findByText("No events yet.")).toBeVisible();
    expect(field()).toHaveValue(OTHER_CASE);

    await user.click(screen.getByRole("button", { name: "test: back" }));

    expect(await rows()).toHaveLength(8);
    expect(field()).toHaveValue(CASE);
    expect(screen.queryByText("No events yet.")).toBeNull();

    await user.click(screen.getByRole("button", { name: "test: forward" }));

    expect(await screen.findByText("No events yet.")).toBeVisible();
    expect(field()).toHaveValue(OTHER_CASE);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("starts over, with an empty field, when the navigation link is followed from a trail", async () => {
    trailServer(decidedTrail());
    const user = userEvent.setup();
    openTrail();
    await rows();
    // Something half typed, and then the link.
    await user.type(field(), "abc");

    await user.click(screen.getByRole("link", { name: "Audit trail" }));

    await waitFor(() => expect(field()).toHaveValue(""));
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(field()).toBeValid();
  });

  it("drops the refusal when the address goes back to a case after a refused submit", async () => {
    trailServer(decidedTrail());
    const user = userEvent.setup();
    openTrail();
    await rows();

    await user.clear(field());
    await user.type(field(), "case-001");
    await user.click(screen.getByRole("button", { name: "Show the trail" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "That is not a case id.",
    );
    expect(screen.queryByRole("table")).toBeNull();

    await user.click(screen.getByRole("button", { name: "test: back" }));

    expect(await rows()).toHaveLength(8);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(field()).toHaveValue(CASE);
    expect(field()).toBeValid();

    // And forward again: the refused text and its refusal are back.
    await user.click(screen.getByRole("button", { name: "test: forward" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That is not a case id.",
    );
    expect(field()).toHaveValue("case-001");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("refuses an address that names something that is not a case id, and makes no call", () => {
    const server = trailServer(decidedTrail());

    open("/underwriter/audit?case=..%2F..%2Fme");

    expect(screen.getByRole("alert")).toHaveTextContent(
      "That is not a case id.",
    );
    expect(screen.getByRole("textbox", { name: "Case id" })).toHaveValue(
      "../../me",
    );
    expect(server.calls.filter((call) => call.path !== "/api/me")).toEqual([]);
  });

  it("drops the trail that was shown when the next thing typed is not a case id", async () => {
    trailServer(decidedTrail());
    const user = userEvent.setup();
    openTrail();
    await rows();

    await user.clear(field());
    await user.click(screen.getByRole("button", { name: "Show the trail" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "That is not a case id.",
    );
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("is not a screen of the customer: no link, no route, no call", async () => {
    const server = trailServer(decidedTrail());

    open(auditTrailPath(CASE), "customer");

    expect(
      await screen.findByRole("heading", { name: "Page not found" }),
    ).toBeVisible();
    const navigation = screen.getByRole("navigation", { name: "Screens" });
    expect(
      within(navigation).queryByRole("link", { name: "Audit trail" }),
    ).toBeNull();
    expect(reads(server)).toBe(0);
    expect(reads(server, PROGRESS_PATH)).toBe(0);
  });

  it("shows the server's refusal when the trail is not open to the role", async () => {
    trailServer([], {
      respond: (call) =>
        call.path === AUDIT_PATH
          ? json(
              403,
              errorBody(
                "role_not_allowed",
                "This action is not open to your role.",
                TRACE_ID,
              ),
            )
          : undefined,
    });

    openTrail();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This action is not open to your role.");
    expect(alert).toHaveTextContent(`Reference: ${TRACE_ID}`);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("lists the first events of a long trail, says that more exist, and reads no more", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // The case still moves, but no later read could list anything new.
    const server = trailServer(decidedTrail().slice(0, 3), {
      hasMore: true,
      caseStatus: "awaiting_human",
    });

    openTrail();

    expect(await rows()).toHaveLength(3);
    expect(
      screen.getByText(
        "This case has more events than are shown here. Only the first ones are listed.",
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Case status: Waiting for a decision. Only the first events of this case are shown, so the trail is not read again.",
      ),
    ).toBeVisible();
    expect(screen.queryByText(/New events appear here/)).toBeNull();
    const after = server.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(AUDIT_POLL_MS * 4));
    expect(server.calls.length).toBe(after);
  });

  it("shows the gate's number exactly as recorded, not rounded", async () => {
    trailServer(
      [
        auditEvent("page.routed", "workflow:gate", 1, {
          detail: { route: "extracting", threshold: 0.925 },
        }),
        auditEvent("page.routed", "workflow:gate", 2, {
          detail: { route: "awaiting_triage", threshold: 1 },
        }),
      ],
      { pages: [pageProgress(1, "extracting"), pageProgress(2, "denied")] },
    );

    openTrail();

    expect((await rows()).map((cells) => cells[4])).toEqual([
      "Sent to extraction. Confidence the gate asked for: 92.5%.",
      "Sent to the underwriter's triage queue. Confidence the gate asked for: 100%.",
    ]);
  });

  it("2.5 says which retrieval row a suggested verdict was made with", async () => {
    trailServer(
      [
        auditEvent("verdict.suggested", "verdict:chat-main", null, {
          detail: { retriever_config: "r3" },
        }),
      ],
      { pages: [pageProgress(1, "extracted")] },
    );

    openTrail();

    expect((await rows()).map((cells) => cells[4])).toEqual([
      "Made with retrieval row r3.",
    ]);
  });

  it("does not ask again after a refusal no repeat can mend, until the user does", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let refusing = true;
    const server = trailServer(decidedTrail(), {
      respond: (call) =>
        refusing && call.path === AUDIT_PATH
          ? json(
              403,
              errorBody(
                "role_not_allowed",
                "This action is not open to your role.",
              ),
            )
          : undefined,
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    openTrail();
    await screen.findByRole("alert");
    const after = server.calls.length;

    // Far longer than the longest wait between reads that are tried again.
    await act(() => vi.advanceTimersByTimeAsync(backoffMs(10) * 3));
    expect(server.calls.length).toBe(after);

    refusing = false;
    await user.click(screen.getByRole("button", { name: "Check again" }));

    expect(await rows()).toHaveLength(8);
  });

  it("goes on asking after too many requests, and after a fault of the server", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const answers = [
      json(429, errorBody("too_many_requests", "Too many requests.")),
      json(502, errorBody("upstream_unavailable", "Not available.")),
    ];
    trailServer(decidedTrail(), {
      respond: (call) =>
        call.path === PROGRESS_PATH ? answers.shift() : undefined,
    });
    openTrail();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many requests. Please wait a moment and try again.",
    );

    await act(() => vi.advanceTimersByTimeAsync(backoffMs(1) + AUDIT_POLL_MS));
    await act(() => vi.advanceTimersByTimeAsync(backoffMs(2) + AUDIT_POLL_MS));

    expect(await rows()).toHaveLength(8);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says so when the case has no events yet", async () => {
    trailServer([], { pages: [] });

    openTrail();

    expect(await screen.findByText("No events yet.")).toBeVisible();
    expect(
      screen.getByText(
        "Case status: Running. New events appear here as the case moves.",
      ),
    ).toBeVisible();
    expect(screen.queryByText(/more events than are shown/)).toBeNull();
  });

  it("shows new events by polling while the case moves, and stops when the case is final", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const events = decidedTrail();
    const server = trailServer(events.slice(0, 5), {
      caseStatus: "awaiting_human",
      pages: [
        pageProgress(1, "awaiting_customer"),
        pageProgress(2, "awaiting_customer"),
      ],
    });
    openTrail();
    expect(await rows()).toHaveLength(5);
    expect(
      screen.getByText(/Case status: Waiting for a decision\./),
    ).toBeVisible();

    // The customer answers elsewhere: the next read shows it, with no reload.
    server.held.events = events.slice(0, 7);
    await act(() => vi.advanceTimersByTimeAsync(AUDIT_POLL_MS));
    await waitFor(async () => expect(await rows()).toHaveLength(7));

    // The case ends: its last event is read, and then nothing is read again.
    server.held.events = events;
    server.held.caseStatus = "completed";
    await act(() => vi.advanceTimersByTimeAsync(AUDIT_POLL_MS));
    await waitFor(async () => expect(await rows()).toHaveLength(8));
    expect(
      screen.getByText(
        "Case status: Completed. The case is finished, so the trail is not read again.",
      ),
    ).toBeVisible();
    const after = server.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(AUDIT_POLL_MS * 4));
    expect(server.calls.length).toBe(after);
  });

  it("reads the case before its trail, so a final case's trail holds its last event", async () => {
    const server = trailServer(decidedTrail(), { caseStatus: "completed" });

    openTrail();
    await rows();

    const order = server.calls
      .map((call) => call.path)
      .filter((path) => path === AUDIT_PATH || path === PROGRESS_PATH);
    expect(order).toEqual([PROGRESS_PATH, AUDIT_PATH]);
  });

  it("reads nothing while the tab is hidden, and catches up when it is shown", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const server = trailServer(decidedTrail());
    let visibility: DocumentVisibilityState = "visible";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(
      () => visibility,
    );
    openTrail();
    await rows();
    const before = reads(server);

    visibility = "hidden";
    document.dispatchEvent(new Event("visibilitychange"));
    await act(() => vi.advanceTimersByTimeAsync(AUDIT_POLL_MS * 2));
    expect(reads(server)).toBe(before);

    visibility = "visible";
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(reads(server)).toBe(before + 1);
  });

  it("says so when the trail cannot be read, and reads it again on request", async () => {
    let failing = true;
    trailServer(decidedTrail(), {
      respond: (call) =>
        failing && call.path === AUDIT_PATH
          ? json(
              502,
              errorBody(
                "upstream_unavailable",
                "The service is not available.",
                TRACE_ID,
              ),
            )
          : undefined,
    });
    const user = userEvent.setup();
    openTrail();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The service is not available right now. Please try again.",
    );
    expect(screen.queryByRole("table")).toBeNull();

    failing = false;
    await user.click(screen.getByRole("button", { name: "Check again" }));

    expect(await rows()).toHaveLength(8);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps the trail it has when a later read fails, says it may be out of date, and reads further apart", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let failing = false;
    const server = trailServer(decidedTrail(), {
      respond: (call) =>
        failing && call.path === PROGRESS_PATH ? json(500, null) : undefined,
    });
    openTrail();
    await rows();

    failing = true;
    await act(() => vi.advanceTimersByTimeAsync(AUDIT_POLL_MS));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The trail could not be read again. What is shown may be out of date.",
    );
    expect(await rows()).toHaveLength(8);
    // After one failure the next read waits longer than one poll.
    const failed = reads(server, PROGRESS_PATH);
    await act(() => vi.advanceTimersByTimeAsync(AUDIT_POLL_MS));
    expect(reads(server, PROGRESS_PATH)).toBe(failed);

    failing = false;
    await act(() => vi.advanceTimersByTimeAsync(backoffMs(1)));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(await rows()).toHaveLength(8);
  });

  it("takes an answer that is not an audit trail for a fault", async () => {
    trailServer([], {
      respond: (call) =>
        call.path === AUDIT_PATH
          ? json(200, { case_id: CASE, events: [{ action: "page.kept" }] })
          : undefined,
    });

    openTrail();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong. Please try again.",
    );
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("is reached from a row of the triage queue, for that row's case", async () => {
    vi.stubGlobal(
      "createImageBitmap",
      vi.fn(async () => ({ width: 320, height: 453, close: () => undefined })),
    );
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
      () =>
        ({ drawImage: () => undefined }) as unknown as CanvasRenderingContext2D,
    );
    const waiting = [triagePage(OTHER_CASE, 1), triagePage(CASE, 2)];
    const server = trailServer(decidedTrail(), {
      respond: (call) =>
        call.path === "/api/triage"
          ? json(200, { pages: waiting, has_more: false })
          : undefined,
    });
    const user = userEvent.setup();
    open("/underwriter/triage");

    const link = await screen.findByRole("link", {
      name: `Audit trail of case ${CASE}`,
    });
    expect(link).toHaveAttribute("href", `/underwriter/audit?case=${CASE}`);
    expect(
      screen.getByRole("link", { name: `Audit trail of case ${OTHER_CASE}` }),
    ).toHaveAttribute("href", `/underwriter/audit?case=${OTHER_CASE}`);
    await user.click(link);

    expect(
      await screen.findByRole("heading", { name: "Audit trail" }),
    ).toBeVisible();
    expect(await rows()).toHaveLength(8);
    expect(screen.getByRole("textbox", { name: "Case id" })).toHaveValue(CASE);
    expect(reads(server)).toBeGreaterThan(0);
  });

  it("is listed in the underwriter's navigation, and opens with the field and no call", async () => {
    const server = trailServer(decidedTrail());
    const user = userEvent.setup();
    open("/underwriter");
    await screen.findByText("The server sees you as: Underwriter.");
    const callsBefore = server.calls.length;

    await user.click(screen.getByRole("link", { name: "Audit trail" }));

    expect(
      await screen.findByRole("heading", { name: "Audit trail" }),
    ).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Case id" })).toHaveValue("");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(server.calls.length).toBe(callsBefore);
  });

  it("takes a UUIDv7 for a case id, and nothing else", () => {
    expect(parseCaseId(CASE)).toBe(CASE);
    expect(parseCaseId(` ${CASE.toUpperCase()}\n`)).toBe(CASE);
    for (const text of [
      "",
      "case-001",
      // A version 4 id is no case id.
      "019a0000-0000-4000-8000-000000010000",
      `${CASE}/../progress`,
      `${CASE}0`,
    ]) {
      expect(parseCaseId(text)).toBeNull();
    }
  });
});
