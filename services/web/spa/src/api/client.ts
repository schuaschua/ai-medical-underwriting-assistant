// The one API client (AD-19, coding-style.md rule 15). Every call to the
// server goes through `request`, which adds the demo role header.
import { getRole } from "../role/roleStore";
import type { DemoRole, ErrorBody, ErrorCode } from "./contracts.gen";

export const ROLE_HEADER = "X-Demo-Role";
const API_ROOT = "/api";

/** Response of `GET /api/me`: the role as the server read it. */
export interface Me {
  role: DemoRole;
}

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
  body?: unknown,
): Promise<T> {
  const headers = new Headers({ Accept: "application/json" });
  // Read at call time, so a call made after a role switch carries the new role.
  const role = getRole();
  if (role !== null) {
    headers.set(ROLE_HEADER, role);
  }
  // A call that never answers is ended, so no screen waits forever.
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), REQUEST_TIMEOUT_MS);
  const init: RequestInit = {
    method,
    headers,
    credentials: "omit",
    signal: timeout.signal,
  };
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
    init.body = JSON.stringify(body);
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
