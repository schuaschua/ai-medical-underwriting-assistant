import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { ROLE_STORAGE_KEY } from "../role/roleStore";
import { SCOREBOARD_PATH } from "../scoreboard/scoreboard";
import {
  classificationScoreboard,
  classifierScore,
  errorBody,
  fakeServer,
  json,
  redactionScoreboard,
  retrievalScoreboard,
  rowScore,
  SCOREBOARD_RUN,
} from "../test/server";

const RETRIEVAL = "/api/scoreboards/retrieval";
const REDACTION = "/api/scoreboards/redaction";
const CLASSIFICATION = "/api/scoreboards/classification";
const TRACE_ID = "0af7651916cd43dd8448eb211c80319c";
const NOT_FOUND = () => json(404, errorBody("not_found", "Not found."));

/** A stand-in for the server that holds the files; each can be changed while the screen is open. */
function scoreboardServer(
  retrieval: (() => Response) | object | null,
  redaction: (() => Response) | object | null = redactionScoreboard(),
  // Story 4.3: the classifier bake-off's file; not written unless given.
  classification: (() => Response) | object | null = null,
) {
  const held = { retrieval, redaction, classification };
  const answer = (file: (() => Response) | object | null) =>
    file === null
      ? NOT_FOUND()
      : typeof file === "function"
        ? (file as () => Response)()
        : json(200, file);
  const server = fakeServer((call) => {
    if (call.role !== "underwriter") {
      return undefined;
    }
    if (call.path === RETRIEVAL) {
      return answer(held.retrieval);
    }
    if (call.path === REDACTION) {
      return answer(held.redaction);
    }
    if (call.path === CLASSIFICATION) {
      return answer(held.classification);
    }
    return undefined;
  });
  return { ...server, held };
}

function openScoreboard(role = "underwriter") {
  window.localStorage.setItem(ROLE_STORAGE_KEY, role);
  return render(
    <MemoryRouter initialEntries={[SCOREBOARD_PATH]}>
      <App />
    </MemoryRouter>,
  );
}

