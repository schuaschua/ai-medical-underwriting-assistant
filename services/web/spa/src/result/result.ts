// A case's result as the services answer it: its status, its pages, its
// facts and its verdict runs. The SPA reads them by polling (AD-19) and
// works nothing out of them: no verdict, no loading, no verification.
import { useMemo } from "react";
import {
  ApiError,
  getFacts,
  getPages,
  getProgress,
  getVerdictRuns,
} from "../api/client";
import type {
  CaseProgress,
  FactList,
  PageList,
  VerdictRunList,
} from "../api/contracts.gen";
import { isFinalStatus } from "../cases/caseProgress";
import { isRefusal } from "../polling/backoff";
import { usePolled, type Judgement, type Polled } from "../polling/polled";

/** How often the result of a case that still moves is read again. */
export const RESULT_POLL_MS = 3_000;

/**
 * One part of the result, read from the service that owns it. A part that
 * cannot be read is a fault of that part alone: the others are still shown.
 */
export type Part<T> =
  /** `staleError`: the newest read of the part failed; this is an earlier answer. */
  | { kind: "read"; value: T; staleError: unknown }
  /** No read of the part has succeeded yet, and the last one failed. */
  | { kind: "failed"; error: unknown };

export interface CaseResult {
  /** The case as `workflow` reports it: its status ends the reading. */
  progress: CaseProgress;
  pages: Part<PageList>;
  facts: Part<FactList>;
  runs: Part<VerdictRunList>;
  /** How many reads in a row left a part unread that another read might mend. */
  failedReads: number;
}

type Parts = Pick<CaseResult, "pages" | "facts" | "runs">;

function part<T>(
  answer: PromiseSettledResult<T>,
  held: Part<T> | undefined,
): Part<T> {
  if (answer.status === "fulfilled") {
    return { kind: "read", value: answer.value, staleError: null };
  }
  // What was read before stays on screen, with a note that it may be old.
  return held?.kind === "read"
    ? { ...held, staleError: answer.reason }
    : { kind: "failed", error: answer.reason };
}

/**
 * Whether another read of the part could show more: it failed, and neither
 * was it refused (a 4xx) nor was its answer of another shape than the
 * contract's (a success the client would not take). Asking again as it is
 * gets those two again.
 */
function mayMend(read: Part<unknown>): boolean {
  const error = read.kind === "read" ? read.staleError : read.error;
  return (
    error !== null &&
    !isRefusal(error) &&
    !(error instanceof ApiError && error.status < 400)
  );
}

function anyMayMend(parts: Parts): boolean {
  return mayMend(parts.pages) || mayMend(parts.facts) || mayMend(parts.runs);
}

/** A reader of one case's result that keeps the last good answer of each part. */
function resultReader(caseId: string): () => Promise<CaseResult> {
  let held: Partial<Parts> = {};
  let failedReads = 0;
  return async () => {
    // The case first, the parts after it: when the case is final at this
    // read, the parts read after it hold everything it will ever have.
    const progress = await getProgress(caseId);
    const [pages, facts, runs] = await Promise.allSettled([
      getPages(caseId),
      getFacts(caseId),
      getVerdictRuns(caseId),
    ]);
    const parts: Parts = {
      pages: part(pages, held.pages),
      facts: part(facts, held.facts),
      runs: part(runs, held.runs),
    };
    held = parts;
    failedReads = anyMayMend(parts) ? failedReads + 1 : 0;
    return { progress, ...parts, failedReads };
  };
}

/** How many failed reads in a row of a part are tried again by themselves once the case is final. */
export const FINAL_CASE_RETRIES = 3;

/**
 * What a read means for the reading (see `usePolled`). A part that failed
 * and may mend makes the next read wait longer. Nothing more is read by
 * itself once the server says the case is final and either every part is
 * in hand (or will not mend) with no listed run still running, or a part
 * has failed `FINAL_CASE_RETRIES` times in a row: "Check again" is left.
 */
export function judge(result: CaseResult): Judgement {
  const final = isFinalStatus(result.progress.case_status);
  if (anyMayMend(result)) {
    return final && result.failedReads >= FINAL_CASE_RETRIES
      ? "settled"
      : "failing";
  }
  const running =
    result.runs.kind === "read" &&
    result.runs.value.verdict_runs.some((run) => run.status === "running");
  return final && !running ? "settled" : "following";
}

/**
 * Follows one case's result: reads it now and then every `RESULT_POLL_MS`
 * while the page is visible, further apart while a read or a part of it
 * fails, and again on request. Reading stops as `judge` says, or once the
 * server refuses the read of the case itself.
 */
export function useResult(caseId: string): {
  state: Polled<CaseResult>;
  /** Read the result again now: the user asked. */
  refresh: () => void;
  /** Read it again at the usual pace, keeping the wait after failed reads (see `usePolled`). */
  follow: () => void;
} {
  const read = useMemo(() => resultReader(caseId), [caseId]);
  return usePolled(read, RESULT_POLL_MS, judge);
}
