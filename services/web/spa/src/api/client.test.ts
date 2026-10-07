import { afterEach, describe, expect, it, vi } from "vitest";
import { setRole } from "../role/roleStore";
import {
  caseProgress,
  caseSummary,
  classification,
  decisionRecorded,
  errorBody,
  fakeServer,
  json,
  pageProgress,
  startedCase,
  thumbnail,
  triagePage,
  UPLOADED,
} from "../test/server";
import {
  ApiError,
  decidePage,
  getAuditTrail,
  getCaseList,
  getClassifications,
  getMe,
  getProgress,
  getThumbnail,
  getTriageQueue,
  IDEMPOTENCY_KEY_HEADER,
  NetworkError,
  newIdempotencyKey,
  REQUEST_TIMEOUT_MS,
  startCase,
  UPLOAD_TIMEOUT_MS,
  uploadDocument,
} from "./client";

const KEY = "3f2b8a52-6c1d-4c43-9d0e-0a8f5a1b2c3d";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("1.3 API client", () => {
  it("sends the chosen role in X-Demo-Role, to the same origin under /api", async () => {
    const server = fakeServer();
    setRole("underwriter");

    await expect(getMe()).resolves.toEqual({ role: "underwriter" });

    expect(server.calls).toEqual([
      {
        path: "/api/me",
        method: "GET",
        role: "underwriter",
        contentType: null,
        body: null,
      },
    ]);
  });

  it("sends the new role on every call after a switch", async () => {
    const server = fakeServer();
    setRole("customer");
    await getMe();

    setRole("underwriter");
    await getMe();
    await getMe();

    expect(server.calls.map((call) => call.role)).toEqual([
      "customer",
      "underwriter",
      "underwriter",
    ]);
  });

  it("turns the server's error body into an ApiError with code and trace id", async () => {
    const traceId = "0af7651916cd43dd8448eb211c80319c";
    fakeServer(() =>
      json(
        403,
        errorBody("role_not_allowed", "This action is not open.", traceId),
      ),
    );
    setRole("customer");

    const error = await getMe().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 403,
      code: "role_not_allowed",
      traceId,
    });
  });

  it("reports a failure with no error body without a code", async () => {
    fakeServer(() => new Response("<html>bad gateway</html>", { status: 502 }));
    setRole("customer");

    const error = await getMe().catch((caught: unknown) => caught);

    expect(error).toMatchObject({ status: 502, code: null, traceId: null });
  });

  it.each([
    ["an empty body", () => new Response(null, { status: 204 })],
    ["a body that is not JSON", () => new Response("<html>ok</html>")],
    ["a JSON value that is not an object", () => new Response("null")],
  ])("reports a success with %s as an ApiError", async (_name, respond) => {
    fakeServer(respond);
    setRole("customer");

    const error = await getMe().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: null, traceId: null });
  });

  it("gives up on a call that never answers", async () => {
    vi.useFakeTimers();
    try {
      // A server that accepts the call and then says nothing, until aborted.
      const fetchMock = vi.fn(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            );
          }),
      );
      vi.stubGlobal("fetch", fetchMock);
      setRole("customer");

      const outcome = getMe().catch((caught: unknown) => caught);
      await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS - 1);
      expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(1);

      expect(await outcome).toBeInstanceOf(NetworkError);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reports an unreachable server as a NetworkError", async () => {
    fakeServer(() => Promise.reject(new TypeError("Failed to fetch")));
    setRole("customer");

    await expect(getMe()).rejects.toBeInstanceOf(NetworkError);
  });
});

