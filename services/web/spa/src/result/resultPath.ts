// Where the result view is, and how a case is named in its address. Kept
// apart from the screen, so that a link to it can be made anywhere.
import { CASE_PARAMETER } from "../audit/auditPath";

/** The result view, on the underwriter's side only (AD-9). */
export const RESULT_PATH = "/underwriter/result";

/** The address of one case's result. */
export function resultPath(caseId: string): string {
  return `${RESULT_PATH}?${CASE_PARAMETER}=${encodeURIComponent(caseId)}`;
}
