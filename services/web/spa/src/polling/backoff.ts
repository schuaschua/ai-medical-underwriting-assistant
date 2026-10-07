// What every read that is repeated shares (AD-19): how long to wait after
// reads that failed, and which failures no repeat can mend.
import { ApiError } from "../api/client";

/** The wait after the first failed read; doubled with each one after it. */
export const BACKOFF_BASE_MS = 3_000;
/** The longest wait between two reads while reads fail. */
export const MAX_BACKOFF_MS = 30_000;

/** The wait after `failures` failed reads in a row: doubled each time, to a limit. */
export function backoffMs(failures: number): number {
  return Math.min(BACKOFF_BASE_MS * 2 ** failures, MAX_BACKOFF_MS);
}

const TOO_MANY_REQUESTS = 429;

/**
 * Whether the server refused the read itself (a 4xx): asking again as it is
 * gets the same answer. Too many requests is the one refusal that passes.
 */
export function isRefusal(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status >= 400 &&
    error.status < 500 &&
    error.status !== TOO_MANY_REQUESTS
  );
}
