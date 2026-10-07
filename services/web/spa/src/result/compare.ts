// Compare (story 3.6, AD-11): two verdict runs of one finished case, each
// made with another retrieval row, side by side. The server says which rows
// to show and whether a row can be run here. What is marked as different is
// a comparison of ids and values the services answered: nothing here knows
// a rule, and nothing says which run is right.
import { ApiError, getComparePairs, getRunSteps } from "../api/client";
import type {
  RetrieverConfig,
  RetrieverPair,
  VerdictRun,
  VerdictRunRequested,
} from "../api/contracts.gen";

/**
 * How many times the result is read again for a run that was asked for and
 * is not listed yet, one read every `RESULT_POLL_MS`: about five minutes.
 */
export const AWAITED_RUN_READS = 100;

/**
 * Asks for a run with a row. The caller sends one request for a row and
 * answers every later ask with that same request: asking `workflow` again
 * schedules a run that ended without a stored result once more.
 */
export type AskForRun = (row: RetrieverConfig) => Promise<VerdictRunRequested>;

export type Rows = readonly [RetrieverConfig, RetrieverConfig];

export type Comparison =
  /** The pair in force, and what was answered for each row that was asked for. */
  | { kind: "ready"; rows: Rows; asked: readonly VerdictRunRequested[] }
  /** A row of every pair cannot be run here: the pairs that were tried. */
  | { kind: "unavailable"; pairs: readonly Rows[] };

function rowsOf(pair: RetrieverPair): Rows {
  return [pair.first, pair.second];
}

/**
 * Ask for a run with each of those rows, one after the other. Null when the
 * server says a row cannot be run here: that is no fault, the pair is just
 * not this build's. The rows after the refused one are not asked for.
 */
async function askFor(
  ask: AskForRun,
  rows: readonly RetrieverConfig[],
): Promise<VerdictRunRequested[] | null> {
  const asked: VerdictRunRequested[] = [];
  for (const row of rows) {
    try {
      asked.push(await ask(row));
    } catch (error) {
      if (
        error instanceof ApiError &&
        error.code === "retriever_not_available"
      ) {
        return null;
      }
      throw error;
    }
  }
  return asked;
}

/**
 * Find the pair of rows to compare on a case, and ask for the runs it lacks:
 * the server's default pair, or its fallback pair when a row of the default
 * one cannot be run here. Only a row the case has no run for is asked for.
 * Which rows can be run is found by asking: only the services know.
 */
export async function startComparison(
  ask: AskForRun,
  hasRun: (row: RetrieverConfig) => boolean,
): Promise<Comparison> {
  const settings = await getComparePairs();
  const tried: Rows[] = [];
  for (const pair of [settings.default_pair, settings.fallback_pair]) {
    const rows = rowsOf(pair);
    if (tried.some((one) => one[0] === rows[0] && one[1] === rows[1])) {
      // The fallback is the default pair again: asked once.
      continue;
    }
    tried.push(rows);
    const asked = await askFor(
      ask,
      rows.filter((row) => !hasRun(row)),
    );
    if (asked !== null) {
      return { kind: "ready", rows, asked };
    }
  }
  return { kind: "unavailable", pairs: tried };
}

/**
 * The rules a run retrieved: the rule ids of its agent steps (AD-15), each
 * once, in the order first seen. Every step of the run is read, an answer
 * at a time, as the server lists them.
 */
export async function readRetrievedRules(runId: string): Promise<string[]> {
  // A set keeps the order in which its members were added.
  const seen = new Set<string>();
  let after: number | null = null;
  for (;;) {
    const listed = await getRunSteps(
      runId,
      { tool: null, ruleId: null },
      after,
    );
    for (const step of listed.steps) {
      for (const ruleId of step.rule_ids) {
        seen.add(ruleId);
      }
    }
    const last = listed.steps[listed.steps.length - 1];
    if (!listed.has_more || last === undefined) {
      return [...seen];
    }
    after = last.step_no;
  }
}

/** Whether two finished runs suggest another verdict, or another loading of it. */
export function verdictDiffers(one: VerdictRun, other: VerdictRun): boolean {
  return one.verdict !== other.verdict || one.loading_pct !== other.loading_pct;
}

/** The ids among `mine` that are not among `theirs`. */
export function onlyIn(
  mine: readonly string[],
  theirs: readonly string[],
): ReadonlySet<string> {
  const other = new Set(theirs);
  return new Set(mine.filter((id) => !other.has(id)));
}
