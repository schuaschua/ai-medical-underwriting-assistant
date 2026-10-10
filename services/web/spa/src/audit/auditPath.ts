// Where the audit trail screen is, and how a case is named in its address.
// Kept apart from the screen, so that a link to it can be made anywhere.

/** The audit trail screen, on the underwriter's side only (AD-9). */
export const AUDIT_PATH = "/underwriter/audit";
/** The query parameter that names the case whose trail is shown. */
export const CASE_PARAMETER = "case";

// A case id is a UUIDv7 in lower case, as the contracts spell it.
const CASE_ID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/**
 * The case id in typed or pasted text, or null if the text is not one.
 * Spaces around it and capital letters are a matter of typing, not of the
 * id. A convenience only: the server checks the id again (security rule 20).
 */
export function parseCaseId(text: string): string | null {
  const caseId = text.trim().toLowerCase();
  return CASE_ID.test(caseId) ? caseId : null;
}

/** The address of one case's audit trail. */
export function auditTrailPath(caseId: string): string {
  return `${AUDIT_PATH}?${CASE_PARAMETER}=${encodeURIComponent(caseId)}`;
}
