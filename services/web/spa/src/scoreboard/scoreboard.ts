// The scoreboards as the server answers them (AD-17): the files the bake-off
// runner wrote. The SPA works no figure out and picks no winner.
import {
  ApiError,
  getClassificationScoreboard,
  getRedactionScoreboard,
  getRetrievalScoreboard,
} from "../api/client";
import type {
  ClassificationScoreboard,
  RedactionScoreboard,
  RetrievalScoreboard,
} from "../api/contracts.gen";
import { usePolled } from "../polling/polled";

/** The scoreboard screen, on the underwriter's side only (AD-9). */
export const SCOREBOARD_PATH = "/underwriter/scoreboard";

/** The redaction report of the run: read, not written yet, or not readable. */
export type RedactionRead =
  | { kind: "read"; report: RedactionScoreboard }
  | { kind: "missing" }
  /** A report of another bake-off run than the table's: it is not this run's. */
  | { kind: "otherRun"; evalRunId: string }
  | { kind: "unreadable"; error: unknown };

/** What one read of the retrieval scoreboard gave. */
export type RetrievalRead =
  /** The server holds no scoreboard: the bake-off has not been run. */
  | { kind: "notRun" }
  | { kind: "read"; board: RetrievalScoreboard; redaction: RedactionRead }
  /** The scoreboard could not be read, or was not one. Nothing of it is shown. */
  | { kind: "unreadable"; error: unknown };

/** What one read of the classifier scoreboard gave (story 4.3). */
export type ClassificationRead =
  | { kind: "notRun" }
  | { kind: "read"; board: ClassificationScoreboard }
  | { kind: "unreadable"; error: unknown };

/** One read of the screen: each table stands alone, whatever became of the other. */
export type ScoreboardRead = {
  kind: "read";
  retrieval: RetrievalRead;
  classification: ClassificationRead;
};

export type ScoreboardState = { kind: "reading" } | ScoreboardRead;

function isMissing(error: unknown): boolean {
  return error instanceof ApiError && error.code === "not_found";
}

async function readRedaction(evalRunId: string): Promise<RedactionRead> {
  try {
    const report = await getRedactionScoreboard();
    // A file left from another run is not shown as this run's check.
    return report.run.eval_run_id === evalRunId
      ? { kind: "read", report }
      : { kind: "otherRun", evalRunId: report.run.eval_run_id };
  } catch (error) {
    return isMissing(error)
      ? { kind: "missing" }
      : { kind: "unreadable", error };
  }
}

/** One read of the retrieval files. It never fails: a failure is an answer of its own. */
async function readRetrieval(): Promise<RetrievalRead> {
  let board: RetrievalScoreboard;
  try {
    board = await getRetrievalScoreboard();
  } catch (error) {
    return isMissing(error)
      ? { kind: "notRun" }
      : { kind: "unreadable", error };
  }
  // The table does not wait on the report, and stands without it.
  return {
    kind: "read",
    board,
    redaction: await readRedaction(board.run.eval_run_id),
  };
}

/** One read of the classifier file. It never fails either. */
async function readClassification(): Promise<ClassificationRead> {
  try {
    return { kind: "read", board: await getClassificationScoreboard() };
  } catch (error) {
    return isMissing(error)
      ? { kind: "notRun" }
      : { kind: "unreadable", error };
  }
}

/** One read of the screen: the two tables side by side, neither waiting on the other. */
async function readScoreboard(): Promise<ScoreboardRead> {
  const [retrieval, classification] = await Promise.all([
    readRetrieval(),
    readClassification(),
  ]);
  return { kind: "read", retrieval, classification };
}

// A file does not change while the screen is open: it is read once, and
// again only when the user asks. The pace is never used.
const NEVER_MS = 2 ** 31 - 1;
const settled = () => "settled" as const;

/** Reads the scoreboards once, and again on request. */
export function useScoreboard(): {
  state: ScoreboardState;
  /** Read the scoreboard again now: the user asked. */
  refresh: () => void;
} {
  const { state, refresh } = usePolled(readScoreboard, NEVER_MS, settled);
  return {
    // `readScoreboard` never fails, so the hook is reading or has its answer.
    state: state.kind === "read" ? state.value : { kind: "reading" },
    refresh,
  };
}