/** The lines of a table, each as the text of its cells, the row first. */
async function lines(
  name = "Retrieval rows, in ladder order",
): Promise<string[][]> {
  const table = await screen.findByRole("table", { name });
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

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("3.5 the retrieval scoreboard", () => {
  it("shows a line per ladder row with its figures and the counts behind them, marks the winner, and says “not measured” without a number", async () => {
    const rows = [
      rowScore("r1", true, { chunk_set: "fixed", failed_runs: 2 }),
      rowScore("r2", true, {
        // Nothing was counted for these figures: no search, none timed.
        rule_recall: null,
        recall_hits: 0,
        recall_searches: 0,
        latency_ms_median: null,
        latency_ms_p95: null,
        latency_searches: 0,
      }),
      rowScore("r3", true, {
        method: "Vector and full text, fused by rank",
        effort: {
          amount: "3.00",
          unit: "stories built",
          source: "epics.md, stories 2.2 to 2.4",
        },
      }),
      // The file holds a stated figure for a row that was not measured.
      rowScore("r4", false, {
        effort: { amount: "4.00", unit: "stories built", source: "epics.md" },
      }),
      rowScore("r5", true, {
        store: "Azure AI Search",
        rule_recall: 1,
        recall_hits: 39,
        verdict_accuracy: 0.7273,
        right_runs: 16,
      }),
      rowScore("r6", false, { store: "Azure AI Search" }),
    ];
    const server = scoreboardServer(
      retrievalScoreboard({
        rows,
        unscored_cases: [{ case_key: "case-007" }, { case_key: "case-009" }],
        failed_searches: [{ retriever_config: "r2" }],
      }),
      redactionScoreboard({
        clean: false,
        cases_checked: 21,
        pages_checked: 90,
        leaks: [
          { case_key: "case-002", page_number: 3, category: "person_name" },
        ],
        cases_not_checked: ["case-007"],
      }),
    );

    openScoreboard();

    expect(await lines()).toEqual([
      [
        "r1",
        "pgvector",
        "fixed",
        "Vector only",
        "97.44% (38 of 39)",
        "68.18% (15 of 22)",
        // Runs that failed, apart from the runs that were right.
        "2 of 22",
        "20 ms median, 35 ms 95th percentile (39 searches)",
        "Not stated",
        "Not stated",
      ],
      [
        "r2",
        "pgvector",
        "smart",
        "Vector only",
        "No searches made",
        "68.18% (15 of 22)",
        "0 of 22",
        "No search timed",
        "Not stated",
        "Not stated",
      ],
      [
        "r3",
        "pgvector",
        "smart",
        "Vector and full text, fused by rank",
        "97.44% (38 of 39)",
        "68.18% (15 of 22)",
        "0 of 22",
        "20 ms median, 35 ms 95th percentile (39 searches)",
        "Not stated",
        "3.00 stories builtSourceepics.md, stories 2.2 to 2.4",
      ],
      // No number at all, whatever the file holds for the row.
      ["r4", "pgvector", "smart", "Vector only", "Not measured"],
      [
        // The winner the file names, marked in words.
        "r5 Winner",
        "Azure AI Search",
        "smart",
        "Vector only",
        "100% (39 of 39)",
        "72.73% (16 of 22)",
        "0 of 22",
        "20 ms median, 35 ms 95th percentile (39 searches)",
        "Not stated",
        "Not stated",
      ],
      ["r6", "Azure AI Search", "smart", "Vector only", "Not measured"],
    ]);
    expect(
      screen.getAllByRole("columnheader").map((header) => header.textContent),
    ).toEqual([
      "Row",
      "Store",
      "Chunk set",
      "Method",
      "Rule recall",
      "Verdict accuracy",
      "Runs failed or missing",
      "Latency",
      "Cost",
      "Effort",
    ]);
    expect(screen.getAllByText("Winner")).toHaveLength(1);
    expect(screen.queryByText(/4\.00/)).toBeNull();
    // A stated figure's source is there on request, not in the way.
    const source = screen.getByText("Source").closest("details");
    expect(source).not.toHaveAttribute("open");
    expect(source).toHaveTextContent("epics.md, stories 2.2 to 2.4");

    // When and against which address, and that these are stand-in figures.
    const ended = document.querySelector("time");
    expect(ended).toHaveAttribute("datetime", SCOREBOARD_RUN.finished_at);
    expect(ended).toHaveAttribute("title", "2026-10-08 09:10:05 UTC");
    expect(screen.getByText(/against http:\/\/localhost:8000\./)).toBeVisible();
    expect(screen.getByRole("note")).toHaveTextContent(
      "These are stand-in figures, not results.",
    );
    expect(
      screen.getByText(
        "2 cases were not scored. They count as wrong for every row.",
      ),
    ).toBeVisible();
    expect(
      screen.getByText("1 search failed. It counts as a miss for its row."),
    ).toBeVisible();
    // What a failed run means for the accuracy beside it, and for the winner.
    expect(
      screen.getByText(
        /It still counts as wrong in the verdict accuracy beside it, so it can decide the winner\./,
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Not clean. 90 pages of 21 cases checked. 1 leak found. 1 case was not checked.",
      ),
    ).toBeVisible();

    // Read once, as the underwriter: a file does not change while it is shown.
    const reads = server.calls.filter((call) =>
      call.path.startsWith("/api/scoreboards/"),
    );
    expect(reads.map((call) => [call.path, call.method, call.role])).toEqual([
      [RETRIEVAL, "GET", "underwriter"],
      // Story 4.3: the classifier file is asked for beside it.
      [CLASSIFICATION, "GET", "underwriter"],
      [REDACTION, "GET", "underwriter"],
    ]);
  });

  it("marks no row when the file names no winner, and says nothing of stand-ins for a run that was none", async () => {
    const deployed = {
      ...SCOREBOARD_RUN,
      eval_run_id: "0199b7a0-0000-7000-8000-000000000007",
      stand_ins: false,
      web_address: "https://web.example.test",
    };
    // The redaction report on file is a clean one of another, local run.
    const server = scoreboardServer(
      retrievalScoreboard({
        run: deployed,
        rows: ["r1", "r2", "r3", "r4", "r5", "r6"].map((config) =>
          rowScore(config, false),
        ),
        winner: null,
      }),
    );

    openScoreboard();

    const listed = await lines();
    expect(listed.map((cells) => cells[0])).toEqual([
      "r1",
      "r2",
      "r3",
      "r4",
      "r5",
      "r6",
    ]);
    expect(listed.every((cells) => cells.at(-1) === "Not measured")).toBe(true);
    expect(screen.queryByText("Winner")).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
    expect(screen.queryByText(/stand-in/)).toBeNull();
    expect(
      screen.queryByText(
        /not scored|search failed|searches failed|decide the winner/,
      ),
    ).toBeNull();
    expect(
      screen.getByText(/against https:\/\/web\.example\.test\./),
    ).toBeVisible();
    // The report of another run is said to be that, and not read as this
    // run's "clean".
    expect(
      screen.getByText(
        `Not available for this run. The redaction report on file is of another bake-off run (${SCOREBOARD_RUN.eval_run_id}) and says nothing about the figures above.`,
      ),
    ).toBeVisible();
    expect(screen.queryByText(/Clean/)).toBeNull();

    // The report of the same run is this run's line.
    server.held.redaction = redactionScoreboard({ run: deployed });
    await userEvent.click(screen.getByRole("button", { name: "Read again" }));
    expect(
      await screen.findByText(
        "Clean: no planted identifier was found in a page text. 94 pages of 22 cases checked.",
      ),
    ).toBeVisible();
  });

  it("says that the bake-off has not been run when there is no file, with no table, and reads again on request", async () => {
    const server = scoreboardServer(null, null);

    openScoreboard();

    expect(
      await screen.findByText("The retrieval bake-off has not been run yet."),
    ).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    // The redaction report is not asked for when there is no scoreboard.
    expect(server.calls.some((call) => call.path === REDACTION)).toBe(false);

    // The run was made meanwhile; its redaction report is not there.
    server.held.retrieval = retrievalScoreboard();
    await userEvent.click(screen.getByRole("button", { name: "Read again" }));

    expect(await lines()).toHaveLength(6);
    expect(
      screen.queryByText("The retrieval bake-off has not been run yet."),
    ).toBeNull();
    // The table stands without the report, which is said to be not available.
    const redaction = screen
      .getByRole("heading", { name: "Redaction check" })
      .closest("section");
    expect(redaction).toHaveTextContent("Not available.");
  });

  it("shows an error and no table for a file that does not fit, from the server or in the answer itself", async () => {
    const server = scoreboardServer(() =>
      json(
        500,
        errorBody(
          "internal_error",
          "Something went wrong. Please try again.",
          TRACE_ID,
        ),
      ),
    );

    openScoreboard();

    expect(
      await screen.findByText("The scoreboard could not be shown."),
    ).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent(
      `Reference: ${TRACE_ID}`,
    );
    expect(screen.queryByRole("table")).toBeNull();
    expect(
      screen.queryByText("The retrieval bake-off has not been run yet."),
    ).toBeNull();

    // Answers that are no scoreboard: a row missing, a measured row without
    // its counts, a figure that is text. None is half drawn.
    for (const wrong of [
      retrievalScoreboard({ rows: retrievalScoreboard().rows.slice(0, 5) }),
      retrievalScoreboard({
        rows: retrievalScoreboard().rows.map((row) =>
          row.retriever_config === "r1" ? { ...row, failed_runs: null } : row,
        ),
      }),
      retrievalScoreboard({
        rows: retrievalScoreboard().rows.map((row) =>
          row.retriever_config === "r2"
            ? { ...row, rule_recall: "<b>1</b>" }
            : row,
        ),
      }),
      retrievalScoreboard({ winner: "r9" }),
    ]) {
      const reads = server.calls.length;
      server.held.retrieval = wrong;
      await userEvent.click(screen.getByRole("button", { name: "Read again" }));
      await waitFor(() => expect(server.calls.length).toBeGreaterThan(reads));
      expect(
        await screen.findByText("The scoreboard could not be shown."),
      ).toBeVisible();
      expect(screen.queryByRole("table")).toBeNull();
    }

    // A redaction report that does not fit leaves the table standing.
    server.held.retrieval = retrievalScoreboard();
    server.held.redaction = { ...redactionScoreboard(), clean: "yes" };
    await userEvent.click(screen.getByRole("button", { name: "Read again" }));

    expect(await lines()).toHaveLength(6);
    expect(
      screen.getByText("The redaction check could not be shown."),
    ).toBeVisible();
  });

  it("is a screen of the underwriter only: the customer has none and asks the server for nothing of it", async () => {
    const server = scoreboardServer(retrievalScoreboard());

    openScoreboard("customer");

    expect(
      await screen.findByRole("heading", { name: "Page not found" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("link", { name: "Scoreboard" }),
    ).not.toBeInTheDocument();
    expect(
      server.calls.some((call) => call.path.startsWith("/api/scoreboards")),
    ).toBe(false);
  });
});

describe("4.3 the classifier scoreboard", () => {
  it("shows both classifiers with their figures and the counts behind them, marks the winner, and stands without the retrieval file", async () => {
    // The retrieval bake-off has not been run: its file is not there.
    const server = scoreboardServer(
      null,
      null,
      classificationScoreboard({
        contenders: [
          classifierScore("llm", true, {
            cost_per_page: {
              amount: "0.01",
              unit: "USD per page",
              source: "Azure price list, 2026-10-20",
            },
          }),
          classifierScore("doc-intelligence", true, {
            accuracy: 0.9362,
            right_pages: 88,
            // No page was scored 0.90 or more: no calibration.
            calibration: null,
            confident_pages: 0,
            confident_right_pages: 0,
            queue_rate: 1,
            queued_pages: 94,
            pages_not_classified: 0,
          }),
        ],
        unscored_cases: [{ contender: "llm", case_key: "case-007" }],
      }),
    );

    openScoreboard();

    expect(await lines("Classifiers")).toEqual([
      [
        // The winner the file names, marked in words.
        "LLM Winner",
        "95.74% (90 of 94)",
        "97.5% (78 of 80)",
        "12.77% (12 of 94)",
        "2 of 94",
        "0.01 USD per pageSourceAzure price list, 2026-10-20",
      ],
      [
        "Document Intelligence",
        "93.62% (88 of 94)",
        "No page scored 90% or more",
        "100% (94 of 94)",
        "0 of 94",
        "Not stated",
      ],
    ]);
    expect(
      screen.getAllByRole("columnheader").map((header) => header.textContent),
    ).toEqual([
      "Classifier",
      "Accuracy",
      "Calibration",
      "Queue rate",
      "Pages not classified",
      "Cost per page",
    ]);
    expect(screen.getAllByText("Winner")).toHaveLength(1);
    expect(screen.getByRole("note")).toHaveTextContent(
      "These are stand-in figures, not results.",
    );
    expect(
      screen.getByText(
        "LLM: 1 file failed or did not finish. Every page of it counts as wrong.",
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        "No planted identifier was found in a classifier's reason (184 checked).",
      ),
    ).toBeVisible();
    // Each table stands alone: the retrieval one says it has not been run.
    expect(
      screen.getByText("The retrieval bake-off has not been run yet."),
    ).toBeVisible();
    expect(screen.getAllByRole("table")).toHaveLength(1);

    // A contender that was not run shows no number, whatever the file
    // holds for it, and nobody is marked when the file names no winner.
    server.held.classification = classificationScoreboard({
      run: { ...SCOREBOARD_RUN, stand_ins: false },
      contenders: [
        classifierScore("llm", true, {
          calibration: 0.85,
          confident_right_pages: 68,
        }),
        classifierScore("doc-intelligence", false, {
          cost_per_page: { amount: "4.00", unit: "USD", source: "a list" },
        }),
      ],
      winner: null,
      not_run: [{ contender: "doc-intelligence", case_key: "case-001" }],
      reasons_not_checked: [
        { contender: "llm", case_key: "case-004" },
        { contender: "llm", case_key: "case-005" },
      ],
      reason_leaks: [
        {
          contender: "llm",
          case_key: "case-002",
          page_number: 3,
          category: "person_name",
        },
      ],
    });
    await userEvent.click(screen.getByRole("button", { name: "Read again" }));

    await waitFor(() => expect(screen.queryByText("Winner")).toBeNull());
    expect((await lines("Classifiers"))[1]).toEqual([
      "Document Intelligence",
      "Not measured",
    ]);
    expect(screen.queryByText(/4\.00/)).toBeNull();
    expect(screen.queryByRole("note")).toBeNull();
    expect(
      screen.getByText(
        "No winner: no classifier has a calibration of at least 90%.",
      ),
    ).toBeVisible();
    // Each line names the classifier it is about, and nothing says "no
    // identifier was found" while a reason leaked or was not read.
    for (const line of [
      "Document Intelligence could not be run: the case of its first file (case-001) failed with no page classified, and no other file was uploaded for it.",
      "The check of the classifiers' reasons is not clean (184 checked).",
      "LLM: 1 planted identifier was found in its reasons.",
      "LLM: the reasons of 2 files were not read.",
    ]) {
      expect(screen.getByText(line)).toBeVisible();
    }
    expect(screen.queryByText(/No planted identifier/)).toBeNull();

    // Nothing was measured: said as that, not as a want of calibration.
    server.held.classification = classificationScoreboard({
      contenders: [
        classifierScore("llm", false),
        classifierScore("doc-intelligence", false),
      ],
      winner: null,
      reasons_not_checked: [{ contender: "llm", case_key: "case-004" }],
    });
    await userEvent.click(screen.getByRole("button", { name: "Read again" }));
    expect(
      await screen.findByText(
        "No classifier was measured, so there is no winner.",
      ),
    ).toBeVisible();
    expect(screen.queryByText(/calibration of at least 90%\./)).toBeNull();
  });

  it("says “not run yet” or shows an error for its own table alone, and the retrieval table stands", async () => {
    const server = scoreboardServer(retrievalScoreboard());

    openScoreboard();

    expect(await lines()).toHaveLength(6);
    expect(
      screen.getByText("The classifier bake-off has not been run yet."),
    ).toBeVisible();
    expect(screen.getAllByRole("table")).toHaveLength(1);
    expect(screen.queryByRole("alert")).toBeNull();

    // Answers that are no classifier scoreboard: a contender missing, a
    // measured one without its counts, a winner that is no contender, and
    // the server's own refusal of a file that does not fit.
    for (const wrong of [
      classificationScoreboard({
        contenders: [classifierScore("llm", true)],
      }),
      classificationScoreboard({
        contenders: [
          classifierScore("llm", true, { right_pages: null }),
          classifierScore("doc-intelligence", false),
        ],
      }),
      classificationScoreboard({ winner: "r3" }),
      classificationScoreboard({
        reason_leaks: [{ contender: "r3", case_key: "case-002" }],
      }),
      () =>
        json(
          500,
          errorBody("internal_error", "Something went wrong.", TRACE_ID),
        ),
    ]) {
      const reads = server.calls.length;
      server.held.classification = wrong;
      await userEvent.click(screen.getByRole("button", { name: "Read again" }));
      await waitFor(() => expect(server.calls.length).toBeGreaterThan(reads));
      expect(
        await screen.findByText(
          "The classifier scoreboard could not be shown.",
        ),
      ).toBeVisible();
      // The retrieval table stands, and no half of the other is drawn.
      expect(await lines()).toHaveLength(6);
      expect(screen.getAllByRole("table")).toHaveLength(1);
    }
    expect(screen.getByRole("alert")).toHaveTextContent(
      `Reference: ${TRACE_ID}`,
    );
  });
});
