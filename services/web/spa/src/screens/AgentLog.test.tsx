import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { auditTrailPath } from "../audit/auditPath";
import { ROLE_STORAGE_KEY } from "../role/roleStore";
import {
  caseProgress,
  errorBody,
  fakeServer,
  json,
  type RecordedCall,
} from "../test/server";

const CASE = "019a0000-0000-7000-8000-000000010000";
const RUN = "019a0000-0000-7000-8000-000000000501";
const FACT = "019a0000-0000-7000-8000-000000000402";
const STEPS_PATH = `/api/verdict-runs/${RUN}/steps`;
const LONG_QUERY = `HbA1c ${"reading ".repeat(20)}<b>7.4 %</b>`;

/** One step of the run, as `verdict` logs it: a done search unless changed. */
function step(stepNo: number, changes: Record<string, unknown> = {}) {
  return {
    verdict_run_id: RUN,
    case_id: CASE,
    step_no: stepNo,
    tool: "search_rules",
    arguments: { query: "HbA1c 7.4 %", fact_id: FACT } as Record<
      string,
      unknown
    >,
    fact_id: FACT as string | null,
    rule_ids: ["UW-DM-002", "UW-DM-001"],
    outcome: "done",
    error_code: null as string | null,
    latency_ms: 19,
    occurred_at: "2026-10-08T09:00:00Z",
    ...changes,
  };
}

/** A run as the agent made it: the facts listed, a long search, a read, and a read it was refused. */
const RUN_STEPS = [
  step(1, { tool: "list_facts", arguments: {}, fact_id: null, rule_ids: [] }),
  step(2, { arguments: { query: LONG_QUERY, fact_id: FACT, top: 5 } }),
  step(3, {
    tool: "read_rule",
    arguments: { rule_id: "UW-DM-002" },
    fact_id: null,
    rule_ids: ["UW-DM-002"],
    latency_ms: 7,
  }),
  step(4, {
    tool: "read_rule",
    arguments: { rule_id: "UW-HT-003" },
    fact_id: null,
    rule_ids: [],
    outcome: "refused",
    error_code: "rule_not_seen",
    latency_ms: 0,
  }),
];

/**
 * A stand-in for the server that holds one completed case whose trail ends
 * with a suggested verdict, and that run's steps. `steps` answers a read of
 * the steps from its query.
 */
function logServer(steps: (asked: URLSearchParams) => Response) {
  return fakeServer((call) => {
    const [path, query] = call.path.split("?");
    if (path === `/api/cases/${CASE}/progress`) {
      return json(200, caseProgress(CASE, "completed", "done", []));
    }
    if (path === `/api/cases/${CASE}/audit`) {
      return json(200, {
        case_id: CASE,
        events: [
          {
            actor_kind: "ai",
            actor: "verdict:chat-main",
            action: "verdict.suggested",
            occurred_at: "2026-10-08T09:00:05Z",
            case_id: CASE,
            page_id: null,
            // AD-8: the event's reference is the run.
            ref: RUN,
            detail: { retriever_config: "r3" },
            trace_id: "0".repeat(32),
            eval_run_id: null,
            error_code: null,
          },
        ],
        has_more: false,
      });
    }
    if (path === STEPS_PATH) {
      return steps(new URLSearchParams(query ?? ""));
    }
    return undefined;
  });
}

function stepReads(server: { calls: RecordedCall[] }): string[] {
  return server.calls
    .filter((call) => call.path.split("?")[0] === STEPS_PATH)
    .map((call) => call.path.slice(STEPS_PATH.length));
}

async function openSteps(user: ReturnType<typeof userEvent.setup>) {
  window.localStorage.setItem(ROLE_STORAGE_KEY, "underwriter");
  render(
    <MemoryRouter initialEntries={[auditTrailPath(CASE)]}>
      <App />
    </MemoryRouter>,
  );
  const opener = await screen.findByRole("button", {
    name: "Show the agent's steps",
  });
  await user.click(opener);
  return opener;
}

