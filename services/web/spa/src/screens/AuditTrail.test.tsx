import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { AUDIT_POLL_MS } from "../audit/auditTrail";
import { auditTrailPath } from "../audit/auditPath";
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

    // Story 2.8: the row also offers its run's steps.
    expect((await rows()).map((cells) => cells[4])).toEqual([
      "Made with retrieval row r3. Show the agent's steps",
    ]);
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
});
