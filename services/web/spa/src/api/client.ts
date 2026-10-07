// The one API client (AD-19, coding-style.md rule 15). Every call to the
// server goes through `request`, which adds the demo role header.
import { parseCaseId } from "../audit/auditPath";
import { getRole } from "../role/roleStore";
import { strings } from "../strings";
import type {
  AgentStepList,
  AuditTrail,
  CaseList,
  CaseProgress,
  CaseStarted,
  CaseStatus,
  ClassificationList,
  ClassificationScoreboard,
  ComparePairs,
  Decision,
  DecisionRecorded,
  ErrorBody,
  ErrorCode,
  FactList,
  Me,
  PageBoxes,
  PageDecisionRequest,
  PageList,
  RedactionScoreboard,
  RetrievalScoreboard,
  RetrieverConfig,
  RuleText,
  ToolName,
  TriageQueue,
  UploadedCase,
  VerdictRunList,
  VerdictRunRequested,
} from "./contracts.gen";

export const ROLE_HEADER = "X-Demo-Role";
/** Sent with an upload, and again with its retry, so the retry makes no second case. */
export const IDEMPOTENCY_KEY_HEADER = "Idempotency-Key";
const API_ROOT = "/api";
/** The media type of an upload, and of the redacted document: the body is the PDF itself. */
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
// The redacted PDF is a file of up to 10 MB: its read has a deadline of its
// own, and again the browser waits longer than the server it asks:
//   web 60 s (WEB_DOCUMENT_TIMEOUT_SECONDS)  <  browser 90 s (this constant).
export const DOCUMENT_TIMEOUT_MS = 90_000;

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
  accept: "application/json" | typeof PNG | typeof PDF,
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
      response.ok && accept !== "application/json"
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

/**
 * One file from this server, read here with the role header every call
 * carries: an element of the page could send none (AD-9). The answer must
 * be of the media type asked for, and hold something.
 */
async function file(
  path: string,
  mediaType: typeof PNG | typeof PDF,
  timeoutMs?: number,
) {
  const { response, payload } = await exchange(
    "GET",
    path,
    timeoutMs === undefined ? {} : { timeoutMs },
    mediaType,
  );
  const content = payload as Blob;
  if (
    response.headers.get("Content-Type")?.split(";")[0]?.trim() !== mediaType ||
    content.size === 0
  ) {
    throw new ApiError(200, null, "The answer was not that file.", null);
  }
  return content;
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
  return file(address.slice(API_ROOT.length), PNG);
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

// --- The result view (story 2.7) ---------------------------------------------
//
// Each answer is checked for what the screen shows from it, so that an
// answer of another shape is a fault of that part and never a broken page.
// Nothing here works anything out of an answer: no verdict, no loading, no
// verification of a quote.

/** An id as the contracts spell it. It goes into an address, so it is this or nothing. */
const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
/** A rule's id as the manual prints it (AD-12). */
const RULE_ID = /^UW-[A-Z]{2,4}-[0-9]{3}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** Whether a value is a page number: a whole number from 1. */
function isPageNumber(value: unknown): value is number {
  return isCount(value) && value >= 1;
}

/** Whether a value is a number a length or a percentage can be. */
function isAmount(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

/** Whether a value has what a fact is shown with. The offsets are there when, and only when, the server says the quote was found. */
function isFact(value: unknown): boolean {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isText(value.fact_id) &&
    isText(value.page_id) &&
    ID.test(value.page_id) &&
    isPageNumber(value.page_number) &&
    typeof value.statement === "string" &&
    typeof value.quote === "string" &&
    (value.quote_verified === true
      ? isCount(value.quote_start) && isCount(value.quote_end)
      : value.quote_verified === false &&
        value.quote_start === null &&
        value.quote_end === null)
  );
}

/** Read a case's facts, in the order the server lists them. Only the underwriter role may. */
export async function getFacts(caseId: string): Promise<FactList> {
  const listed = await request<unknown>("GET", casePath(caseId, "facts"));
  if (
    !isRecord(listed) ||
    listed.case_id !== caseId ||
    !Array.isArray(listed.facts) ||
    !listed.facts.every(isFact)
  ) {
    throw new ApiError(200, null, "The answer was not facts.", null);
  }
  return listed as unknown as FactList;
}

/** Whether a value has what a reason is shown with: its rule, its facts and its effect. */
function isReason(value: unknown): boolean {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isText(value.rule_id) &&
    RULE_ID.test(value.rule_id) &&
    Array.isArray(value.fact_ids) &&
    value.fact_ids.length > 0 &&
    value.fact_ids.every(isText) &&
    isText(value.effect) &&
    (value.debit_pct === null || isAmount(value.debit_pct))
  );
}

