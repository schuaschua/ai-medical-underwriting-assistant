import type { ReactNode } from "react";
import type {
  ClassificationScoreboard,
  ClassifierContender,
  ClassifierScore,
  RedactionScoreboard,
  RetrievalRowScore,
  RetrievalScoreboard,
  StatedFigure,
} from "../api/contracts.gen";
import { ErrorMessage } from "../components/ErrorMessage";
import { LocalTime } from "../components/LocalTime";
import {
  type ClassificationRead,
  type RedactionRead,
  type RetrievalRead,
  useScoreboard,
} from "../scoreboard/scoreboard";
import { exactPercentage, strings } from "../strings";

const text = strings.scoreboard;
const classifiers = strings.classifierScoreboard;

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

/** The report in one line: clean or not, what was checked, leaks and unchecked cases by count, and the quotes redaction took away. */
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
  parts.push(
    text.redactionQuotes(report.quotes_not_found, report.quotes_checked),
  );
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

/** The columns that hold what was measured or stated of a contender. */
const CLASSIFIER_COLUMNS = [
  classifiers.accuracyColumn,
  classifiers.calibrationColumn,
  classifiers.queueRateColumn,
  classifiers.notClassifiedColumn,
  classifiers.costColumn,
];

function ClassifierFigures({ score }: { score: ClassifierScore }) {
  if (
    !score.measured ||
    score.pages === null ||
    score.pages_not_classified === null
  ) {
    // No number at all, whatever the file holds for the contender.
    return <td colSpan={CLASSIFIER_COLUMNS.length}>{text.notMeasured}</td>;
  }
  return (
    <>
      <td>
        {share(
          score.accuracy,
          score.right_pages,
          score.pages,
          classifiers.noPages,
        )}
      </td>
      <td>
        {share(
          score.calibration,
          score.confident_right_pages,
          score.confident_pages,
          classifiers.noConfidentPages,
        )}
      </td>
      <td>
        {share(
          score.queue_rate,
          score.queued_pages,
          score.pages,
          classifiers.noPages,
        )}
      </td>
      <td>
        {classifiers.notClassified(score.pages_not_classified, score.pages)}
      </td>
      <td>
        <Stated figure={score.cost_per_page} />
      </td>
    </>
  );
}

function ClassifierBoard({ board }: { board: ClassificationScoreboard }) {
  return (
    <>
      <p>{classifiers.intro}</p>
      <p>
        {text.runBefore}
        <LocalTime at={board.run.finished_at} />
        {text.runAgainst(board.run.web_address)}{" "}
        {text.runId(board.run.eval_run_id)}
      </p>
      {board.run.stand_ins && (
        <p role="note">
          <strong>{classifiers.standIns}</strong>
        </p>
      )}
      <table aria-label={classifiers.tableLabel}>
        <thead>
          <tr>
            <th scope="col">{classifiers.contenderColumn}</th>
            {CLASSIFIER_COLUMNS.map((column) => (
              <th key={column} scope="col">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {board.contenders.map((score) => (
            <tr key={score.contender}>
              <th scope="row">
                {classifiers.contenders[score.contender]}
                {/* The file names the winner; it is marked in words. */}
                {board.winner === score.contender && (
                  <>
                    {" "}
                    <strong>{text.winner}</strong>
                  </>
                )}
              </th>
              <ClassifierFigures score={score} />
            </tr>
          ))}
        </tbody>
      </table>
      {board.winner === null && (
        <p>
          {board.contenders.some((score) => score.measured)
            ? classifiers.noWinner
            : classifiers.noneMeasured}
        </p>
      )}
      {board.not_run.map((tried) => (
        <p key={tried.contender}>
          {classifiers.couldNotRun(
            classifiers.contenders[tried.contender],
            tried.case_key,
          )}
        </p>
      ))}
      <ByClassifier
        board={board}
        about={board.unscored_cases}
        say={classifiers.unscoredFiles}
      />
      <p>
        {board.reason_leaks.length > 0 || board.reasons_not_checked.length > 0
          ? classifiers.reasonsNotClean(board.reasons_checked)
          : classifiers.reasonsClean(board.reasons_checked)}
      </p>
      <ByClassifier
        board={board}
        about={board.reason_leaks}
        say={classifiers.reasonLeaks}
      />
      <ByClassifier
        board={board}
        about={board.reasons_not_checked}
        say={classifiers.reasonsNotRead}
      />
    </>
  );
}

/** One line per classifier the file lists something of, with how many: the file's entries, counted, never weighed. */
function ByClassifier({
  board,
  about,
  say,
}: {
  board: ClassificationScoreboard;
  about: { contender: ClassifierContender }[];
  say: (classifier: string, count: number) => string;
}) {
  return (
    <>
      {board.contenders.map(({ contender }) => {
        const count = about.filter(
          (entry) => entry.contender === contender,
        ).length;
        return (
          count > 0 && (
            <p key={contender}>
              {say(classifiers.contenders[contender], count)}
            </p>
          )
        );
      })}
    </>
  );
}

function Retrieval({ read }: { read: RetrievalRead }) {
  return (
    <section>
      <h2>{text.heading}</h2>
      {read.kind === "notRun" && <p>{text.notRun}</p>}
      {read.kind === "unreadable" && (
        <>
          <p>{text.unreadable}</p>
          <ErrorMessage error={read.error} />
        </>
      )}
      {read.kind === "read" && (
        <>
          <Board board={read.board} />
          <Redaction read={read.redaction} />
        </>
      )}
    </section>
  );
}

function Classifiers({ read }: { read: ClassificationRead }) {
  return (
    <section>
      <h2>{classifiers.heading}</h2>
      {read.kind === "notRun" && <p>{classifiers.notRun}</p>}
      {read.kind === "unreadable" && (
        <>
          <p>{classifiers.unreadable}</p>
          <ErrorMessage error={read.error} />
        </>
      )}
      {read.kind === "read" && <ClassifierBoard board={read.board} />}
    </section>
  );
}

/**
 * The two scoreboards. The retrieval one: a line per row of the ladder with
 * what the row is and what the bake-off measured, the winner the file names,
 * and the redaction check of the same run. Under it the classifier one
 * (story 4.3): a line per contender. Each stands alone: a file that is
 * missing or does not fit hides its own table only. Nothing here scores,
 * orders or chooses (AD-17): every figure and count is a file's.
 */
export function Scoreboard() {
  const { state, refresh } = useScoreboard();
  if (state.kind === "reading") {
    return (
      <section>
        <h2>{text.heading}</h2>
        <p role="status">{text.reading}</p>
      </section>
    );
  }
  return (
    <>
      <Retrieval read={state.retrieval} />
      <Classifiers read={state.classification} />
      <p>
        <button type="button" onClick={refresh}>
          {text.readAgain}
        </button>
      </p>
    </>
  );
}
