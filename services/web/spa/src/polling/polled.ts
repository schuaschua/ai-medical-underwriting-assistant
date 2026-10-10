// A value the server holds and the SPA follows by reading it again and again
// (AD-19): no WebSockets, no server-sent events. One hook does the reading
// for every screen that shows such a value, so they all behave alike.
import { useCallback, useEffect, useRef, useState } from "react";
import { backoffMs, isRefusal } from "./backoff";

export type Polled<T> =
  /** The first read is under way. */
  | { kind: "reading" }
  /**
   * `staleError`: the newest read failed, so what is shown may be out of
   * date. `number`: counts the reads that were answered, this one included.
   */
  | { kind: "read"; value: T; staleError: unknown; number: number }
  /**
   * Nothing can be shown: no read has succeeded yet and the last one failed,
   * or the server refused the read itself, whatever was shown before.
   */
  | { kind: "unreadable"; error: unknown };

/**
 * What an answer means for the reading: more may come, at the usual pace
 * (`following`); part of it failed, so the next read waits longer
 * (`failing`); or nothing more will come (`settled`).
 */
export type Judgement = "following" | "failing" | "settled";

function isHidden(): boolean {
  return document.visibilityState === "hidden";
}

/**
 * Follows a value: reads it now and then every `everyMs` while the page is
 * visible, further apart while reads fail, and again on request. Reading
 * stops once the server refuses the read itself (a 4xx other than 429): no
 * repeat mends that, so its message is shown and only a request reads
 * again. `judge` may say of an answer that nothing more will come, which
 * stops the reading too, or that part of it failed, which counts as a failed
 * read for the wait before the next one. `read` and `judge` must be the same
 * functions on every render.
 */
export function usePolled<T>(
  read: () => Promise<T>,
  everyMs: number,
  judge?: (value: T) => Judgement,
): {
  state: Polled<T>;
  /** Read again now: something was changed, or the user asked. */
  refresh: () => void;
  /**
   * Read again at the usual pace although the value was judged settled:
   * the caller awaits something the value does not tell of. Unlike
   * `refresh` it keeps the wait after reads that failed.
   */
  follow: () => void;
} {
  const [state, setState] = useState<Polled<T>>({ kind: "reading" });
  const mounted = useRef(true);
  // One read at a time, so answers cannot overtake each other.
  const inFlight = useRef(false);
  // A read was asked for while one was out: another follows it at once.
  const again = useRef(false);
  const failures = useRef(0);
  const notBefore = useRef(0);
  // The server refused the read: nothing more is read until it is asked for.
  const settled = useRef(false);
  const readAgain = useRef<() => void>(() => undefined);

  const readNow = useCallback(
    (asked: boolean) => {
      if (inFlight.current) {
        // The read that is out may have been answered before what the user
        // just did: its answer is followed by a new read.
        again.current = again.current || asked;
        return;
      }
      if (!asked && (settled.current || Date.now() < notBefore.current)) {
        return;
      }
      settled.current = false;
      inFlight.current = true;
      read()
        .then(
          (value) => {
            const judgement = judge?.(value) ?? "following";
            if (judgement === "failing") {
              failures.current += 1;
              notBefore.current = Date.now() + backoffMs(failures.current);
            } else {
              failures.current = 0;
              notBefore.current = 0;
            }
            // The value will not change again: only a request reads again.
            settled.current = judgement === "settled";
            if (mounted.current) {
              setState((shown) => ({
                kind: "read",
                value,
                staleError: null,
                number: shown.kind === "read" ? shown.number + 1 : 1,
              }));
            }
          },
          (error: unknown) => {
            if (isRefusal(error)) {
              settled.current = true;
              if (mounted.current) {
                // The server's own answer, in place of anything shown before.
                setState({ kind: "unreadable", error });
              }
              return;
            }
            failures.current += 1;
            notBefore.current = Date.now() + backoffMs(failures.current);
            if (mounted.current) {
              // What is already shown stays, with a note that it may be old.
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
          const asked = again.current;
          again.current = false;
          // Nothing is read for a screen that has been left.
          if (asked && mounted.current) {
            readAgain.current();
          }
        });
    },
    [read, judge],
  );

  useEffect(() => {
    readAgain.current = () => readNow(true);
  }, [readNow]);

  useEffect(() => {
    mounted.current = true;
    const poll = () => {
      // Nobody is looking at a hidden tab: nothing is read for it.
      if (!isHidden()) {
        readNow(false);
      }
    };
    poll();
    const timer = setInterval(poll, everyMs);
    // Back in view: catch up at once.
    document.addEventListener("visibilitychange", poll);
    return () => {
      mounted.current = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [readNow, everyMs]);

  const refresh = useCallback(() => readNow(true), [readNow]);
  const follow = useCallback(() => {
    settled.current = false;
    readNow(false);
  }, [readNow]);
  return { state, refresh, follow };
}