/** Whether a value has what a verdict run is shown with, the label included (AD-10). */
function isVerdictRun(value: unknown): boolean {
  if (!isRecord(value)) {
    return false;
  }
  return (
    isText(value.verdict_run_id) &&
    isText(value.retriever_config) &&
    isText(value.status) &&
    isText(value.label) &&
    (value.verdict === null || isText(value.verdict)) &&
    (value.loading_pct === null || isAmount(value.loading_pct)) &&
    (value.confidence === null || isUnitNumber(value.confidence)) &&
    Array.isArray(value.reasons) &&
    value.reasons.every(isReason) &&
    Array.isArray(value.system_reasons) &&
    value.system_reasons.every(isText) &&
    (value.error_code === null || isText(value.error_code))
  );
}

/**
 * Read a case's verdict runs, oldest first as the server lists them, each a
 * suggestion with its label. Only the underwriter role may.
 */
export async function getVerdictRuns(caseId: string): Promise<VerdictRunList> {
  const listed = await request<unknown>(
    "GET",
    casePath(caseId, "verdict-runs"),
  );
  if (
    !isRecord(listed) ||
    listed.case_id !== caseId ||
    !Array.isArray(listed.verdict_runs) ||
    !listed.verdict_runs.every(isVerdictRun) ||
    typeof listed.has_more !== "boolean"
  ) {
    throw new ApiError(200, null, "The answer was not verdict runs.", null);
  }
  return listed as unknown as VerdictRunList;
}

// --- The agent's log (story 2.8) ----------------------------------------------

/** How a read of a run's steps is narrowed; null means "not by this". The server does the narrowing. */
export interface StepNarrowing {
  tool: ToolName | null;
  ruleId: string | null;
}

/** Whether a value has what a step of that run is shown with. */
function isStepOf(runId: string): (value: unknown) => boolean {
  return (value) =>
    isRecord(value) &&
    value.verdict_run_id === runId &&
    isPageNumber(value.step_no) &&
    isText(value.tool) &&
    isRecord(value.arguments) &&
    !Array.isArray(value.arguments) &&
    (value.fact_id === null || isText(value.fact_id)) &&
    Array.isArray(value.rule_ids) &&
    value.rule_ids.every(isText) &&
    isText(value.outcome) &&
    (value.error_code === null || isText(value.error_code)) &&
    isAmount(value.latency_ms) &&
    isText(value.occurred_at);
}

/**
 * Read the tool calls of one verdict run, in the order the agent made them
 * (AD-15): only that tool's, and only those that returned or read that
 * rule, when asked so, and only those after the step number last seen.
 * `has_more` says that the run has more than this answer lists. Only the
 * underwriter role may. 404 `not_found` for a run the server does not hold.
 */
export async function getRunSteps(
  runId: string,
  narrowing: StepNarrowing,
  afterStepNo: number | null,
): Promise<AgentStepList> {
  if (!ID.test(runId)) {
    throw new ApiError(200, null, "That was not a run's id.", null);
  }
  const asked = new URLSearchParams();
  if (narrowing.tool !== null) {
    asked.set("tool", narrowing.tool);
  }
  if (narrowing.ruleId !== null) {
    asked.set("rule_id", narrowing.ruleId);
  }
  if (afterStepNo !== null) {
    asked.set("after_step_no", String(afterStepNo));
  }
  const query = asked.toString();
  const listed = await request<unknown>(
    "GET",
    `/verdict-runs/${runId}/steps${query === "" ? "" : `?${query}`}`,
  );
  if (
    !isRecord(listed) ||
    !Array.isArray(listed.steps) ||
    !listed.steps.every(isStepOf(runId)) ||
    typeof listed.has_more !== "boolean" ||
    // "More" with nothing listed would be asked for again without end.
    (listed.has_more && listed.steps.length === 0) ||
    // Each step comes after the one before it, and the first after the
    // cursor: a step listed again would be shown twice.
    !(listed.steps as { step_no: number }[]).every(
      (step, place, steps) =>
        step.step_no > (steps[place - 1]?.step_no ?? afterStepNo ?? 0),
    )
  ) {
    throw new ApiError(200, null, "The answer was not agent steps.", null);
  }
  return listed as unknown as AgentStepList;
}

