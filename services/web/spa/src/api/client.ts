// The one API client (AD-19, coding-style.md rule 15). Every call to the
// server goes through `request`, which adds the demo role header.
import { parseCaseId } from "../audit/auditPath";
import { getRole } from "../role/roleStore";
import { strings } from "../strings";
import type {
  AuditTrail,
  CaseList,
  CaseProgress,
  CaseStarted,
  CaseStatus,
  ClassificationList,
  Decision,
  DecisionRecorded,
  ErrorBody,
  ErrorCode,
  Me,
  PageDecisionRequest,
  TriageQueue,
  UploadedCase,
} from "./contracts.gen";

export const ROLE_HEADER = "X-Demo-Role";
/** Sent with an upload, and again with its retry, so the retry makes no second case. */
export const IDEMPOTENCY_KEY_HEADER = "Idempotency-Key";
const API_ROOT = "/api";
/** The media type of an upload: the body is the PDF itself. */
const PDF = "application/pdf";
/** The media type of a page's thumbnail. */
const PNG = "image/png";

export class ApiError extends Error {
  readonly status: number;
  /** A code from the contracts catalogue, or null when the server sent no error body. */
  readonly code: ErrorCode | null;
  readonly traceId: string | null;

  constructor(
    status: number,
    code: ErrorCode | null,
    message: string,
    traceId: string | null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.traceId = traceId;
  }
}

/** The request never got an answer (offline, server down, or too slow). */
export class NetworkError extends Error {
  constructor() {
    super("The server could not be reached.");
    this.name = "NetworkError";
  }
}

/** How long a call may take before it is given up. */
export const REQUEST_TIMEOUT_MS = 30_000;
// Upload deadlines, shortest first, so each caller outlasts the one it calls:
//   intake 90 s (INTAKE_UPLOAD_DEADLINE_SECONDS)  <  web 120 s
//   (WEB_UPLOAD_TIMEOUT_SECONDS)  <  browser 150 s (this constant).
// The browser waits longest, so it shows the server's answer, not its own timeout.
export const UPLOAD_TIMEOUT_MS = 150_000;

interface RequestOptions {
  /** Sent as the JSON body. */
  json?: unknown;
  /** Sent as the body, as it is, declared as a PDF. */
  pdf?: Blob;
  /** Sent with the upload: see `IDEMPOTENCY_KEY_HEADER`. */
  idempotencyKey?: string;
  timeoutMs?: number;
}

function isErrorBody(value: unknown): value is ErrorBody {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }
  const detail = (value as { error: unknown }).error;
  return (
    typeof detail === "object" &&
    detail !== null &&
    typeof (detail as Record<string, unknown>).code === "string" &&
    typeof (detail as Record<string, unknown>).message === "string" &&
    typeof (detail as Record<string, unknown>).trace_id === "string"
  );
}

/**
 * One call to the server and its answer, read whole within the time limit.
 * A success is read as `accept` says: JSON, or the bytes of a file. A
 * failure is always read as JSON, for the server's error body.
 */
async function exchange(
  method: "GET" | "POST",
  path: string,
  options: RequestOptions,
  accept: "application/json" | typeof PNG,
): Promise<{ response: Response; payload: unknown }> {
  const headers = new Headers({ Accept: accept });
  // Read at call time, so a call made after a role switch carries the new role.
  const role = getRole();
  if (role !== null) {
    headers.set(ROLE_HEADER, role);
  }
  if (options.idempotencyKey !== undefined) {
    headers.set(IDEMPOTENCY_KEY_HEADER, options.idempotencyKey);
  }
  // A call that never answers is ended, so no screen waits forever.
  const timeout = new AbortController();
  const timer = setTimeout(
    () => timeout.abort(),
    options.timeoutMs ?? REQUEST_TIMEOUT_MS,
  );
  const init: RequestInit = {
    method,
    headers,
    credentials: "omit",
    signal: timeout.signal,
  };
  if (options.pdf !== undefined) {
    // The server decides whether the file really is a PDF, by its content.
    headers.set("Content-Type", PDF);
    init.body = options.pdf;
  } else if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    init.body = JSON.stringify(options.json);
  }

  let response: Response;
  let payload: unknown;
  try {
    response = await fetch(`${API_ROOT}${path}`, init);
    // Read inside the time limit too: a body can stall after the headers.
    payload =
      response.ok && accept === PNG
        ? await response.blob()
        : await response.json().catch(() => null);
  } catch {
    throw new NetworkError();
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    if (isErrorBody(payload)) {
      const { code, message, trace_id } = payload.error;
      throw new ApiError(response.status, code, message, trace_id);
    }
    throw new ApiError(response.status, null, "The request failed.", null);
  }
  return { response, payload };
}

async function request<T>(
  method: "GET" | "POST",
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { response, payload } = await exchange(
    method,
    path,
    options,
    "application/json",
  );
  if (typeof payload !== "object" || payload === null) {
    // A success must carry a JSON object: an empty or non-JSON body is a fault.
    throw new ApiError(response.status, null, "The answer was not JSON.", null);
  }
  return payload as T;
}