/** The rows of the steps table, each as the text of its cells, the step number first. */
async function stepRows(): Promise<string[][]> {
  const table = await screen.findByRole("table", {
    name: `Agent steps of run ${RUN}`,
  });
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) =>
      [within(row).getByRole("rowheader"), ...within(row).getAllByRole("cell")]
        // The time is local to whoever looks: left out here.
        .slice(0, 7)
        .map((cell) => cell.textContent ?? ""),
    );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("2.8 the agent's log", () => {
  it("opens a run's steps from the trail's “verdict suggested” event, in order with every field, a refused call as visible as a done one", async () => {
    const server = logServer(() =>
      json(200, { steps: RUN_STEPS, has_more: false }),
    );
    const user = userEvent.setup();

    const opener = await openSteps(user);

    expect(
      await screen.findByRole("heading", {
        name: `The agent's steps of run ${RUN}`,
      }),
    ).toHaveFocus();
    expect(opener).toHaveAttribute("aria-expanded", "true");
    expect(await stepRows()).toEqual([
      ["1", "List the facts", "None", "None", "None", "Done", "19 ms"],
      [
        "2",
        "Search the manual",
        // A long value is cut; the whole value is under it, as text.
        `query: ${LONG_QUERY.slice(0, 80)}…${LONG_QUERY}fact_id: ${FACT}top: 5`,
        FACT,
        "UW-DM-002, UW-DM-001",
        "Done",
        "19 ms",
      ],
      [
        "3",
        "Read a rule",
        "rule_id: UW-DM-002",
        "None",
        "UW-DM-002",
        "Done",
        "7 ms",
      ],
      [
        "4",
        "Read a rule",
        "rule_id: UW-HT-003",
        "None",
        "None",
        "Refused. A rule was asked for that had not been found first.",
        "0 ms",
      ],
    ]);
    // security rule 22: what the model wrote is text, never an element.
    const table = screen.getByRole("table", {
      name: `Agent steps of run ${RUN}`,
    });
    expect(table.querySelector("b")).toBeNull();
    expect(within(table).getAllByRole("time")).toHaveLength(4);
    // One read of the run, as the underwriter, and nothing written.
    expect(stepReads(server)).toEqual([""]);
    expect(
      server.calls.filter((call) => call.method !== "GET").map((c) => c.path),
    ).toEqual([]);
    // Read only: nothing edits, removes or runs a step again.
    const steps = within(
      screen.getByRole("region", { name: `The agent's steps of run ${RUN}` }),
    );
    expect(
      steps.getAllByRole("button").map((button) => button.textContent),
    ).toEqual([
      "Show matching steps",
      "Read the steps again",
      "Close the steps",
    ]);

    await user.click(steps.getByRole("button", { name: "Close the steps" }));

    expect(screen.queryByRole("table", { name: /Agent steps/ })).toBeNull();
    expect(opener).toHaveFocus();
  });

  it("asks the server to narrow by tool and by rule, and for the steps after the last one shown", async () => {
    // The server narrows and cuts; the browser shows what it is given.
    const server = logServer((asked) => {
      if (asked.get("tool") === "read_rule") {
        return json(200, { steps: [RUN_STEPS[2]], has_more: false });
      }
      const after = Number(asked.get("after_step_no") ?? 0);
      const rest = RUN_STEPS.filter((one) => one.step_no > after);
      return json(200, { steps: rest.slice(0, 2), has_more: rest.length > 2 });
    });
    const user = userEvent.setup();
    await openSteps(user);

    // More than one answer holds: the first steps, and a way to the rest.
    expect((await stepRows()).map((cells) => cells[0])).toEqual(["1", "2"]);
    expect(
      screen.getByText("This run has more steps than are shown."),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Show more steps" }));
    await screen.findByRole("rowheader", { name: "4" });
    expect((await stepRows()).map((cells) => cells[0])).toEqual([
      "1",
      "2",
      "3",
      "4",
    ]);
    expect(
      screen.queryByRole("button", { name: "Show more steps" }),
    ).toBeNull();
    expect(stepReads(server)).toEqual(["", "?after_step_no=2"]);

    // A rule id of another form is refused here, with no call.
    const rule = screen.getByRole("textbox", { name: "Rule id" });
    await user.type(rule, "dm-2");
    await user.click(
      screen.getByRole("button", { name: "Show matching steps" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "That is not a rule id.",
    );
    expect(stepReads(server)).toHaveLength(2);

    // Both filters go to the server, and what it answers is what is shown.
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Tool" }),
      "Read a rule",
    );
    await user.clear(rule);
    await user.type(rule, " uw-dm-002 ");
    await user.click(
      screen.getByRole("button", { name: "Show matching steps" }),
    );
    await screen.findByRole("button", { name: "Show every step" });
    expect((await stepRows()).map((cells) => cells[0])).toEqual(["3"]);
    expect(stepReads(server).at(-1)).toBe("?tool=read_rule&rule_id=UW-DM-002");

    await user.click(screen.getByRole("button", { name: "Show every step" }));
    await screen.findByRole("rowheader", { name: "1" });
    expect(stepReads(server).at(-1)).toBe("");
  });

  it("shows a plain fault for a run that cannot be read, and none of an answer of another shape", async () => {
    let answer = () =>
      json(404, errorBody("not_found", "That verdict run could not be found."));
    logServer(() => answer());
    const user = userEvent.setup();
    await openSteps(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The steps of this run could not be found.",
    );

    // A step of another run, in an answer for this one: not shown.
    answer = () =>
      json(200, {
        steps: [step(1, { verdict_run_id: CASE })],
        has_more: false,
      });
    await user.click(
      screen.getByRole("button", { name: "Read the steps again" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong",
    );
    expect(screen.queryByRole("table", { name: /Agent steps/ })).toBeNull();

    // A step listed twice, or out of order: not shown either.
    answer = () => json(200, { steps: [step(2), step(2)], has_more: false });
    await user.click(
      screen.getByRole("button", { name: "Read the steps again" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong",
    );
    expect(screen.queryByRole("table", { name: /Agent steps/ })).toBeNull();
  });
});