/** Whether text is a rule's id as the manual prints it. A convenience: the server checks it again. */
export function isRuleId(text: string): boolean {
  return RULE_ID.test(text);
}

/** Read the manual's text of one rule. 404 `not_found` if the manual has no such rule. */
export async function getRule(ruleId: string): Promise<RuleText> {
  if (!RULE_ID.test(ruleId)) {
    throw new ApiError(200, null, "That was not a rule's id.", null);
  }
  const rule = await request<unknown>(
    "GET",
    `/rules/${encodeURIComponent(ruleId)}`,
  );
  if (
    !isRecord(rule) ||
    rule.rule_id !== ruleId ||
    typeof rule.text !== "string" ||
    !isPageNumber(rule.manual_page) ||
    !isText(rule.impairment)
  ) {
    throw new ApiError(200, null, "The answer was not a rule.", null);
  }
  return rule as unknown as RuleText;
}

function isPage(value: unknown): boolean {
  return (
    isRecord(value) &&
    isText(value.page_id) &&
    isText(value.document_id) &&
    ID.test(value.document_id) &&
    isPageNumber(value.page_number)
  );
}

/**
 * Read a case's pages: which document they belong to, and each one's
 * number. The list is empty until the document is redacted.
 */
export async function getPages(caseId: string): Promise<PageList> {
  const listed = await request<unknown>("GET", casePath(caseId, "pages"));
  if (
    !isRecord(listed) ||
    listed.case_id !== caseId ||
    !Array.isArray(listed.pages) ||
    !listed.pages.every(isPage)
  ) {
    throw new ApiError(200, null, "The answer was not pages.", null);
  }
  return listed as unknown as PageList;
}

// A box may end a hair beyond its page by rounding; further out is a fault.
const PAGE_EDGE_SLACK_POINTS = 1;

/**
 * Whether a value is the box of a word on a page of that size: whole-number
 * offsets into the page text, and four lengths, from a top-left to a
 * bottom-right corner, that lie on the page.
 */
function isBoxOn(width: number, height: number): (value: unknown) => boolean {
  return (value) =>
    isRecord(value) &&
    isCount(value.char_start) &&
    isCount(value.char_end) &&
    value.char_start < value.char_end &&
    isAmount(value.x0) &&
    isAmount(value.y0) &&
    isAmount(value.x1) &&
    isAmount(value.y1) &&
    value.x0 <= value.x1 &&
    value.y0 <= value.y1 &&
    value.x1 <= width + PAGE_EDGE_SLACK_POINTS &&
    value.y1 <= height + PAGE_EDGE_SLACK_POINTS;
}

/**
 * Read where the words of an offset range sit on a page (AD-14). The
 * offsets are a fact's own, handed back as the server gave them: the
 * browser never looks for a quote in any text itself. The answer must be
 * about that page, under the page number the fact names, with every box on
 * the page: boxes of another page, or beside the page, are never drawn.
 */
export async function getPageBoxes(
  pageId: string,
  pageNumber: number,
  quoteStart: number,
  quoteEnd: number,
): Promise<PageBoxes> {
  if (!ID.test(pageId)) {
    throw new ApiError(200, null, "That was not a page's id.", null);
  }
  const range = new URLSearchParams({
    quote_start: String(quoteStart),
    quote_end: String(quoteEnd),
  });
  const answered = await request<unknown>(
    "GET",
    `/pages/${pageId}/boxes?${range.toString()}`,
  );
  if (
    !isRecord(answered) ||
    answered.page_id !== pageId ||
    answered.page_number !== pageNumber ||
    !isAmount(answered.page_width) ||
    !isAmount(answered.page_height) ||
    answered.page_width === 0 ||
    answered.page_height === 0 ||
    !Array.isArray(answered.boxes) ||
    !answered.boxes.every(isBoxOn(answered.page_width, answered.page_height))
  ) {
    throw new ApiError(200, null, "The answer was not word boxes.", null);
  }
  return answered as unknown as PageBoxes;
}

/**
 * Read a document's file: the redacted PDF, the only file of a document
 * the server has a route for (AD-21). 409 `not_redacted` until there is one.
 */
export async function getDocumentFile(documentId: string): Promise<Blob> {
  if (!ID.test(documentId)) {
    throw new ApiError(200, null, "That was not a document's id.", null);
  }
  return file(`/documents/${documentId}/file`, PDF, DOCUMENT_TIMEOUT_MS);
}

