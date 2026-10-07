// The underwriter's triage queue as the server lists it. The SPA reads it by
// polling (AD-19) and never works out for itself which pages belong in it.
import { useCallback, useEffect, useRef, useState } from "react";
import { getTriageQueue } from "../api/client";
import type { TriageQueue } from "../api/contracts.gen";
import { backoffMs } from "../cases/caseProgress";

/** How often the queue is read again. */
export const TRIAGE_POLL_MS = 3_000;

export type QueueState =
  /** The first read is under way. */
  | { kind: "reading" }
  /**
   * `staleError`: the newest read failed, so what is shown may be out of
   * date. `number`: counts the reads that were answered, this one included.
   */
  | { kind: "read"; queue: TriageQueue; staleError: unknown; number: number }
  /** No read has succeeded yet, and the last one failed. */
  | { kind: "unreadable"; error: unknown };

function isHidden(): boolean {
  return document.visibilityState === "hidden";
}

/**
 * Follows the queue: reads it now and then every `TRIAGE_POLL_MS` while the
 * page is visible, further apart while reads fail, and again on request.
 */
export function useTriageQueue(): {
  state: QueueState;
  /** Read the queue again now: a decision was made, or the user asked. */
  refresh: () => void;
} {
  const [state, setState] = useState<QueueState>({ kind: "reading" });
  const mounted = useRef(true);
  // One read at a time, so answers cannot overtake each other.
  const inFlight = useRef(false);
  // A read was asked for while one was out: another follows it at once.
  const again = useRef(false);
  const failures = useRef(0);
  const notBefore = useRef(0);
  const readAgain = useRef<() => void>(() => undefined);

  const read = useCallback((asked: boolean) => {
    if (inFlight.current) {
      // The read that is out may have been answered before what the user
      // just did: its answer is followed by a new read.
      again.current = again.current || asked;
      return;
    }
    if (!asked && Date.now() < notBefore.current) {
      return;
    }
    inFlight.current = true;
    getTriageQueue()
      .then(
        (queue) => {
          failures.current = 0;
          notBefore.current = 0;
          if (mounted.current) {
            setState((shown) => ({
              kind: "read",
              queue,
              staleError: null,
              number: shown.kind === "read" ? shown.number + 1 : 1,
            }));
          }
        },
        (error: unknown) => {
          failures.current += 1;
          notBefore.current = Date.now() + backoffMs(failures.current);
          if (mounted.current) {
            // A queue already shown stays, with a note that it may be old.
            setState((shown) =>
              shown.kind === "read"
                ? { ...shown, staleError: error }
                : { kind: "unreadable", error },
            );
          }
        },
      )
      .finally(() => {
        inFlight.current = false;
        if (again.current) {
          again.current = false;
          readAgain.current();
        }
      });
  }, []);

  useEffect(() => {
    readAgain.current = () => read(true);
  }, [read]);

  useEffect(() => {
    mounted.current = true;
    const poll = () => {
      // Nobody is looking at a hidden tab: nothing is read for it.
      if (!isHidden()) {
        read(false);
      }
    };
    poll();
    const timer = setInterval(poll, TRIAGE_POLL_MS);
    // Back in view: catch up at once.
    document.addEventListener("visibilitychange", poll);
    return () => {
      mounted.current = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [read]);

  const refresh = useCallback(() => read(true), [read]);
  return { state, refresh };
}
