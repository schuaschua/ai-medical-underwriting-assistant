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

/** One case of the underwriter's list, as `workflow` lists it. */
export function caseSummary(
  caseId: string,
  caseStatus = "running",
  pageCount = 0,
  waitingPageCount = 0,
  startedAt = "2026-10-07T09:00:00Z",
) {
  return {
    case_id: caseId,
    case_status: caseStatus,
    started_at: startedAt,
    page_count: pageCount,
    waiting_page_count: waitingPageCount,
  };
}

/** When and where a bake-off run was made, as both scoreboard files say it. */
export const SCOREBOARD_RUN = {
  eval_run_id: "0199b7a0-0000-7000-8000-000000000006",
  started_at: "2026-10-08T09:00:00Z",
  finished_at: "2026-10-08T09:10:05Z",
  web_address: "http://localhost:8000",
  stand_ins: true,
};

/** One row of the retrieval scoreboard: measured with these figures, or not measured. */
export function rowScore(
  retrieverConfig: string,
  measured: boolean,
  changes: Record<string, unknown> = {},
) {
  const numbers = {
    rule_recall: 0.9744,
    recall_hits: 38,
    recall_searches: 39,
    verdict_accuracy: 0.6818,
    right_runs: 15,
    cases: 22,
    failed_runs: 0,
    latency_ms_median: 20,
    latency_ms_p95: 35,
    latency_searches: 39,
  };
  return {
    retriever_config: retrieverConfig,
    store: "pgvector",
    chunk_set: "smart",
    method: "Vector only",
    measured,
    ...(measured
      ? numbers
      : Object.fromEntries(Object.keys(numbers).map((name) => [name, null]))),
    cost: null,
    effort: null,
    ...changes,
  };
}

/** The retrieval scoreboard as the runner writes it: `r1`, `r2`, `r3` and `r5` measured, `r5` the winner. */
export function retrievalScoreboard(changes: Record<string, unknown> = {}) {
  return {
    run: SCOREBOARD_RUN,
    top_k: 5,
    rows: ["r1", "r2", "r3", "r4", "r5", "r6"].map((config) =>
      rowScore(config, !["r4", "r6"].includes(config)),
    ),
    winner: "r5",
    failed_searches: [],
    unscored_cases: [],
    ...changes,
  };
}

/** The redaction report of the same run: clean unless told otherwise. */
export function redactionScoreboard(changes: Record<string, unknown> = {}) {
  return {
    run: SCOREBOARD_RUN,
    clean: true,
    cases_checked: 22,
    pages_checked: 94,
    identifiers_checked: 180,
    leaks: [],
    may_also_be_redacted: 30,
    may_also_be_redacted_masked: 4,
    cases_not_checked: [],
    ...changes,
  };
}

/** One line of the classifier scoreboard: measured with these figures, or not measured. */
export function classifierScore(
  contender: string,
  measured: boolean,
  changes: Record<string, unknown> = {},
) {
  const numbers = {
    pages: 94,
    accuracy: 0.9574,
    right_pages: 90,
    calibration: 0.975,
    confident_pages: 80,
    confident_right_pages: 78,
    queue_rate: 0.1277,
    queued_pages: 12,
    pages_not_classified: 2,
  };
  return {
    contender,
    measured,
    ...(measured
      ? numbers
      : Object.fromEntries(Object.keys(numbers).map((name) => [name, null]))),
    cost_per_page: null,
    ...changes,
  };
}

/** The classifier scoreboard as the runner writes it: both contenders measured, `llm` the winner. */
export function classificationScoreboard(
  changes: Record<string, unknown> = {},
) {
  return {
    run: SCOREBOARD_RUN,
    contenders: [
      classifierScore("llm", true),
      classifierScore("doc-intelligence", true),
    ],
    winner: "llm",
    not_run: [],
    unscored_cases: [],
    unclassified_pages: [],
    reasons_checked: 184,
    reason_leaks: [],
    reasons_not_checked: [],
    ...changes,
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
const CASE_ROUTE =
  /^\/api\/cases\/([^/]+)\/(start|progress|classifications|audit)$/;
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
      if (call.path === "/api/cases" && call.method === "GET") {
        return call.role === "underwriter"
          ? json(200, { cases: [], has_more: false })
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
      if (caseId !== undefined && resource === "audit") {
        if (call.role !== "underwriter") {
          return json(
            403,
            errorBody(
              "role_not_allowed",
              "This action is not open to your role.",
            ),
          );
        }
        return started.has(caseId)
          ? json(200, { case_id: caseId, events: [], has_more: false })
          : json(404, errorBody("not_found", "That case could not be found."));
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
