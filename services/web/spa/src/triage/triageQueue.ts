// The underwriter's triage queue as the server lists it. The SPA reads it by
// polling (AD-19) and never works out for itself which pages belong in it.
import { getTriageQueue } from "../api/client";
import type { TriageQueue } from "../api/contracts.gen";
import { usePolled, type Polled } from "../polling/polled";

/** How often the queue is read again. */
export const TRIAGE_POLL_MS = 3_000;

export type QueueState = Polled<TriageQueue>;

/**
 * Follows the queue: reads it now and then every `TRIAGE_POLL_MS` while the
 * page is visible, further apart while reads fail, and again on request.
 */
export function useTriageQueue(): {
  state: QueueState;
  /** Read the queue again now: a decision was made, or the user asked. */
  refresh: () => void;
} {
  return usePolled(getTriageQueue, TRIAGE_POLL_MS);
}
