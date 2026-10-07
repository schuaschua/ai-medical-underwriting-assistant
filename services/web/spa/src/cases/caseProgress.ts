// What the server says about each case of this session. The SPA reads
// progress by polling (AD-19); it never works a status out for itself.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getProgress, startCase } from "../api/client";
import type { CaseStatus, PageProgress } from "../api/contracts.gen";

/** How often the progress of each case is read again. */
export const PROGRESS_POLL_MS = 3_000;
/** The longest wait between two reads of a case whose reads keep failing. */
export const PROGRESS_MAX_BACKOFF_MS = 30_000;

export type CaseState =
  /** Nothing is known yet: the first read is under way. */
  | { kind: "checking" }
  /** The server has been asked to start the case. */
  | { kind: "starting" }
  /**
   * `redactionFailed`: the server said the redaction stage failed. `pages`:
   * the case's pages as the server last listed them, each with its status.
   */
  | {
      kind: "started";
      status: CaseStatus;
      redactionFailed?: boolean;
      pages: readonly PageProgress[];
    }
  /** The document was received, but its case is not started. */
  | { kind: "not_started"; error: unknown }
  /** No read has succeeded yet, and the last one failed. */
  | { kind: "unreadable"; error: unknown };

export type CaseStates = Readonly<Record<string, CaseState>>;

const CHECKING: CaseState = { kind: "checking" };

// A case in one of these statuses will not change again, so it is not read
// again. A case that is running, or waiting for a person, is.
const FINISHED: readonly CaseStatus[] = ["completed", "failed"];

function isFinished(state: CaseState): boolean {
  return state.kind === "started" && FINISHED.includes(state.status);
}

export function stateOf(states: CaseStates, caseId: string): CaseState {
  return Object.hasOwn(states, caseId) ? states[caseId]! : CHECKING;
}

/** What is kept about the reads of one case, beside what is shown. */
interface Reads {
  /** A read is out: no second one is sent until it is back. */
  inFlight: boolean;
  /** Reads sent, and the number of the newest one whose answer was used. */
  sent: number;
  applied: number;
  /** Failures in a row, and the time before which no read is sent. */
  failures: number;
  notBefore: number;
  /** Counts the starts: an answer to a read sent before one is out of date. */
  starts: number;
}

/** The wait after `failures` failed reads in a row: doubled each time, to a limit. */
export function backoffMs(failures: number): number {
  return Math.min(PROGRESS_POLL_MS * 2 ** failures, PROGRESS_MAX_BACKOFF_MS);
}

function isHidden(): boolean {
  return document.visibilityState === "hidden";
}

/**
 * Follows the given cases: reads each one's progress now and then every
 * `PROGRESS_POLL_MS` while the page is visible, and starts a case, or reads
 * one again, on request.
 */
export function useCaseProgress(caseIds: readonly string[]): {
  states: CaseStates;
  start: (caseId: string) => void;
  check: (caseId: string) => void;
} {
  const [states, setStates] = useState<CaseStates>({});
  // The same states, readable from a timer without waiting for a render.
  const current = useRef<CaseStates>({});
  const reads = useRef<Record<string, Reads>>({});
  const mounted = useRef(true);

  const readsOf = useCallback((caseId: string): Reads => {
    reads.current[caseId] ??= {
      inFlight: false,
      sent: 0,
      applied: 0,
      failures: 0,
      notBefore: 0,
      starts: 0,
    };
    return reads.current[caseId];
  }, []);

  const set = useCallback((caseId: string, state: CaseState) => {
    if (!mounted.current) {
      return;
    }
    current.current = { ...current.current, [caseId]: state };
    setStates(current.current);
  }, []);

  const start = useCallback(
    (caseId: string) => {
      if (stateOf(current.current, caseId).kind === "starting") {
        return;
      }
      const mine = readsOf(caseId);
      mine.starts += 1;
      set(caseId, { kind: "starting" });
      startCase(caseId).then(
        (started) => {
          mine.failures = 0;
          mine.notBefore = 0;
          // The answer to a start lists no pages: the next read brings them.
          set(caseId, {
            kind: "started",
            status: started.case_status,
            pages: [],
          });
        },
        (error: unknown) => set(caseId, { kind: "not_started", error }),
      );
    },
    [readsOf, set],
  );

  const read = useCallback(
    (caseId: string, asked = false) => {
      const mine = readsOf(caseId);
      const before = stateOf(current.current, caseId);
      if (
        // One read at a time for a case: answers cannot overtake each other.
        mine.inFlight ||
        before.kind === "starting" ||
        isFinished(before) ||
        // A case that is not started changes only when it is started.
        before.kind === "not_started" ||
        (!asked && Date.now() < mine.notBefore)
      ) {
        return;
      }
      mine.inFlight = true;
      mine.sent += 1;
      const number = mine.sent;
      const startsBefore = mine.starts;
      // An answer is used only if no newer one was, and no start came between.
      const upToDate = () =>
        number > mine.applied && mine.starts === startsBefore;
      getProgress(caseId)
        .then(
          (progress) => {
            mine.failures = 0;
            mine.notBefore = 0;
            if (upToDate()) {
              mine.applied = number;
              set(caseId, {
                kind: "started",
                status: progress.case_status,
                redactionFailed: progress.redaction_status === "failed",
                pages: progress.pages,
              });
            }
          },
          (error: unknown) => {
            if (error instanceof ApiError && error.code === "not_found") {
              // The server does not know this case: it was never started.
              if (upToDate()) {
                mine.applied = number;
                set(caseId, { kind: "not_started", error: null });
              }
              return;
            }
            // Any other failure says nothing about the case. Reads go on,
            // further apart each time; a status already shown stays.
            mine.failures += 1;
            mine.notBefore = Date.now() + backoffMs(mine.failures);
            const shown = stateOf(current.current, caseId).kind;
            if (
              upToDate() &&
              (shown === "checking" || shown === "unreadable")
            ) {
              set(caseId, { kind: "unreadable", error });
            }
          },
        )
        .finally(() => {
          mine.inFlight = false;
        });
    },
    [readsOf, set],
  );

  /** Read a case again now, whatever its back-off: the user asked. */
  const check = useCallback(
    (caseId: string) => {
      if (stateOf(current.current, caseId).kind === "unreadable") {
        set(caseId, CHECKING);
      }
      read(caseId, true);
    },
    [read, set],
  );

  // A string, so the effect runs again only when the set of cases changes.
  const followed = caseIds.join(",");
  useEffect(() => {
    if (followed === "") {
      return;
    }
    const readAll = () => {
      // Nobody is looking at a hidden tab: nothing is read for it.
      if (!isHidden()) {
        followed.split(",").forEach((caseId) => read(caseId));
      }
    };
    readAll();
    const timer = setInterval(readAll, PROGRESS_POLL_MS);
    // Back in view: catch up at once.
    document.addEventListener("visibilitychange", readAll);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", readAll);
    };
  }, [followed, read]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  return { states, start, check };
}
