import { afterEach, describe, expect, it, vi } from "vitest";
import { setRole } from "../role/roleStore";
import {
  caseProgress,
  errorBody,
  fakeServer,
  json,
  pageProgress,
  startedCase,
  UPLOADED,
} from "../test/server";
import {
  ApiError,
  getMe,
  getProgress,
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