describe("1.5 API client", () => {
  it("sends an upload as the PDF itself, with the role header", async () => {
    const server = fakeServer();
    setRole("customer");
    const file = new Blob(["%PDF-1.7"]);

    await expect(uploadDocument(file, KEY)).resolves.toEqual(UPLOADED);

    expect(server.calls).toEqual([
      {
        path: "/api/cases",
        method: "POST",
        role: "customer",
        contentType: "application/pdf",
        body: file,
        idempotencyKey: KEY,
      },
    ]);
  });

  it("turns a refused upload into an ApiError with the server's code", async () => {
    fakeServer();
    setRole("underwriter");

    const error = await uploadDocument(new Blob(["%PDF-1.7"]), KEY).catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 403, code: "role_not_allowed" });
  });

  it.each([
    ["no case id", { document_id: "d" }],
    ["no document id", { case_id: "c" }],
    ["an empty case id", { case_id: "", document_id: "d" }],
    ["a list", []],
  ])(
    "reports a 201 with %s as an ApiError without a code",
    async (_n, body) => {
      fakeServer(() => json(201, body));
      setRole("customer");

      const error = await uploadDocument(new Blob(["%PDF-1.7"]), KEY).catch(
        (caught: unknown) => caught,
      );

      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ code: null, traceId: null });
    },
  );

  it("waits longer for an upload than the server's own deadlines", () => {
    // intake 90 s < web 120 s < browser: the browser outlasts both.
    expect(UPLOAD_TIMEOUT_MS).toBe(150_000);
    expect(UPLOAD_TIMEOUT_MS).toBeGreaterThan(120_000);
  });

  it("gives an upload longer than other calls before giving up", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi.fn(
        (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            );
          }),
      );
      vi.stubGlobal("fetch", fetchMock);
      setRole("customer");

      const outcome = uploadDocument(new Blob(["%PDF-1.7"]), KEY).catch(
        (caught: unknown) => caught,
      );
      await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS);
      expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(UPLOAD_TIMEOUT_MS - REQUEST_TIMEOUT_MS);

      expect(await outcome).toBeInstanceOf(NetworkError);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("1.6 API client", () => {
  it("names the idempotency header as the server reads it", () => {
    expect(IDEMPOTENCY_KEY_HEADER).toBe("Idempotency-Key");
  });

  it("makes a new, well-formed idempotency key each time", () => {
    const keys = new Set(Array.from({ length: 20 }, () => newIdempotencyKey()));

    expect(keys.size).toBe(20);
    for (const key of keys) {
      // What the server accepts: 16 to 64 letters, digits, "-" or "_".
      expect(key).toMatch(/^[A-Za-z0-9_-]{16,64}$/);
    }
  });

  it("starts a case with a POST as the customer and no options", async () => {
    const server = fakeServer();
    setRole("customer");

    await expect(startCase(UPLOADED.case_id)).resolves.toEqual(
      startedCase(UPLOADED.case_id),
    );

    expect(server.calls).toEqual([
      {
        path: `/api/cases/${UPLOADED.case_id}/start`,
        method: "POST",
        role: "customer",
        contentType: "application/json",
        body: "{}",
      },
    ]);
  });

  it("reads the progress of a started case, and 404 for any other", async () => {
    const server = fakeServer();
    setRole("customer");

    const unknown = await getProgress(UPLOADED.case_id).catch(
      (caught: unknown) => caught,
    );
    expect(unknown).toMatchObject({ status: 404, code: "not_found" });

    await startCase(UPLOADED.case_id);
    await expect(getProgress(UPLOADED.case_id)).resolves.toEqual(
      caseProgress(UPLOADED.case_id),
    );
    expect(server.calls.at(-1)).toMatchObject({
      path: `/api/cases/${UPLOADED.case_id}/progress`,
      method: "GET",
      role: "customer",
    });
  });

  it("1.9 reads the pages of a progress, and refuses a page that is not one", async () => {
    let pages: unknown[] = [pageProgress(1, "extracting")];
    fakeServer((call) =>
      call.path.endsWith("/progress")
        ? json(200, { ...caseProgress(UPLOADED.case_id), pages })
        : undefined,
    );
    setRole("customer");

    await expect(getProgress(UPLOADED.case_id)).resolves.toMatchObject({
      pages: [{ page_number: 1, page_status: "extracting", error_code: null }],
    });

    const good = pageProgress(2, "classified");
    const without = (field: keyof typeof good) =>
      Object.fromEntries(Object.entries(good).filter(([key]) => key !== field));
    for (const broken of [
      null,
      "page 1",
      // One field missing or of the wrong type at a time.
      without("page_id"),
      { ...good, page_id: 7 },
      without("page_number"),
      { ...good, page_number: "2" },
      without("page_status"),
      { ...good, page_status: null },
    ]) {
      pages = [pageProgress(1, "extracting"), broken];
      await expect(getProgress(UPLOADED.case_id)).rejects.toMatchObject({
        status: 200,
        code: null,
      });
    }
  });

  it("keeps a case id from changing the path it is sent on", async () => {
    const server = fakeServer();
    setRole("customer");

    await getProgress("../me").catch(() => undefined);

    expect(server.calls[0]?.path).toBe("/api/cases/..%2Fme/progress");
  });

  it("makes a key without crypto.randomUUID, from random bytes", () => {
    let filled = 0;
    vi.stubGlobal("crypto", {
      getRandomValues: (bytes: Uint8Array) => {
        filled += 1;
        bytes.forEach((_, index) => {
          bytes[index] = (index * 37 + filled * 11) % 256;
        });
        return bytes;
      },
    });

    const first = newIdempotencyKey();
    const second = newIdempotencyKey();

    expect(first).toMatch(/^[0-9a-f]{32}$/);
    expect(second).toMatch(/^[0-9a-f]{32}$/);
    expect(second).not.toBe(first);
    expect(filled).toBe(2);
  });

  it.each([
    ["no pages", { case_id: UPLOADED.case_id, case_status: "running" }],
    [
      "pages that are not a list",
      { ...caseProgress(UPLOADED.case_id), pages: "none" },
    ],
    [
      "another case's progress",
      caseProgress("019a0000-0000-7000-8000-00000000000f"),
    ],
    ["no case id", { case_status: "running", pages: [] }],
  ])(
    "reports a progress with %s as an ApiError without a code",
    async (_n, body) => {
      fakeServer(() => json(200, body));
      setRole("customer");

      const error = await getProgress(UPLOADED.case_id).catch(
        (caught: unknown) => caught,
      );

      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ code: null, traceId: null });
    },
  );

  it.each([
    [
      "another case's answer",
      startedCase("019a0000-0000-7000-8000-00000000000f"),
    ],
    ["no case id", { case_status: "running" }],
  ])(
    "reports a start answered with %s as an ApiError without a code",
    async (_n, body) => {
      fakeServer(() => json(200, body));
      setRole("customer");

      const error = await startCase(UPLOADED.case_id).catch(
        (caught: unknown) => caught,
      );

      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ code: null, traceId: null });
    },
  );

  it.each([
    ["no status", { case_id: UPLOADED.case_id }],
    ["an unknown status", startedCase(UPLOADED.case_id, "archived")],
    ["a status that is a prototype member", startedCase("c", "toString")],
    ["a list", []],
  ])(
    "reports a start or a progress with %s as an ApiError without a code",
    async (_name, body) => {
      fakeServer(() => json(200, body));
      setRole("customer");

      for (const call of [startCase, getProgress]) {
        const error = await call(UPLOADED.case_id).catch(
          (caught: unknown) => caught,
        );

        expect(error).toBeInstanceOf(ApiError);
        expect(error).toMatchObject({ code: null, traceId: null });
      }
    },
  );

  it("turns a refused start into an ApiError with the server's code", async () => {
    fakeServer();
    setRole("underwriter");

    const error = await startCase(UPLOADED.case_id).catch(
      (caught: unknown) => caught,
    );

    expect(error).toMatchObject({ status: 403, code: "role_not_allowed" });
  });
});

