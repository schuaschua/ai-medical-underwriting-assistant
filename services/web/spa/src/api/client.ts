// The one API client (AD-19, coding-style.md rule 15). Every call to the
// server goes through `request`, which adds the demo role header.
import { getRole } from "../role/roleStore";
import { strings } from "../strings";
import type {
  CaseProgress,
  CaseStarted,
  CaseStatus,
  ErrorBody,
  ErrorCode,
  Me,
  UploadedCase,
} from "./contracts.gen";

export const ROLE_HEADER = "X-Demo-Role";
/** Sent with an upload, and again with its retry, so the retry makes no second case. */
export const IDEMPOTENCY_KEY_HEADER = "Idempotency-Key";
const API_ROOT = "/api";
/** The media type of an upload: the body is the PDF itself. */
const PDF = "application/pdf";

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

async function request<T>(
  method: "GET" | "POST",
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers = new Headers({ Accept: "application/json" });
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
    payload = await response.json().catch(() => null);
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

/** Read a case's status and pages. 404 `not_found` if it was never started. */
export async function getProgress(caseId: string): Promise<CaseProgress> {
  const progress = await request<unknown>("GET", casePath(caseId, "progress"));
  if (!isAboutCase(progress, caseId) || !Array.isArray(progress.pages)) {
    throw new ApiError(200, null, "The answer was not a progress.", null);
  }
  return progress as unknown as CaseProgress;
}