export function getMe(): Promise<Me> {
  return request<Me>("GET", "/me");
}

/** Whether a value is a created case as the contract describes it. */
export function isUploadedCase(value: unknown): value is UploadedCase {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    typeof record.case_id === "string" &&
    record.case_id !== "" &&
    typeof record.document_id === "string" &&
    record.document_id !== ""
  );
}

function isCaseStatus(value: unknown): value is CaseStatus {
  // Own keys only: "constructor" is no case status.
  return typeof value === "string" && Object.hasOwn(strings.caseStatus, value);
}

/** Whether an answer is about the case that was asked for, with a known status. */
function isAboutCase(
  value: unknown,
  caseId: string,
): value is Record<string, unknown> & { case_status: CaseStatus } {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return record.case_id === caseId && isCaseStatus(record.case_status);
}

/** A new key for one upload attempt; its retries send the same one. */
export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // Older browsers, and pages not served over HTTPS, have no randomUUID:
  // 16 random bytes as 32 hex digits do the same job.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join(
    "",
  );
}

/**
 * Upload one PDF as a new case. Only the customer role may. A repeat with
 * the same key is answered with the case the first call created.
 */
export async function uploadDocument(
  file: Blob,
  idempotencyKey: string,
): Promise<UploadedCase> {
  const uploaded = await request<unknown>("POST", "/cases", {
    pdf: file,
    idempotencyKey,
    timeoutMs: UPLOAD_TIMEOUT_MS,
  });
  if (!isUploadedCase(uploaded)) {
    // A success without a usable case is a fault: nothing is listed for it.
    throw new ApiError(201, null, "The answer was not a created case.", null);
  }
  return uploaded;
}

function casePath(caseId: string, resource: string): string {
  return `/cases/${encodeURIComponent(caseId)}/${resource}`;
}

/**
 * Start an uploaded case. Safe to repeat: a case that is already started is
 * left as it is and the same answer comes back.
 */
export async function startCase(caseId: string): Promise<CaseStarted> {
  // No options: the server starts the case with its defaults.
  const started = await request<unknown>("POST", casePath(caseId, "start"), {
    json: {},
  });
  if (!isAboutCase(started, caseId)) {
    throw new ApiError(200, null, "The answer was not a started case.", null);
  }
  return started as unknown as CaseStarted;
}

/** Whether a value has what a page of a progress is shown with. */
function isPageProgress(value: unknown): boolean {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const page = value as Record<string, unknown>;
  return (
    typeof page.page_id === "string" &&
    typeof page.page_number === "number" &&
    typeof page.page_status === "string"
  );
}

/** Read a case's status and pages. 404 `not_found` if it was never started. */
export async function getProgress(caseId: string): Promise<CaseProgress> {
  const progress = await request<unknown>("GET", casePath(caseId, "progress"));
  if (
    !isAboutCase(progress, caseId) ||
    !Array.isArray(progress.pages) ||
    !progress.pages.every(isPageProgress)
  ) {
    throw new ApiError(200, null, "The answer was not a progress.", null);
  }
  return progress as unknown as CaseProgress;
}

/** Whether a value is a number from 0 to 1 (NaN fails both comparisons). */
function isUnitNumber(value: unknown): boolean {
  return typeof value === "number" && value >= 0 && value <= 1;
}

/** Whether a value has what a classification is shown with. */
function isClassification(value: unknown): boolean {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const classification = value as Record<string, unknown>;
  return (
    typeof classification.page_id === "string" &&
    typeof classification.page_type === "string" &&
    isUnitNumber(classification.confidence)
  );
}

/**
 * Read what the classifier said of each page of a case: the page type and
 * the confidence, a number from 0 to 1.
 */
export async function getClassifications(
  caseId: string,
): Promise<ClassificationList> {
  const listed = await request<unknown>(
    "GET",
    casePath(caseId, "classifications"),
  );
  if (
    typeof listed !== "object" ||
    listed === null ||
    (listed as Record<string, unknown>).case_id !== caseId ||
    !Array.isArray((listed as Record<string, unknown>).classifications) ||
    !(listed as { classifications: unknown[] }).classifications.every(
      isClassification,
    )
  ) {
    throw new ApiError(200, null, "The answer was not classifications.", null);
  }
  return listed as ClassificationList;
}

/**
 * Whether a failed decision call may have stored the decision all the same:
 * no answer came, the server said the fault was its own, or it answered
 * "done" with something that was not the decision. Only a refusal (4xx)
 * stored nothing.
 */
export function mayBeStored(error: unknown): boolean {
  return (
    !(error instanceof ApiError) || error.status < 400 || error.status >= 500
  );
}

/**
 * Send a person's decision about one page. The server takes the actor from
 * the role header and decides whether that role may make that decision.
 * Safe to repeat: the same decision again is answered with the stored one.
 */
export async function decidePage(
  caseId: string,
  pageId: string,
  decision: Decision,
): Promise<DecisionRecorded> {
  const body: PageDecisionRequest = { decision };
  const recorded = await request<unknown>(
    "POST",
    casePath(caseId, `pages/${encodeURIComponent(pageId)}/decisions`),
    { json: body },
  );
  const record =
    typeof recorded === "object" && recorded !== null
      ? (recorded as Record<string, unknown>)
      : {};
  if (record.page_id !== pageId || record.decision !== decision) {
    throw new ApiError(200, null, "The answer was not the decision.", null);
  }
  return recorded as DecisionRecorded;
}

