// The underwriter's list of cases as the server lists it. The SPA reads it by
// polling (AD-19) and never works out for itself which cases belong in it, in
// what order, or what a case waits for.
import { getCaseList } from "../api/client";
import type { CaseList } from "../api/contracts.gen";
import { usePolled, type Polled } from "../polling/polled";

/** How often the list is read again. */
export const CASE_LIST_POLL_MS = 5_000;
/** The case list screen, on the underwriter's side only (AD-9). */
export const CASE_LIST_PATH = "/underwriter/cases";

/**
 * Follows the list: reads it now and then every `CASE_LIST_POLL_MS` while the
 * page is visible, further apart while reads fail, and again on request.
 */
export function useCaseList(): {
  state: Polled<CaseList>;
  /** Read the list again now: the user asked. */
  refresh: () => void;
} {
  return usePolled(getCaseList, CASE_LIST_POLL_MS);
}
