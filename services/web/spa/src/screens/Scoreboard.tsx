import type { ReactNode } from "react";
import type {
  RedactionScoreboard,
  RetrievalRowScore,
  RetrievalScoreboard,
  StatedFigure,
} from "../api/contracts.gen";
import { ErrorMessage } from "../components/ErrorMessage";
import { LocalTime } from "../components/LocalTime";
import { type RedactionRead, useScoreboard } from "../scoreboard/scoreboard";
import { exactPercentage, strings } from "../strings";

const text = strings.scoreboard;

/** The columns that hold what was measured or stated of a row. */
const FIGURE_COLUMNS = [
  text.recallColumn,
  text.accuracyColumn,
  text.failedRunsColumn,
  text.latencyColumn,
  text.costColumn,
  text.effortColumn,
];

/** A share as the file holds it, with the two counts behind it; `none` when nothing was counted. */
function share(
  figure: number | null,
  part: number | null,
  whole: number | null,
  none: string,
): string {
  return figure === null || part === null || whole === null
    ? none
    : text.shareOf(exactPercentage(figure), part, whole);
}

/** A figure somebody stated: its amount and unit, and its source on request. */
function Stated({ figure }: { figure: StatedFigure | null }) {
  if (figure === null) {
    return <>{text.notStated}</>;
  }
  return (
    <>
      {text.stated(figure.amount, figure.unit)}
      <details>
        <summary>{text.source}</summary>
        {figure.source}
      </details>
    </>
  );
}

function Figures({ row }: { row: RetrievalRowScore }) {
  if (!row.measured) {
    // No number at all, whatever the file holds for the row.
    return <td colSpan={FIGURE_COLUMNS.length}>{text.notMeasured}</td>;
  }
  return (
    <>
      <td>
        {share(
          row.rule_recall,
          row.recall_hits,
          row.recall_searches,
          text.noSearches,
        )}
      </td>
      <td>
        {share(row.verdict_accuracy, row.right_runs, row.cases, text.noCases)}
      </td>
      <td>
        {row.failed_runs !== null &&
          row.cases !== null &&
          text.failedRuns(row.failed_runs, row.cases)}
      </td>
      <td>
        {row.latency_ms_median === null ||
        row.latency_ms_p95 === null ||
        row.latency_searches === null
          ? text.noLatency
          : text.latency(
              row.latency_ms_median,
              row.latency_ms_p95,
              row.latency_searches,
            )}
      </td>
      <td>
        <Stated figure={row.cost} />
      </td>
      <td>
        <Stated figure={row.effort} />
      </td>
    </>
  );
}

function Redaction({ read }: { read: RedactionRead }) {
  let body: ReactNode;
  if (read.kind === "missing") {
    body = <p>{text.redactionMissing}</p>;
  } else if (read.kind === "otherRun") {
    body = <p>{text.redactionOtherRun(read.evalRunId)}</p>;
  } else if (read.kind === "unreadable") {
    body = (
      <>
        <p>{text.redactionUnreadable}</p>
        <ErrorMessage error={read.error} />
      </>
    );
  } else {
    body = <p>{redactionLine(read.report)}</p>;
  }
  return (
    <section>
      <h3>{text.redactionHeading}</h3>
      {body}
    </section>
  );
}

/** The report in one line: clean or not, what was checked, leaks and unchecked cases by count. */
function redactionLine(report: RedactionScoreboard): string {
  const parts = [
    report.clean ? text.redactionClean : text.redactionNotClean,
    text.redactionChecked(report.pages_checked, report.cases_checked),
  ];
  if (report.leaks.length > 0) {
    parts.push(text.redactionLeaks(report.leaks.length));
  }
  if (report.cases_not_checked.length > 0) {
    parts.push(text.redactionNotChecked(report.cases_not_checked.length));
  }
  return parts.join(" ");
}

function Board({ board }: { board: RetrievalScoreboard }) {
  return (
    <>
      <p>{text.intro(board.top_k)}</p>
      <p>
        {text.runBefore}
        <LocalTime at={board.run.finished_at} />
        {text.runAgainst(board.run.web_address)}{" "}
        {text.runId(board.run.eval_run_id)}
      </p>
      {board.run.stand_ins && (
        <p role="note">
          <strong>{text.standIns}</strong>
        </p>
      )}
      <table aria-label={text.tableLabel}>
        <thead>
          <tr>
            <th scope="col">{text.rowColumn}</th>
            <th scope="col">{text.storeColumn}</th>
            <th scope="col">{text.chunkSetColumn}</th>
            <th scope="col">{text.methodColumn}</th>
            {FIGURE_COLUMNS.map((column) => (
              <th key={column} scope="col">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {board.rows.map((row) => (
            <tr key={row.retriever_config}>
              <th scope="row">
                <code>{row.retriever_config}</code>
                {/* The file names the winner; it is marked in words. */}
                {board.winner === row.retriever_config && (
                  <>
                    {" "}
                    <strong>{text.winner}</strong>
                  </>
                )}
              </th>
              <td>{row.store}</td>
              <td>{row.chunk_set}</td>
              <td>{row.method}</td>
              <Figures row={row} />
            </tr>
          ))}
        </tbody>
      </table>
      {board.rows.some((row) => row.measured && (row.failed_runs ?? 0) > 0) && (
        <p>{text.failedRunsMeaning}</p>
      )}
      {board.unscored_cases.length > 0 && (
        <p>{text.unscoredCases(board.unscored_cases.length)}</p>
      )}
      {board.failed_searches.length > 0 && (
        <p>{text.failedSearches(board.failed_searches.length)}</p>
      )}
    </>
  );
}

/**
 * The retrieval scoreboard: one line per row of the ladder with what the row
 * is and what the bake-off measured, the winner the file names, and the
 * redaction check of the same run. Nothing here scores, orders or chooses
 * (AD-17): every figure and count is the file's.
 */
export function Scoreboard() {
  const { state, refresh } = useScoreboard();
  const readAgain = (
    <p>
      <button type="button" onClick={refresh}>
        {text.readAgain}
      </button>
    </p>
  );

  return (
    <section>
      <h2>{text.heading}</h2>
      {state.kind === "reading" && <p role="status">{text.reading}</p>}
      {state.kind === "notRun" && (
        <>
          <p>{text.notRun}</p>
          {readAgain}
        </>
      )}
      {state.kind === "unreadable" && (
        <>
          <p>{text.unreadable}</p>
          <ErrorMessage error={state.error} />
          {readAgain}
        </>
      )}
      {state.kind === "read" && (
        <>
          <Board board={state.board} />
          <Redaction read={state.redaction} />
          {readAgain}
        </>
      )}
    </section>
  );
}