describe("1.10 API client", () => {
  const PAGE = pageProgress(2, "awaiting_customer").page_id;

  it("sends a decision as a POST with the role header and the decision only", async () => {
    const server = fakeServer();
    setRole("customer");

    await expect(decidePage(UPLOADED.case_id, PAGE, "keep")).resolves.toEqual(
      decisionRecorded(UPLOADED.case_id, PAGE, "keep", "customer"),
    );

    expect(server.calls).toEqual([
      {
        path: `/api/cases/${UPLOADED.case_id}/pages/${PAGE}/decisions`,
        method: "POST",
        role: "customer",
        contentType: "application/json",
        // No actor: the server takes it from the role header.
        body: JSON.stringify({ decision: "keep" }),
      },
    ]);
  });

  it("turns a refused decision into an ApiError with the server's code", async () => {
    fakeServer((call) =>
      call.path.endsWith("/decisions")
        ? json(
            409,
            errorBody(
              "not_awaiting_decision",
              "Not waiting for this decision.",
            ),
          )
        : undefined,
    );
    setRole("customer");

    await expect(
      decidePage(UPLOADED.case_id, PAGE, "discard"),
    ).rejects.toMatchObject({ status: 409, code: "not_awaiting_decision" });
  });

  it("refuses an answer that is not the decision that was sent", async () => {
    let answer: unknown = decisionRecorded(
      UPLOADED.case_id,
      PAGE,
      "discard",
      "customer",
    );
    fakeServer((call) =>
      call.path.endsWith("/decisions") ? json(200, answer) : undefined,
    );
    setRole("customer");

    // Another decision, and another page.
    await expect(
      decidePage(UPLOADED.case_id, PAGE, "keep"),
    ).rejects.toMatchObject({ status: 200, code: null });
    answer = decisionRecorded(UPLOADED.case_id, UPLOADED.case_id, "keep", null);
    await expect(
      decidePage(UPLOADED.case_id, PAGE, "keep"),
    ).rejects.toBeInstanceOf(ApiError);
    // No decision at all: null, a list, a text.
    for (const nothing of [null, [], "keep", {}]) {
      answer = nothing;
      await expect(
        decidePage(UPLOADED.case_id, PAGE, "keep"),
      ).rejects.toMatchObject({ name: "ApiError", status: 200, code: null });
    }
  });

  it("keeps a page id from changing the path a decision is sent on", async () => {
    const server = fakeServer();
    setRole("customer");

    await expect(
      decidePage(UPLOADED.case_id, "../../me?x=", "keep"),
    ).rejects.toBeInstanceOf(ApiError);

    expect(server.calls[0]?.path).toBe(
      `/api/cases/${UPLOADED.case_id}/pages/..%2F..%2Fme%3Fx%3D/decisions`,
    );
  });

  it("reads the classifications of a case, with the confidence as the server's number", async () => {
    let classifications: unknown[] = [
      classification(UPLOADED.case_id, 2, "other", 0.96),
    ];
    const server = fakeServer((call) =>
      call.path.endsWith("/classifications")
        ? json(200, { case_id: UPLOADED.case_id, classifications })
        : undefined,
    );
    setRole("customer");

    await expect(getClassifications(UPLOADED.case_id)).resolves.toMatchObject({
      classifications: [
        { page_id: PAGE, page_type: "other", confidence: 0.96 },
      ],
    });
    expect(server.calls[0]).toMatchObject({
      path: `/api/cases/${UPLOADED.case_id}/classifications`,
      method: "GET",
      role: "customer",
    });

    const good = classification(UPLOADED.case_id, 1, "invoice", 1);
    for (const broken of [
      null,
      "a page",
      { ...good, page_id: 7 },
      { ...good, page_type: null },
      { ...good, confidence: "0.96" },
      { ...good, confidence: null },
      // A confidence is a number from 0 to 1.
      { ...good, confidence: 1.01 },
      { ...good, confidence: -0.1 },
      { ...good, confidence: Number.NaN },
    ]) {
      classifications = [good, broken];
      await expect(getClassifications(UPLOADED.case_id)).rejects.toMatchObject({
        status: 200,
        code: null,
      });
    }
  });

  it("refuses classifications that are about another case, or are no list", async () => {
    let answer: unknown = { case_id: PAGE, classifications: [] };
    fakeServer((call) =>
      call.path.endsWith("/classifications") ? json(200, answer) : undefined,
    );
    setRole("customer");

    await expect(getClassifications(UPLOADED.case_id)).rejects.toMatchObject({
      status: 200,
      code: null,
    });
    answer = { case_id: UPLOADED.case_id, classifications: "none" };
    await expect(getClassifications(UPLOADED.case_id)).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});

describe("1.11 API client", () => {
  const CASE = UPLOADED.case_id;

  it("reads the triage queue as the underwriter, with the role header", async () => {
    const pages = [
      triagePage(CASE, 2, { pageType: "invoice", confidence: 0.4 }, "customer"),
      triagePage(CASE, 3, null),
    ];
    const server = fakeServer((call) =>
      call.path === "/api/triage"
        ? json(200, { pages, has_more: true })
        : undefined,
    );
    setRole("underwriter");

    await expect(getTriageQueue()).resolves.toEqual({ pages, has_more: true });

    expect(server.calls).toEqual([
      {
        path: "/api/triage",
        method: "GET",
        role: "underwriter",
        contentType: null,
        body: null,
      },
    ]);
  });

  it("turns the refusal of the customer into an ApiError with the server's code", async () => {
    fakeServer();
    setRole("customer");

    await expect(getTriageQueue()).rejects.toMatchObject({
      status: 403,
      code: "role_not_allowed",
    });
  });

  it("refuses an answer that is not a queue, or lists half a reading", async () => {
    const whole = triagePage(CASE, 1);
    const answers: unknown[] = [
      { pages: [whole] },
      { pages: "none", has_more: false },
      { pages: [{ ...whole, page_id: "" }], has_more: false },
      { pages: [{ ...whole, thumbnail_path: null }], has_more: false },
      { pages: [{ ...whole, confidence: null }], has_more: false },
      { pages: [{ ...whole, confidence: 96 }], has_more: false },
      { pages: [{ ...whole, reason: null }], has_more: false },
      { pages: [null], has_more: false },
    ];
    setRole("underwriter");

    for (const answer of answers) {
      fakeServer(() => json(200, answer));
      await expect(getTriageQueue()).rejects.toBeInstanceOf(ApiError);
    }
  });

  it("reads a thumbnail from the address the server gave, with the role header", async () => {
    const address = triagePage(CASE, 1).thumbnail_path;
    const server = fakeServer();
    setRole("underwriter");

    const picture = await getThumbnail(address);

    expect(picture.size).toBe(8);
    expect(server.calls).toEqual([
      {
        path: address,
        method: "GET",
        role: "underwriter",
        contentType: null,
        body: null,
      },
    ]);
  });

  it("reads no address that is not a thumbnail of this server", async () => {
    const server = fakeServer();
    setRole("underwriter");

    for (const address of [
      "https://example.invalid/api/pages/x/thumbnail",
      "//example.invalid/api/pages/x/thumbnail",
      "/api/cases",
      "pages/x/thumbnail",
      // Under `/api/pages/`, but not a thumbnail of a page id, and nothing more.
      "/api/pages/x/thumbnail",
      "/api/pages/../me",
      `/api/pages/${CASE}/text`,
      `/api/pages/${CASE}/thumbnail?size=large`,
      `/api/pages/${CASE}/thumbnail/more`,
      `/api/pages/${CASE}/../../me#/thumbnail`,
      `/api/pages/${CASE.toUpperCase()}/thumbnail`,
    ]) {
      await expect(getThumbnail(address)).rejects.toBeInstanceOf(ApiError);
    }
    expect(server.calls).toEqual([]);
  });

  it("refuses a thumbnail that is no picture, and reports the server's error", async () => {
    const address = triagePage(CASE, 1).thumbnail_path;
    setRole("underwriter");

    fakeServer(() => json(200, { page_id: "x" }));
    await expect(getThumbnail(address)).rejects.toMatchObject({ code: null });

    fakeServer(
      () =>
        new Response(new Uint8Array(), {
          status: 200,
          headers: { "Content-Type": "image/png" },
        }),
    );
    await expect(getThumbnail(address)).rejects.toBeInstanceOf(ApiError);

    fakeServer(() =>
      json(404, errorBody("not_found", "That page could not be found.")),
    );
    await expect(getThumbnail(address)).rejects.toMatchObject({
      status: 404,
      code: "not_found",
    });

    fakeServer(() => thumbnail());
    await expect(getThumbnail(address)).resolves.toBeDefined();
  });
});

describe("1.12 API client", () => {
  const AUDITED = "019a0000-0000-7000-8000-000000030000";
  const OTHER = "019a0000-0000-7000-8000-000000040000";

  /** One event of a trail, whole, as `workflow` answers it. */
  function auditEvent(changes: Record<string, unknown> = {}) {
    return {
      actor_kind: "ai",
      actor: "classification:chat-main",
      action: "stage.failed",
      occurred_at: "2026-10-07T09:00:00Z",
      case_id: AUDITED,
      page_id: "019a0000-0000-7000-8000-000000030001",
      ref: "019a0000-0000-7000-8000-000000030002",
      detail: null,
      trace_id: "0af7651916cd43dd8448eb211c80319c",
      eval_run_id: null,
      error_code: "model_unavailable",
      ...changes,
    };
  }

  function trail(changes: Record<string, unknown> = {}) {
    return {
      case_id: AUDITED,
      events: [auditEvent()],
      has_more: false,
      ...changes,
    };
  }

  function without(record: Record<string, unknown>, field: string) {
    return Object.fromEntries(
      Object.entries(record).filter(([name]) => name !== field),
    );
  }

  it("reads a whole audit trail as the underwriter, with the role header", async () => {
    const answered = trail({
      events: [
        auditEvent(),
        auditEvent({
          action: "document.redacted",
          actor: "intake:azure-ai-language",
          page_id: null,
          detail: { Person: 2 },
          error_code: null,
        }),
      ],
      has_more: true,
    });
    const server = fakeServer(() => json(200, answered));
    setRole("underwriter");

    await expect(getAuditTrail(AUDITED)).resolves.toEqual(answered);

    expect(server.calls).toMatchObject([
      {
        path: `/api/cases/${AUDITED}/audit`,
        method: "GET",
        role: "underwriter",
      },
    ]);
  });

  it("takes an event that leaves its error code out, as one whose code is null", async () => {
    const answered = trail({
      events: [
        without(auditEvent({ action: "page.classified" }), "error_code"),
      ],
    });
    fakeServer(() => json(200, answered));
    setRole("underwriter");

    await expect(getAuditTrail(AUDITED)).resolves.toEqual(answered);
  });

  it.each([
    ["no has_more", without(trail(), "has_more")],
    ["a has_more that is no yes or no", trail({ has_more: "no" })],
    ["another case's case_id", trail({ case_id: OTHER })],
    ["events that are no list", trail({ events: {} })],
    ["an event that is no object", trail({ events: ["page.kept"] })],
    [
      "an event without an action",
      trail({ events: [auditEvent({ action: "" })] }),
    ],
    [
      "an event without an actor",
      trail({ events: [auditEvent({ actor: 7 })] }),
    ],
    [
      "an event without an actor kind",
      trail({ events: [without(auditEvent(), "actor_kind")] }),
    ],
    [
      "an event without a time",
      trail({ events: [auditEvent({ occurred_at: null })] }),
    ],
    [
      "an event without a ref",
      trail({ events: [without(auditEvent(), "ref")] }),
    ],
    [
      "an event whose page is neither an id nor null",
      trail({ events: [auditEvent({ page_id: 3 })] }),
    ],
    [
      "an event whose detail is text",
      trail({ events: [auditEvent({ detail: "Person 2" })] }),
    ],
    [
      "an event whose error code is no text",
      trail({ events: [auditEvent({ error_code: 502 })] }),
    ],
    [
      "one wrong event among right ones",
      trail({
        events: [auditEvent(), auditEvent({ actor: "" }), auditEvent()],
      }),
    ],
  ])("refuses an answer with %s", async (_what, answered) => {
    fakeServer(() => json(200, answered));
    setRole("underwriter");

    const refused = getAuditTrail(AUDITED);

    await expect(refused).rejects.toBeInstanceOf(ApiError);
    await expect(refused).rejects.toMatchObject({ status: 200, code: null });
  });

  it("turns the refusal of the customer, and an unknown case, into an ApiError with the server's code", async () => {
    setRole("customer");
    fakeServer();
    await expect(getAuditTrail(AUDITED)).rejects.toMatchObject({
      status: 403,
      code: "role_not_allowed",
    });

    setRole("underwriter");
    await expect(getAuditTrail(AUDITED)).rejects.toMatchObject({
      status: 404,
      code: "not_found",
    });
  });
});

describe("1.13 API client", () => {
  const CASE = UPLOADED.case_id;

  it("reads the case list as the underwriter, with the role header", async () => {
    const cases = [
      caseSummary(CASE, "awaiting_human", 6, 3),
      caseSummary("019a0000-0000-7000-8000-000000000009", "completed", 3, 0),
    ];
    const server = fakeServer((call) =>
      call.path === "/api/cases" && call.method === "GET"
        ? json(200, { cases, has_more: true })
        : undefined,
    );
    setRole("underwriter");

    await expect(getCaseList()).resolves.toEqual({ cases, has_more: true });

    expect(server.calls).toEqual([
      {
        path: "/api/cases",
        method: "GET",
        role: "underwriter",
        contentType: null,
        body: null,
      },
    ]);
  });

  it("turns the refusal of the customer into an ApiError with the server's code", async () => {
    fakeServer();
    setRole("customer");

    await expect(getCaseList()).rejects.toMatchObject({
      status: 403,
      code: "role_not_allowed",
    });
  });

  it("refuses an answer that is not a case list, or lists half a case", async () => {
    const whole = caseSummary(CASE, "running", 2, 1);
    const answers: unknown[] = [
      { cases: [whole] },
      { cases: "none", has_more: false },
      { cases: [whole], has_more: "no" },
      { cases: [{ ...whole, case_id: "" }], has_more: false },
      { cases: [{ ...whole, case_status: null }], has_more: false },
      { cases: [{ ...whole, started_at: 1 }], has_more: false },
      { cases: [{ ...whole, page_count: "2" }], has_more: false },
      { cases: [{ ...whole, page_count: -1 }], has_more: false },
      { cases: [{ ...whole, waiting_page_count: 0.5 }], has_more: false },
      { cases: [null], has_more: false },
      // An id that is no case id would go into the address of a trail.
      { cases: [{ ...whole, case_id: "../../triage" }], has_more: false },
      { cases: [{ ...whole, case_id: CASE.toUpperCase() }], has_more: false },
      // More pages waiting than the case has.
      { cases: [{ ...whole, waiting_page_count: 3 }], has_more: false },
      // No object at all.
      null,
      [],
      "cases",
    ];
    setRole("underwriter");

    for (const answer of answers) {
      fakeServer(() => json(200, answer));
      await expect(getCaseList()).rejects.toBeInstanceOf(ApiError);
    }
  });
});
