// A stand-in for the `web` service: answers `fetch` the way the real one does
// and records every call, so tests can assert on the headers that were sent.
import { vi } from "vitest";

export interface RecordedCall {
  path: string;
  method: string;
  role: string | null;
  contentType: string | null;
  body: BodyInit | null;
}

/** The ids the stand-in gives the next uploaded case. */
export const UPLOADED = {
  case_id: "019a0000-0000-7000-8000-000000000001",
  document_id: "019a0000-0000-7000-8000-000000000002",
  status: "running",
} as const;

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

export function fakeServer(
  respond?: (call: RecordedCall) => Response | Promise<Response>,
) {
  const calls: RecordedCall[] = [];
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
      calls.push(call);
      if (respond !== undefined) {
        return respond(call);
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
      return json(404, errorBody("not_found", "Not found."));
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return { calls };
}