// --- The scoreboard files (story 3.5) ------------------------------------------
//
// AD-17: the scores are files the bake-off runner wrote, and the server has
// checked each against its model. An answer is checked here for what the
// screen shows from it, so that one of another shape is a fault and never a
// half-drawn table. Nothing here works a figure out.

/** The rows of the ladder, in its order: a scoreboard lists exactly these. */
const LADDER = ["r1", "r2", "r3", "r4", "r5", "r6"];

function isNullOr(
  is: (value: unknown) => boolean,
): (value: unknown) => boolean {
  return (value) => value === null || is(value);
}

/** Whether a value says when and against which address a bake-off run was made. */
function isScoreboardRun(value: unknown): boolean {
  return (
    isRecord(value) &&
    isText(value.eval_run_id) &&
    isText(value.started_at) &&
    isText(value.finished_at) &&
    isText(value.web_address) &&
    typeof value.stand_ins === "boolean"
  );
}

/** Whether a value is a figure somebody stated: an amount as text, its unit and its source. */
function isStatedFigure(value: unknown): boolean {
  return (
    isRecord(value) &&
    isText(value.amount) &&
    isText(value.unit) &&
    isText(value.source)
  );
}

/** Whether a value has what a line of the scoreboard is shown with, as that row of the ladder. */
function isRowScore(value: unknown, place: number): boolean {
  if (!isRecord(value)) {
    return false;
  }
  const figures = [value.rule_recall, value.verdict_accuracy];
  const counts = [
    value.recall_hits,
    value.recall_searches,
    value.right_runs,
    value.cases,
    value.failed_runs,
    value.latency_searches,
  ];
  const latencies = [value.latency_ms_median, value.latency_ms_p95];
  return (
    value.retriever_config === LADDER[place] &&
    isText(value.store) &&
    isText(value.chunk_set) &&
    isText(value.method) &&
    typeof value.measured === "boolean" &&
    isNullOr(isStatedFigure)(value.cost) &&
    isNullOr(isStatedFigure)(value.effort) &&
    figures.every(isNullOr(isUnitNumber)) &&
    latencies.every(isNullOr(isAmount)) &&
    // A measured row has the counts behind its figures; a row that is not
    // measured has none.
    (value.measured
      ? counts.every(isCount)
      : counts.every((count) => count === null))
  );
}

/**
 * Read the retrieval scoreboard: every row of the ladder as the bake-off
 * runner scored it, and the winner it names. Only the underwriter role may.
 * 404 `not_found` until the bake-off has been run.
 */
export async function getRetrievalScoreboard(): Promise<RetrievalScoreboard> {
  const board = await request<unknown>("GET", "/scoreboards/retrieval");
  if (
    !isRecord(board) ||
    !isScoreboardRun(board.run) ||
    !isPageNumber(board.top_k) ||
    !Array.isArray(board.rows) ||
    board.rows.length !== LADDER.length ||
    !board.rows.every(isRowScore) ||
    !(board.winner === null || LADDER.some((row) => row === board.winner)) ||
    !Array.isArray(board.failed_searches) ||
    !Array.isArray(board.unscored_cases)
  ) {
    throw new ApiError(200, null, "The answer was not a scoreboard.", null);
  }
  return board as unknown as RetrievalScoreboard;
}

/**
 * Read the redaction report of the same bake-off run: whether a planted
 * identifier was left in a stored page text. Only the underwriter role may.
 * 404 `not_found` until there is one.
 */
export async function getRedactionScoreboard(): Promise<RedactionScoreboard> {
  const report = await request<unknown>("GET", "/scoreboards/redaction");
  if (
    !isRecord(report) ||
    !isScoreboardRun(report.run) ||
    typeof report.clean !== "boolean" ||
    !isCount(report.cases_checked) ||
    !isCount(report.pages_checked) ||
    !Array.isArray(report.leaks) ||
    !Array.isArray(report.cases_not_checked)
  ) {
    throw new ApiError(
      200,
      null,
      "The answer was not a redaction report.",
      null,
    );
  }
  return report as unknown as RedactionScoreboard;
}

// --- The classifier scoreboard (story 4.3) --------------------------------------

/** The classifier contenders, in the contracts' order: a scoreboard lists exactly these. */
const CONTENDERS = ["llm", "doc-intelligence"];

