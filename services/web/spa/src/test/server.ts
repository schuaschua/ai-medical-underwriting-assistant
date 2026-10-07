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

/** One page of a progress, as `workflow` lists it: its number and its status. */
export function pageProgress(
  pageNumber: number,
  pageStatus: string,
  errorCode: string | null = null,
) {
  return {
    page_id: `019a0000-0000-7000-8000-0000000001${String(pageNumber).padStart(2, "0")}`,
    page_number: pageNumber,
    page_status: pageStatus,
    error_code: errorCode,
  };
}

/** The progress of a case; with no pages yet unless some are given. */
export function caseProgress(
  caseId: string,
  caseStatus = "running",
  redactionStatus = "running",
  pages: ReturnType<typeof pageProgress>[] = [],
) {
  return {
    case_id: caseId,
    case_status: caseStatus,
    redaction_status: redactionStatus,
    pages,
  };
}

/** One classification, as `classification` lists it for the page of that number. */
export function classification(
  caseId: string,
  pageNumber: number,
  pageType: string,
  confidence: number,
) {
  return {
    classification_id: `019a0000-0000-7000-8000-0000000002${String(pageNumber).padStart(2, "0")}`,
    case_id: caseId,
    page_id: pageProgress(pageNumber, "classified").page_id,
    contender: "llm",
    page_type: pageType,
    is_medical: !["id_document", "invoice", "other"].includes(pageType),
    confidence,
    reason: "A synthetic reason.",
  };
}

/** The answer to a decision, as `workflow` gives it. */
export function decisionRecorded(
  caseId: string,
  pageId: string,
  decision: string,
  role: string | null,
) {
  return {
    decision_id: "019a0000-0000-7000-8000-000000000301",
    case_id: caseId,
    page_id: pageId,
    decision,
    actor: role,
    page_status:
      {
        keep: "awaiting_triage",
        accept: "extracting",
        deny: "denied",
      }[decision] ?? "discarded",
    occurred_at: "2026-10-07T09:00:00Z",
  };
}

/** One page of the triage queue, as `web` lists it for the page of that number. */
export function triagePage(
  caseId: string,
  pageNumber: number,
  reading: { pageType: string; confidence: number; reason?: string } | null = {
    pageType: "lab_report",
    confidence: 0.6,
  },
  queuedBy: string | null = "gate",
) {
  const pageId = `${caseId.slice(0, -4)}${String(pageNumber).padStart(4, "0")}`;
  return {
    case_id: caseId,
    page_id: pageId,
    page_number: pageNumber,
    thumbnail_path: `/api/pages/${pageId}/thumbnail`,
    page_type: reading?.pageType ?? null,
    is_medical:
      reading === null
        ? null
        : !["id_document", "invoice", "other"].includes(reading.pageType),
    confidence: reading?.confidence ?? null,
    reason: reading === null ? null : (reading.reason ?? "A synthetic reason."),
    queued_by: queuedBy,
  };
}

/** A thumbnail as `web` serves it. The bytes are no picture and need not be. */
export function thumbnail(): Response {
  return new Response(new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]), {
    status: 200,
    headers: { "Content-Type": "image/png" },
  });
}

const THUMBNAIL_ROUTE = /^\/api\/pages\/[^/]+\/thumbnail$/;
const CASE_ROUTE = /^\/api\/cases\/([^/]+)\/(start|progress|classifications)$/;
const DECISION_ROUTE = /^\/api\/cases\/([^/]+)\/pages\/([^/]+)\/decisions$/;

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
      if (caseId !== undefined && resource === "classifications") {
        return json(200, { case_id: caseId, classifications: [] });
      }
      if (call.path === "/api/triage") {
        return call.role === "underwriter"
          ? json(200, { pages: [], has_more: false })
          : json(
              403,
              errorBody(
                "role_not_allowed",
                "This action is not open to your role.",
              ),
            );
      }
      if (THUMBNAIL_ROUTE.test(call.path)) {
        return thumbnail();
      }
      const [, decidedCase, decidedPage] = DECISION_ROUTE.exec(call.path) ?? [];
      if (
        decidedCase !== undefined &&
        decidedPage !== undefined &&
        call.method === "POST" &&
        typeof call.body === "string"
      ) {
        const sent = JSON.parse(call.body) as { decision: string };
        return json(
          200,
          decisionRecorded(decidedCase, decidedPage, sent.decision, call.role),
        );
      }
      return json(404, errorBody("not_found", "Not found."));
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return { calls, started };
}
