// A case's audit trail as the server answers it. The SPA reads it by polling
// (AD-19) and works nothing out: not the order, not an actor, not a status.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getAuditTrail, getProgress } from "../api/client";
import type { AuditTrail, CaseProgress } from "../api/contracts.gen";
import { isFinalStatus } from "../cases/caseProgress";
import { backoffMs, isRefusal } from "../polling/backoff";

/** How often the trail of a case that still moves is read again. */
export const AUDIT_POLL_MS = 3_000;

export type TrailState =
  /** The first read is under way. */
  | { kind: "reading" }
  /**
   * `progress`: the case as the server reports it, read just before the
   * trail: its status, and the number of each page. `staleError`: the newest
   * read failed, so what is shown may be out of date.
   */
  | {
      kind: "read";
      trail: AuditTrail;
      progress: CaseProgress;
      staleError: unknown;
    }
  /** The server knows no such case. */
  | { kind: "unknown" }
  /** No read has succeeded yet, and the last one failed. */
  | { kind: "unreadable"; error: unknown };

function isHidden(): boolean {
  return document.visibilityState === "hidden";
}

function isUnknownCase(error: unknown): boolean {
  return error instanceof ApiError && error.code === "not_found";
}

/**
 * Follows one case's trail: reads it now and then every `AUDIT_POLL_MS`
 * while the page is visible, further apart while reads fail, and again on
 * request. Reading stops once the server says the case is final, that there
 * is no such case, that the case has more events than it lists, or refuses
 * the read.
 */
export function useAuditTrail(caseId: string): {
  state: TrailState;
  /** Read the trail again now: the user asked. */
  refresh: () => void;
} {
  const [state, setState] = useState<TrailState>({ kind: "reading" });
  const mounted = useRef(true);
  // One read at a time, so answers cannot overtake each other.
  const inFlight = useRef(false);
  // A read was asked for while one was out: another follows it at once.
  const again = useRef(false);
  const failures = useRef(0);
  const notBefore = useRef(0);
  // Nothing more will come: only a request of the user reads again.
  const settled = useRef(false);
  const readAgain = useRef<() => void>(() => undefined);

  const read = useCallback(
    (asked: boolean) => {
      if (inFlight.current) {
        again.current = again.current || asked;
        return;
      }
      if (!asked && (settled.current || Date.now() < notBefore.current)) {
        return;
      }
      inFlight.current = true;
      // The case first, the trail after it: when the case is final at the
      // first read, the trail read after it holds its last event.
      getProgress(caseId)
        .then(async (progress) => ({
          progress,
          trail: await getAuditTrail(caseId),
        }))
        .then(
          ({ progress, trail }) => {
            failures.current = 0;
            notBefore.current = 0;
            // A final case gets no more events; a trail over the limit
            // would list the same first ones again.
            settled.current =
              isFinalStatus(progress.case_status) || trail.has_more;
            if (mounted.current) {
              setState({ kind: "read", trail, progress, staleError: null });
            }
          },
          (error: unknown) => {
            if (isUnknownCase(error)) {
              settled.current = true;
              if (mounted.current) {
                setState({ kind: "unknown" });
              }
              return;
            }
            if (isRefusal(error)) {
              // No repeat mends it: only a request of the user reads again.
              settled.current = true;
            }
            failures.current += 1;
            notBefore.current = Date.now() + backoffMs(failures.current);
            if (mounted.current) {
              // A trail already shown stays, with a note that it may be old.
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
          // Nobody is left to show a queued read to once the screen is gone.
          if (again.current && mounted.current) {
            readAgain.current();
          }
          again.current = false;
        });
    },
    [caseId],
  );

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
    const timer = setInterval(poll, AUDIT_POLL_MS);
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