/** Whether a value has what a line of the classifier scoreboard is shown with, as that contender. */
function isClassifierScore(value: unknown, place: number): boolean {
  if (!isRecord(value)) {
    return false;
  }
  const figures = [value.accuracy, value.calibration, value.queue_rate];
  const counts = [
    value.pages,
    value.right_pages,
    value.confident_pages,
    value.confident_right_pages,
    value.queued_pages,
    value.pages_not_classified,
  ];
  return (
    value.contender === CONTENDERS[place] &&
    typeof value.measured === "boolean" &&
    isNullOr(isStatedFigure)(value.cost_per_page) &&
    figures.every(isNullOr(isUnitNumber)) &&
    // A measured contender has the counts behind its figures; one that is
    // not measured has none.
    (value.measured
      ? counts.every(isCount)
      : counts.every((count) => count === null))
  );
}

/** Whether a value is about one of the contenders and a file of the page set. */
function namesContender(value: unknown): boolean {
  return (
    isRecord(value) &&
    CONTENDERS.some((contender) => contender === value.contender) &&
    isText(value.case_key)
  );
}

/**
 * Read the classifier scoreboard: both contenders as the bake-off runner
 * scored them on the page set, and the winner it names. Only the
 * underwriter role may. 404 `not_found` until that bake-off has been run.
 */
export async function getClassificationScoreboard(): Promise<ClassificationScoreboard> {
  const board = await request<unknown>("GET", "/scoreboards/classification");
  if (
    !isRecord(board) ||
    !isScoreboardRun(board.run) ||
    !Array.isArray(board.contenders) ||
    board.contenders.length !== CONTENDERS.length ||
    !board.contenders.every(isClassifierScore) ||
    !(
      board.winner === null ||
      CONTENDERS.some((contender) => contender === board.winner)
    ) ||
    !Array.isArray(board.unclassified_pages) ||
    !isCount(board.reasons_checked) ||
    // What the screen words by classifier names its contender.
    ![
      board.not_run,
      board.unscored_cases,
      board.reason_leaks,
      board.reasons_not_checked,
    ].every((list) => Array.isArray(list) && list.every(namesContender))
  ) {
    throw new ApiError(
      200,
      null,
      "The answer was not a classifier scoreboard.",
      null,
    );
  }
  return board as unknown as ClassificationScoreboard;
}

// --- Compare (story 3.6) --------------------------------------------------------
//
// AD-11: two verdict runs of one finished case side by side. The server says
// which rows to show and whether a row can be run; nothing here decides it.

/** The states a requested run can be in: any other is no answer. */
const RUN_STATES = ["running", "done", "failed"];

/** Whether a value names two different rows of the ladder. */
function isRetrieverPair(value: unknown): boolean {
  return (
    isRecord(value) &&
    LADDER.some((row) => row === value.first) &&
    LADDER.some((row) => row === value.second) &&
    value.first !== value.second
  );
}

/**
 * Read the pairs of retrieval rows Compare shows: the default pair, and the
 * pair to use when a row of the default one cannot be run here. A setting
 * of the server. Only the underwriter role may.
 */
export async function getComparePairs(): Promise<ComparePairs> {
  const pairs = await request<unknown>("GET", "/compare-pairs");
  if (
    !isRecord(pairs) ||
    !isRetrieverPair(pairs.default_pair) ||
    !isRetrieverPair(pairs.fallback_pair)
  ) {
    throw new ApiError(200, null, "The answer was not Compare's pairs.", null);
  }
  return pairs as unknown as ComparePairs;
}

/**
 * Ask for one more verdict run on a finished case, with that retrieval row.
 * The same case and row again is answered with the state of the run there
 * is, with one exception: a run that ended without a stored result is
 * scheduled again. So a screen sends it once for a row. Only the underwriter role may. 409
 * `retriever_not_available` for a row that cannot be run here, and 409
 * `pages_not_terminal` for a case that is not finished.
 */
export async function requestVerdictRun(
  caseId: string,
  row: RetrieverConfig,
): Promise<VerdictRunRequested> {
  const requested = await request<unknown>(
    "POST",
    casePath(caseId, "verdict-runs"),
    { json: { retriever_config: row } },
  );
  if (
    !isRecord(requested) ||
    requested.case_id !== caseId ||
    requested.retriever_config !== row ||
    !RUN_STATES.some((status) => status === requested.status) ||
    !isNullOr(isText)(requested.verdict_run_id) ||
    // The code is there for a failed run only: missing or null otherwise.
    !(requested.error_code == null || isText(requested.error_code))
  ) {
    throw new ApiError(200, null, "The answer was not a requested run.", null);
  }
  return requested as unknown as VerdictRunRequested;
}
