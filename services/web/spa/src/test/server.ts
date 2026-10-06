// A stand-in for the `web` service: answers `fetch` the way the real one does
// and records every call, so tests can assert on the headers that were sent.
import { vi } from "vitest";

export interface RecordedCall {
  path: string;
  method: string;
  role: string | null;
  contentType: string | null;
  body: BodyInit | null;
  /** The Idempotency-Key header, when the call carried one. */
  idempotencyKey?: string;
}

/** The ids the stand-in gives the next uploaded case. */
export const UPLOADED = {
  case_id: "019a0000-0000-7000-8000-000000000001",
  document_id: "019a0000-0000-7000-8000-000000000002",
} as const;

/** The answer to a start, as `workflow` gives it with no options sent. */
export function startedCase(caseId: string, caseStatus = "running") {
  return {
    case_id: caseId,
    case_status: caseStatus,
    classifier_contender: "llm",
    retriever_configs: ["r3"],
    stop_after: null,
    eval_run_id: null,
  };
}

/** The progress of a case with no pages yet. */
export function caseProgress(caseId: string, caseStatus = "running") {
  return {
    case_id: caseId,
    case_status: caseStatus,
    redaction_status: "running",
    pages: [],
  };
}

const CASE_ROUTE = /^\/api\/cases\/([^/]+)\/(start|progress)$/;

const NO_TRACE_ID = "0".repeat(32);

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function errorBody(
  code: string,
  message: string,
  traceId = NO_TRACE_ID,
) {
  return { error: { code, message, trace_id: traceId } };
}

/**
 * `respond` may answer any call itself; when it returns nothing, the stand-in
 * answers as the real service would. It knows which cases were started: the
 * progress of any other case is 404, as it is on the server.
 */
export function fakeServer(
  respond?: (
    call: RecordedCall,
  ) => Response | Promise<Response> | undefined | void,
) {
  const calls: RecordedCall[] = [];
  const started = new Set<string>();
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      const call: RecordedCall = {
        path: String(input),
        method: init?.method ?? "GET",
        role: headers.get("X-Demo-Role"),
        contentType: headers.get("Content-Type"),
        body: init?.body ?? null,
      };
      const idempotencyKey = headers.get("Idempotency-Key");
      if (idempotencyKey !== null) {
        call.idempotencyKey = idempotencyKey;
      }
      calls.push(call);
      const answer = respond?.(call);
      if (answer !== undefined) {
        return answer;
      }
      if (call.path === "/api/me") {
        return call.role === "customer" || call.role === "underwriter"
          ? json(200, { role: call.role })
          : json(
              400,
              errorBody("invalid_role", "The X-Demo-Role header is missing."),
            );
      }
      if (call.path === "/api/cases" && call.method === "POST") {
        return call.role === "customer"
          ? json(201, UPLOADED)
          : json(
              403,
              errorBody(
                "role_not_allowed",
                "This action is not open to your role.",
              ),
            );
      }
      const [, caseId, resource] = CASE_ROUTE.exec(call.path) ?? [];
      if (caseId !== undefined && resource === "start") {
        if (call.method !== "POST" || call.role !== "customer") {
          return json(
            403,
            errorBody(
              "role_not_allowed",
              "This action is not open to your role.",
            ),
          );
        }
        started.add(caseId);
        return json(200, startedCase(caseId));
      }
      if (caseId !== undefined && resource === "progress") {
        return started.has(caseId)
          ? json(200, caseProgress(caseId))
          : json(404, errorBody("not_found", "That case could not be found."));
      }
      return json(404, errorBody("not_found", "Not found."));
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return { calls, started };
}