/** A page's thumbnail on this server: `/api/pages/<page id>/thumbnail`, and nothing after it. */
const THUMBNAIL_ADDRESS =
  /^\/api\/pages\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/thumbnail$/;

/** Whether a value is text with something in it. */
function isText(value: unknown): value is string {
  return typeof value === "string" && value !== "";
}

/**
 * Whether a value has what a row of the triage queue is shown with. What the
 * classifier said is there whole, or not at all.
 */
function isTriagePage(value: unknown): boolean {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const page = value as Record<string, unknown>;
  return (
    isText(page.case_id) &&
    isText(page.page_id) &&
    typeof page.page_number === "number" &&
    isText(page.thumbnail_path) &&
    ((page.page_type === null &&
      page.confidence === null &&
      page.reason === null) ||
      (typeof page.page_type === "string" &&
        isUnitNumber(page.confidence) &&
        typeof page.reason === "string"))
  );
}

/**
 * Read the pages that wait for the underwriter, across cases, each with
 * what the classifier said of it. Only the underwriter role may.
 */
export async function getTriageQueue(): Promise<TriageQueue> {
  const queue = await request<unknown>("GET", "/triage");
  const record = queue as Record<string, unknown>;
  if (
    !Array.isArray(record.pages) ||
    !record.pages.every(isTriagePage) ||
    typeof record.has_more !== "boolean"
  ) {
    throw new ApiError(200, null, "The answer was not a triage queue.", null);
  }
  return queue as TriageQueue;
}

/**
 * Read a page's thumbnail, the picture of the redacted page, from the
 * address the server gave for it. It is read here, with the role header
 * every call carries, and not by an image element, which could send none.
 */
export async function getThumbnail(address: string): Promise<Blob> {
  if (!THUMBNAIL_ADDRESS.test(address)) {
    // Only this server's own thumbnails are read, whatever an answer names.
    throw new ApiError(200, null, "The address was not a thumbnail's.", null);
  }
  const { response, payload } = await exchange(
    "GET",
    address.slice(API_ROOT.length),
    {},
    PNG,
  );
  const picture = payload as Blob;
  if (
    response.headers.get("Content-Type")?.split(";")[0]?.trim() !== PNG ||
    picture.size === 0
  ) {
    throw new ApiError(200, null, "The answer was not a picture.", null);
  }
  return picture;
}

/** Whether a value is a whole number that is not negative, as a count is. */
function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

/** Whether a value has what a row of the case list is shown with. */
function isCaseSummary(value: unknown): boolean {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const listed = value as Record<string, unknown>;
  return (
    // The id goes into the address of the case's trail: a case id, or nothing.
    isText(listed.case_id) &&
    parseCaseId(listed.case_id) === listed.case_id &&
    isText(listed.case_status) &&
    isText(listed.started_at) &&
    isCount(listed.page_count) &&
    isCount(listed.waiting_page_count) &&
    // No more pages wait than the case has.
    listed.waiting_page_count <= listed.page_count
  );
}

/**
 * Read the cases, newest first as the server orders them, and whether more
 * exist than are listed. Only the underwriter role may.
 */
export async function getCaseList(): Promise<CaseList> {
  const list = await request<unknown>("GET", "/cases");
  // `request` has refused an answer that is no JSON object, null included.
  const record = list as Record<string, unknown>;
  if (
    !Array.isArray(record.cases) ||
    !record.cases.every(isCaseSummary) ||
    typeof record.has_more !== "boolean"
  ) {
    throw new ApiError(200, null, "The answer was not a case list.", null);
  }
  return list as CaseList;
}

/** Whether a value has what an event of the audit trail is shown with. */
function isAuditEvent(value: unknown): boolean {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const event = value as Record<string, unknown>;
  return (
    isText(event.action) &&
    isText(event.actor) &&
    isText(event.actor_kind) &&
    isText(event.occurred_at) &&
    isText(event.ref) &&
    (event.page_id === null || isText(event.page_id)) &&
    (event.detail === null || typeof event.detail === "object") &&
    // The code is there for a failed step only: missing or null otherwise.
    (event.error_code == null || isText(event.error_code))
  );
}

/**
 * Read a case's audit trail: its events in the order the server recorded
 * them, and whether the case has more than are listed. Only the underwriter
 * role may. 404 `not_found` if the case was never started.
 */
export async function getAuditTrail(caseId: string): Promise<AuditTrail> {
  const trail = await request<unknown>("GET", casePath(caseId, "audit"));
  const record = trail as Record<string, unknown>;
  if (
    record.case_id !== caseId ||
    !Array.isArray(record.events) ||
    !record.events.every(isAuditEvent) ||
    typeof record.has_more !== "boolean"
  ) {
    throw new ApiError(200, null, "The answer was not an audit trail.", null);
  }
  return trail as AuditTrail;
}
