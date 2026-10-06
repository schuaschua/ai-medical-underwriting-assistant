import { afterEach, describe, expect, it, vi } from "vitest";
import { setRole } from "../role/roleStore";
import { errorBody, fakeServer, json } from "../test/server";
import {
  ApiError,
  getMe,
  NetworkError,
  REQUEST_TIMEOUT_MS,
  UPLOAD_TIMEOUT_MS,
  uploadDocument,
} from "./client";

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

    await expect(uploadDocument(file)).resolves.toMatchObject({
      status: "running",
    });

    expect(server.calls).toEqual([
      {
        path: "/api/cases",
        method: "POST",
        role: "customer",
        contentType: "application/pdf",
        body: file,
      },
    ]);
  });

  it("turns a refused upload into an ApiError with the server's code", async () => {
    fakeServer();
    setRole("underwriter");

    const error = await uploadDocument(new Blob(["%PDF-1.7"])).catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 403, code: "role_not_allowed" });
  });

  it.each([
    ["no case id", { document_id: "d", status: "running" }],
    ["an unknown status", { case_id: "c", document_id: "d", status: "new" }],
    ["a list", []],
  ])(
    "reports a 201 with %s as an ApiError without a code",
    async (_n, body) => {
      fakeServer(() => json(201, body));
      setRole("customer");

      const error = await uploadDocument(new Blob(["%PDF-1.7"])).catch(
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

      const outcome = uploadDocument(new Blob(["%PDF-1.7"])).catch(
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
