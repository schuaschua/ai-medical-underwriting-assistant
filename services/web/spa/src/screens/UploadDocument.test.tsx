import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import {
  backoffMs,
  PROGRESS_MAX_BACKOFF_MS,
  PROGRESS_POLL_MS,
} from "../cases/caseProgress";
import { UPLOAD_KEY_STORAGE_KEY } from "../cases/uploadKey";
import { CASES_STORAGE_KEY } from "../cases/sessionCases";
import { ROLE_STORAGE_KEY } from "../role/roleStore";
import { MAX_UPLOAD_MB, strings } from "../strings";
import {
  caseProgress,
  errorBody,
  fakeServer,
  json,
  pageProgress,
  startedCase,
  UPLOADED,
  type RecordedCall,
} from "../test/server";

const UPLOAD_PATH = "/customer/upload";

// A synthetic case document from story 1.4. Tests run from services/web/spa.
const CASE_PDF = readFileSync(
  resolve(process.cwd(), "../../../data/cases/case-001.pdf"),
);

function casePdf(): File {
  return new File([CASE_PDF], "case-001.pdf", { type: "application/pdf" });
}

function openUploadScreen(role: "customer" | "underwriter" = "customer") {
  window.localStorage.setItem(ROLE_STORAGE_KEY, role);
  return render(
    <MemoryRouter initialEntries={[UPLOAD_PATH]}>
      <App />
    </MemoryRouter>,
  );
}

function fileInput(): HTMLInputElement {
  return screen.getByLabelText("PDF document");
}

function uploadButton(): HTMLElement {
  return screen.getByRole("button", { name: "Upload" });
}

/** Whether a call is the upload itself, not a call about the case it made. */
function isUpload(call: RecordedCall): boolean {
  return call.path === "/api/cases" && call.method === "POST";
}

function caseRows(): string[][] {
  const table = screen.getByRole("table");
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) =>
      within(row)
        .getAllByRole("cell")
        .map((cell) => cell.textContent ?? ""),
    );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("1.5 upload screen", () => {
  it("is on the customer's navigation and starts with no cases", () => {
    fakeServer();

    openUploadScreen();

    expect(
      screen.getByRole("heading", { name: "Upload a document" }),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Upload a document" }),
    ).toHaveAttribute("href", UPLOAD_PATH);
    expect(screen.getByText("No documents uploaded yet.")).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    // Nothing to send yet.
    expect(uploadButton()).toBeDisabled();
    expect(fileInput()).toHaveAttribute("accept", "application/pdf,.pdf");
  });

  it("uploads the chosen PDF as the customer and lists the new case as running", async () => {
    const server = fakeServer();
    const user = userEvent.setup();
    openUploadScreen();
    const file = casePdf();

    await user.upload(fileInput(), file);
    await user.click(uploadButton());

    expect(
      await screen.findByText(
        "Your document was uploaded and its case has started.",
      ),
    ).toBeVisible();
    expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]);
    // One upload: the file itself as the body, declared as a PDF, as the customer.
    const [upload] = server.calls;
    expect(server.calls.filter(isUpload)).toHaveLength(1);
    expect(upload).toMatchObject({
      path: "/api/cases",
      method: "POST",
      role: "customer",
      contentType: "application/pdf",
    });
    expect(upload?.body).toBe(file);
    expect(file.size).toBe(CASE_PDF.length);
    // The picker is emptied, ready for the next document.
    expect(fileInput().files).toHaveLength(0);
    expect(uploadButton()).toBeDisabled();
  });

  it("shows progress while the upload is under way", async () => {
    let answer: (response: Response) => void = () => {};
    const server = fakeServer((call) =>
      isUpload(call)
        ? new Promise<Response>((resolveAnswer) => {
            answer = resolveAnswer;
          })
        : undefined,
    );
    const user = userEvent.setup();
    openUploadScreen();

    await user.upload(fileInput(), casePdf());
    await user.click(uploadButton());

    expect(
      await screen.findByRole("progressbar", { name: "Uploading…" }),
    ).toBeVisible();
    expect(uploadButton()).toBeDisabled();
    expect(fileInput()).toBeDisabled();
    // A second submit while one is under way sends nothing more.
    expect(server.calls).toHaveLength(1);

    answer(json(201, UPLOADED));

    await waitFor(() =>
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]),
    );
  });

  it("lists every case uploaded in this session, newest first", async () => {
    const second = {
      ...UPLOADED,
      case_id: "019a0000-0000-7000-8000-000000000003",
    };
    let uploads = 0;
    fakeServer((call) =>
      isUpload(call)
        ? json(201, uploads++ === 0 ? UPLOADED : second)
        : undefined,
    );
    const user = userEvent.setup();
    const firstView = openUploadScreen();

    await user.upload(fileInput(), casePdf());
    await user.click(uploadButton());
    await screen.findByRole("table");
    await user.upload(fileInput(), casePdf());
    await user.click(uploadButton());

    await waitFor(() =>
      expect(caseRows()).toEqual([
        [second.case_id, "Running"],
        [UPLOADED.case_id, "Running"],
      ]),
    );

    // A reload in the same browser session still shows them, and reads
    // their status from the server again.
    firstView.unmount();
    openUploadScreen();
    expect(caseRows()).toHaveLength(2);
    await waitFor(() =>
      expect(caseRows().map((row) => row[1])).toEqual(["Running", "Running"]),
    );
  });

  it.each([
    [
      "a file over 10 MB",
      413,
      "file_too_large",
      "The file is larger than 10 MB. Choose a smaller PDF.",
    ],
    [
      "a file that is not a PDF",
      415,
      "unsupported_file_type",
      "Only PDF files can be uploaded.",
    ],
    [
      "an empty file",
      422,
      "validation_failed",
      "That could not be accepted. Check what you sent and try again.",
    ],
    [
      "a method the server does not take",
      405,
      "method_not_allowed",
      "That action is not available here.",
    ],
    [
      "a failure behind the server",
      502,
      "upstream_unavailable",
      "The service is not available right now. Please try again.",
    ],
  ])(
    "shows a plain message for %s and lists no case",
    async (_name, status, code, message) => {
      const traceId = "0af7651916cd43dd8448eb211c80319c";
      fakeServer(() =>
        json(status, errorBody(code, "<b>server wording</b>", traceId)),
      );
      // The picker's own filter is a convenience; here it is stepped around.
      const user = userEvent.setup({ applyAccept: false });
      openUploadScreen();

      await user.upload(
        fileInput(),
        new File(["not a pdf"], "notes.pdf", { type: "text/plain" }),
      );
      await user.click(uploadButton());

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(message);
      expect(alert).toHaveTextContent(`Reference: ${traceId}`);
      expect(alert).not.toHaveTextContent("server wording");
      expect(alert.querySelector("b")).toBeNull();
      expect(screen.getByText("No documents uploaded yet.")).toBeVisible();
      // The file is still chosen, so the upload can be tried again.
      expect(uploadButton()).toBeEnabled();
    },
  );

  it("leaves the decision to the server: any chosen file is sent as it is", async () => {
    const server = fakeServer(() =>
      json(415, errorBody("unsupported_file_type", "Only PDF files.")),
    );
    const user = userEvent.setup({ applyAccept: false });
    openUploadScreen();
    const text = new File(["plain text"], "notes.txt", { type: "text/plain" });

    await user.upload(fileInput(), text);
    await user.click(uploadButton());

    await screen.findByRole("alert");
    expect(server.calls[0]?.body).toBe(text);
  });

  it("says so when the server cannot be reached, and a retry can succeed", async () => {
    let reachable = false;
    fakeServer(() =>
      reachable ? undefined : Promise.reject(new TypeError("Failed to fetch")),
    );
    const user = userEvent.setup();
    openUploadScreen();
    await user.upload(fileInput(), casePdf());

    await user.click(uploadButton());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The server could not be reached. Please try again.",
    );

    reachable = true;
    await user.click(uploadButton());

    await screen.findByText(strings.upload.uploaded);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each([
    ["no case id", { document_id: UPLOADED.document_id }],
    ["an empty case id", { ...UPLOADED, case_id: "" }],
    ["an empty document id", { ...UPLOADED, document_id: "" }],
    ["no document id", { case_id: UPLOADED.case_id }],
  ])(
    "shows the general error for a 201 with %s, and lists nothing",
    async (_name, body) => {
      fakeServer(() => json(201, body));
      const user = userEvent.setup();
      openUploadScreen();

      await user.upload(fileInput(), casePdf());
      await user.click(uploadButton());

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Something went wrong. Please try again.",
      );
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(window.sessionStorage.getItem(CASES_STORAGE_KEY)).toBeNull();
    },
  );

  it("clears the success message when another file is chosen", async () => {
    fakeServer();
    const user = userEvent.setup();
    openUploadScreen();
    await user.upload(fileInput(), casePdf());
    await user.click(uploadButton());
    await screen.findByText(strings.upload.uploaded);

    await user.upload(fileInput(), casePdf());

    expect(screen.queryByText(strings.upload.uploaded)).not.toBeInTheDocument();
    // The case that was uploaded stays listed.
    expect(caseRows()).toHaveLength(1);
  });

  it("clears the error message when another file is chosen", async () => {
    fakeServer(() =>
      json(415, errorBody("unsupported_file_type", "Only PDF files.")),
    );
    const user = userEvent.setup();
    openUploadScreen();
    await user.upload(fileInput(), casePdf());
    await user.click(uploadButton());
    await screen.findByRole("alert");

    await user.upload(fileInput(), casePdf());

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(uploadButton()).toBeEnabled();
  });

  it("sends one upload when the form is submitted twice", async () => {
    let answer: (response: Response) => void = () => {};
    const server = fakeServer((call) =>
      isUpload(call)
        ? new Promise<Response>((resolveAnswer) => {
            answer = resolveAnswer;
          })
        : undefined,
    );
    const user = userEvent.setup();
    openUploadScreen();
    await user.upload(fileInput(), casePdf());
    const form = fileInput().closest("form");
    expect(form).not.toBeNull();

    // Enter pressed twice: the button is disabled, but a form can still submit.
    fireEvent.submit(form!);
    fireEvent.submit(form!);
    await screen.findByRole("progressbar");
    fireEvent.submit(form!);

    expect(server.calls).toHaveLength(1);
    answer(json(201, UPLOADED));
    await screen.findByRole("table");
    expect(caseRows()).toHaveLength(1);
  });

  it("sends nothing when the form is submitted with no file", () => {
    const server = fakeServer();
    openUploadScreen();

    fireEvent.submit(fileInput().closest("form")!);

    expect(server.calls).toHaveLength(0);
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("still lists the case when the browser refuses to store it", async () => {
    fakeServer();
    const plainSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (
      this: Storage,
      key: string,
      value: string,
    ) {
      if (key === CASES_STORAGE_KEY) {
        throw new DOMException("quota", "QuotaExceededError");
      }
      plainSetItem.call(this, key, value);
    });
    const user = userEvent.setup();
    openUploadScreen();

    await user.upload(fileInput(), casePdf());
    await user.click(uploadButton());

    await screen.findByText(strings.upload.uploaded);
    expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]);
    expect(window.sessionStorage.getItem(CASES_STORAGE_KEY)).toBeNull();
  });

  it("still lists the case when the browser refuses to read storage", async () => {
    fakeServer();
    const plainGetItem = Storage.prototype.getItem;
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (key === CASES_STORAGE_KEY) {
        throw new DOMException("blocked", "SecurityError");
      }
      return plainGetItem.call(this, key);
    });
    const user = userEvent.setup();
    openUploadScreen();
    expect(screen.getByText("No documents uploaded yet.")).toBeVisible();

    await user.upload(fileInput(), casePdf());
    await user.click(uploadButton());

    await screen.findByText(strings.upload.uploaded);
    expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]);
  });

  it("states the size limit from one constant", () => {
    expect(MAX_UPLOAD_MB).toBe(10);
    expect(strings.upload.intro).toContain(`up to ${MAX_UPLOAD_MB} MB`);
    expect(strings.errors.byCode.file_too_large).toContain(
      `larger than ${MAX_UPLOAD_MB} MB`,
    );
    expect(strings.errors.byCode.payload_too_large).toBe(
      strings.errors.byCode.file_too_large,
    );
  });

  it("is not a screen of the underwriter's", async () => {
    const server = fakeServer();

    openUploadScreen("underwriter");

    expect(
      await screen.findByRole("heading", { name: "Page not found" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("link", { name: "Upload a document" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("PDF document")).not.toBeInTheDocument();
    expect(server.calls.some((call) => call.path === "/api/cases")).toBe(false);
  });

  it("ignores stored cases that are not in the contract's shape", () => {
    fakeServer();
    window.sessionStorage.setItem(
      CASES_STORAGE_KEY,
      JSON.stringify([
        UPLOADED,
        { case_id: 7, document_id: "x" },
        { case_id: "other" },
        { ...UPLOADED, case_id: "" },
        null,
      ]),
    );

    openUploadScreen();

    expect(caseRows().map((row) => row[0])).toEqual([UPLOADED.case_id]);
  });

  it("shows no cases when the stored list is not JSON", () => {
    fakeServer();
    window.sessionStorage.setItem(CASES_STORAGE_KEY, "{not json");

    openUploadScreen();

    expect(screen.getByText("No documents uploaded yet.")).toBeVisible();
  });
});

describe("1.6 upload, start and progress", () => {
  const START_PATH = `/api/cases/${UPLOADED.case_id}/start`;
  const PROGRESS_PATH = `/api/cases/${UPLOADED.case_id}/progress`;
  const NOT_STARTED =
    "Your document was received, but its case has not started. Use “Start it again” in the list below.";

  function calls(server: { calls: RecordedCall[] }, path: string) {
    return server.calls.filter((call) => call.path === path);
  }

  function startAgainButton(): HTMLElement {
    return screen.getByRole("button", {
      name: `Start case ${UPLOADED.case_id} again`,
    });
  }

  /** A case uploaded earlier in this browser session, as a reload finds it. */
  function storeCase() {
    window.sessionStorage.setItem(
      CASES_STORAGE_KEY,
      JSON.stringify([UPLOADED]),
    );
  }

  async function chooseAndUpload(user: ReturnType<typeof userEvent.setup>) {
    await user.upload(fileInput(), casePdf());
    await user.click(uploadButton());
  }

  it("sends an idempotency key with the upload, then starts the case and shows its status", async () => {
    const server = fakeServer();
    const user = userEvent.setup();
    openUploadScreen();

    await chooseAndUpload(user);

    expect(
      await screen.findByText(
        "Your document was uploaded and its case has started.",
      ),
    ).toBeVisible();
    expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]);
    // The upload first, with its key; then the start of the case it made.
    const [upload, start] = server.calls;
    expect(upload?.path).toBe("/api/cases");
    expect(upload?.idempotencyKey).toMatch(/^[A-Za-z0-9_-]{16,64}$/);
    expect(start).toMatchObject({
      path: START_PATH,
      method: "POST",
      role: "customer",
      contentType: "application/json",
      body: "{}",
    });
    // The key is for the upload alone.
    expect(start?.idempotencyKey).toBeUndefined();
  });

  it("sends the same key when an upload is tried again, and a new one for the next file", async () => {
    let reachable = false;
    const server = fakeServer((call) =>
      isUpload(call) && !reachable
        ? Promise.reject(new TypeError("Failed to fetch"))
        : undefined,
    );
    const user = userEvent.setup();
    openUploadScreen();

    // The first try never gets an answer; the server may have stored the file.
    await chooseAndUpload(user);
    await screen.findByRole("alert");
    reachable = true;
    await user.click(uploadButton());
    await screen.findByText(strings.upload.uploaded);
    // Another document.
    await chooseAndUpload(user);
    await waitFor(() => expect(server.calls.filter(isUpload)).toHaveLength(3));

    const [first, retry, next] = server.calls
      .filter(isUpload)
      .map((call) => call.idempotencyKey);
    expect(first).toBeDefined();
    expect(retry).toBe(first);
    expect(next).toBeDefined();
    expect(next).not.toBe(first);
  });

  it("keeps the key for the same file and makes a new one for another file", async () => {
    const server = fakeServer((call) =>
      isUpload(call)
        ? json(415, errorBody("unsupported_file_type", "Only PDF files."))
        : undefined,
    );
    const user = userEvent.setup();
    openUploadScreen();
    const file = casePdf();
    const other = new File([CASE_PDF], "another-case.pdf", {
      type: "application/pdf",
    });

    // The same file, chosen again after a failure: it is the same upload.
    await user.upload(fileInput(), file);
    await user.click(uploadButton());
    await screen.findByRole("alert");
    await user.upload(fileInput(), file);
    await user.click(uploadButton());
    await waitFor(() => expect(server.calls.filter(isUpload)).toHaveLength(2));
    // Another file is another upload.
    await user.upload(fileInput(), other);
    await user.click(uploadButton());
    await waitFor(() => expect(server.calls.filter(isUpload)).toHaveLength(3));

    const [first, again, third] = server.calls.map(
      (call) => call.idempotencyKey,
    );
    expect(again).toBe(first);
    expect(third).not.toBe(first);
  });

  it("retries with the same key after a reload in the middle of an upload", async () => {
    let reachable = false;
    const server = fakeServer((call) =>
      isUpload(call) && !reachable
        ? Promise.reject(new TypeError("Failed to fetch"))
        : undefined,
    );
    const user = userEvent.setup();
    const file = casePdf();
    const firstView = openUploadScreen();
    await user.upload(fileInput(), file);
    await user.click(uploadButton());
    await screen.findByRole("alert");
    // The key is kept against the file, not in the page's memory.
    const stored = JSON.parse(
      window.sessionStorage.getItem(UPLOAD_KEY_STORAGE_KEY) ?? "null",
    ) as Record<string, unknown>;
    expect(stored).toMatchObject({
      name: file.name,
      size: file.size,
      last_modified: file.lastModified,
    });

    // The page is reloaded and the same file is chosen again.
    firstView.unmount();
    reachable = true;
    openUploadScreen();
    await user.upload(fileInput(), file);
    await user.click(uploadButton());
    await screen.findByText(strings.upload.uploaded);

    const [first, afterReload] = server.calls
      .filter(isUpload)
      .map((call) => call.idempotencyKey);
    expect(afterReload).toBe(first);
    expect(afterReload).toBe(stored.key);
    // Done: the key is not kept for a later upload.
    expect(window.sessionStorage.getItem(UPLOAD_KEY_STORAGE_KEY)).toBeNull();
  });

  it("still sends one key per file when the browser refuses to store it", async () => {
    let reachable = false;
    const server = fakeServer((call) =>
      isUpload(call) && !reachable
        ? Promise.reject(new TypeError("Failed to fetch"))
        : undefined,
    );
    const plainSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (
      this: Storage,
      key: string,
      value: string,
    ) {
      if (key === UPLOAD_KEY_STORAGE_KEY) {
        throw new DOMException("quota", "QuotaExceededError");
      }
      plainSetItem.call(this, key, value);
    });
    const user = userEvent.setup();
    openUploadScreen();

    await chooseAndUpload(user);
    await screen.findByRole("alert");
    reachable = true;
    await user.click(uploadButton());
    await screen.findByText(strings.upload.uploaded);

    const [first, retry] = server.calls
      .filter(isUpload)
      .map((call) => call.idempotencyKey);
    expect(first).toBeDefined();
    expect(retry).toBe(first);
  });

  it("ignores a stored key that is not in the expected shape", async () => {
    const server = fakeServer();
    window.sessionStorage.setItem(UPLOAD_KEY_STORAGE_KEY, "{not json");
    const user = userEvent.setup();
    openUploadScreen();

    await chooseAndUpload(user);
    await screen.findByText(strings.upload.uploaded);

    expect(server.calls[0]?.idempotencyKey).toMatch(/^[A-Za-z0-9_-]{16,64}$/);
  });

  it("says the document was received while the start is still under way", async () => {
    let answer: (response: Response) => void = () => {};
    fakeServer((call) =>
      call.path === START_PATH
        ? new Promise<Response>((resolveAnswer) => {
            answer = resolveAnswer;
          })
        : undefined,
    );
    const user = userEvent.setup();
    openUploadScreen();

    await chooseAndUpload(user);

    // Uploaded, not yet started: neither "has started" nor "has not started".
    const banner = await screen.findByText(
      "Your document was received. Starting its case…",
    );
    expect(banner).toHaveAttribute("role", "status");
    expect(caseRows()).toEqual([[UPLOADED.case_id, "Starting…"]]);
    expect(screen.queryByText(strings.upload.uploaded)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    answer(json(200, startedCase(UPLOADED.case_id)));

    expect(await screen.findByText(strings.upload.uploaded)).toBeVisible();
    expect(
      screen.queryByText("Your document was received. Starting its case…"),
    ).not.toBeInTheDocument();
  });

  it.each([
    [
      "the start is refused behind the server",
      () =>
        json(502, errorBody("upstream_unavailable", "<b>server wording</b>")),
      "The service is not available right now. Please try again.",
    ],
    [
      "the server cannot be reached for the start",
      () => Promise.reject(new TypeError("Failed to fetch")),
      "The server could not be reached. Please try again.",
    ],
    [
      "the start is answered with something else",
      () => json(200, { case_id: UPLOADED.case_id }),
      "Something went wrong. Please try again.",
    ],
  ])(
    "says the case was received but not started when %s, and can try again",
    async (_name, failure, reason) => {
      let failing = true;
      const server = fakeServer((call) =>
        call.path === START_PATH && failing ? failure() : undefined,
      );
      const user = userEvent.setup();
      openUploadScreen();

      await chooseAndUpload(user);

      // The document is safe; only its case is not running yet.
      expect(await screen.findByText(NOT_STARTED)).toBeVisible();
      const [row] = caseRows();
      expect(row?.[0]).toBe(UPLOADED.case_id);
      expect(row?.[1]).toContain("Received, not started");
      expect(row?.[1]).toContain(reason);
      expect(row?.[1]).not.toContain("server wording");
      expect(calls(server, START_PATH)).toHaveLength(1);
      // The file was sent once: a retry starts the case, it does not upload again.
      failing = false;
      await user.click(startAgainButton());

      await waitFor(() =>
        expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]),
      );
      expect(screen.getByText(strings.upload.uploaded)).toBeVisible();
      expect(screen.queryByText(NOT_STARTED)).not.toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(calls(server, START_PATH)).toHaveLength(2);
      expect(server.calls.filter(isUpload)).toHaveLength(1);
    },
  );

  it("keeps offering the retry while the start keeps failing", async () => {
    const server = fakeServer((call) =>
      call.path === START_PATH
        ? json(502, errorBody("upstream_unavailable", "Down."))
        : undefined,
    );
    const user = userEvent.setup();
    openUploadScreen();
    await chooseAndUpload(user);
    await screen.findByText(NOT_STARTED);

    await user.click(startAgainButton());

    await waitFor(() => expect(calls(server, START_PATH)).toHaveLength(2));
    expect(await screen.findByText(NOT_STARTED)).toBeVisible();
    expect(startAgainButton()).toBeEnabled();
  });

  it("shows a case found in the session as received, not started, when the server does not know it", async () => {
    const server = fakeServer();
    storeCase();
    const user = userEvent.setup();

    openUploadScreen();

    // Read from the server at once, without waiting for the first interval.
    await waitFor(() =>
      expect(caseRows()[0]?.[1]).toContain("Received, not started"),
    );
    expect(calls(server, PROGRESS_PATH)[0]).toMatchObject({
      method: "GET",
      role: "customer",
    });

    await user.click(startAgainButton());

    await waitFor(() =>
      expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]),
    );
  });

  describe("polling", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    async function nextPoll() {
      await act(() => vi.advanceTimersByTimeAsync(PROGRESS_POLL_MS));
    }

    it("reads the progress of each case again and again, and shows a changed status without a reload", async () => {
      // The fake clock also runs with real time, so `waitFor` still works.
      vi.useFakeTimers({ shouldAdvanceTime: true });
      let status = "running";
      const server = fakeServer((call) =>
        call.path === PROGRESS_PATH
          ? json(200, caseProgress(UPLOADED.case_id, status))
          : undefined,
      );
      storeCase();

      openUploadScreen();
      await waitFor(() =>
        expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]),
      );
      expect(calls(server, PROGRESS_PATH)).toHaveLength(1);

      status = "awaiting_human";
      await nextPoll();
      expect(caseRows()).toEqual([
        [UPLOADED.case_id, "Waiting for a decision"],
      ]);
      expect(calls(server, PROGRESS_PATH)).toHaveLength(2);

      status = "completed";
      await nextPoll();
      expect(caseRows()).toEqual([[UPLOADED.case_id, "Completed"]]);

      // A finished case will not change again: it is not read any more.
      await nextPoll();
      await nextPoll();
      expect(calls(server, PROGRESS_PATH)).toHaveLength(3);
    });

    it("shows a case that failed, and stops reading it", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const server = fakeServer((call) =>
        call.path === PROGRESS_PATH
          ? json(200, caseProgress(UPLOADED.case_id, "failed"))
          : undefined,
      );
      storeCase();

      openUploadScreen();

      await waitFor(() =>
        expect(caseRows()).toEqual([[UPLOADED.case_id, "Failed"]]),
      );
      await nextPoll();
      expect(calls(server, PROGRESS_PATH)).toHaveLength(1);
    });

    it("keeps the status it has when a read fails, reads less often, and catches up", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      let answer: () => Response | Promise<Response> = () =>
        json(200, caseProgress(UPLOADED.case_id, "running"));
      const server = fakeServer((call) =>
        call.path === PROGRESS_PATH ? answer() : undefined,
      );
      storeCase();
      openUploadScreen();
      await waitFor(() =>
        expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]),
      );
      const reads = () => calls(server, PROGRESS_PATH).length;
      expect(reads()).toBe(1);

      // Three kinds of failure in a row: each leaves the status as it is.
      const failures = [
        () => Promise.reject(new TypeError("Failed to fetch")),
        () => json(502, errorBody("upstream_unavailable", "Down.")),
        () => json(200, { case_status: "archived" }),
      ];
      answer = failures[0]!;
      await nextPoll();
      expect(reads()).toBe(2);
      // After one failure the next read waits two intervals, not one.
      answer = failures[1]!;
      await nextPoll();
      expect(reads()).toBe(2);
      await nextPoll();
      expect(reads()).toBe(3);
      // After two failures, four intervals.
      answer = failures[2]!;
      for (let tick = 0; tick < 3; tick += 1) {
        await nextPoll();
        expect(reads()).toBe(3);
      }
      await nextPoll();
      expect(reads()).toBe(4);
      expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();

      // The next read succeeds: the status catches up and the pace is back.
      answer = () =>
        json(200, caseProgress(UPLOADED.case_id, "awaiting_human"));
      await act(() => vi.advanceTimersByTimeAsync(backoffMs(3)));
      expect(caseRows()).toEqual([
        [UPLOADED.case_id, "Waiting for a decision"],
      ]);
      const caughtUp = reads();
      await nextPoll();
      expect(reads()).toBe(caughtUp + 1);
    });

    it("never waits longer than the longest back-off", () => {
      expect(backoffMs(1)).toBe(PROGRESS_POLL_MS * 2);
      expect(backoffMs(2)).toBe(PROGRESS_POLL_MS * 4);
      expect(backoffMs(50)).toBe(PROGRESS_MAX_BACKOFF_MS);
      expect(PROGRESS_MAX_BACKOFF_MS).toBe(30_000);
    });

    it("sends one read at a time for a case, however slow the answer", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const answers: ((response: Response) => void)[] = [];
      const server = fakeServer((call) =>
        call.path === PROGRESS_PATH
          ? new Promise<Response>((resolveAnswer) => {
              answers.push(resolveAnswer);
            })
          : undefined,
      );
      storeCase();
      openUploadScreen();
      await waitFor(() => expect(answers).toHaveLength(1));

      // The first read is still out: no second one is sent on top of it.
      await nextPoll();
      await nextPoll();
      expect(calls(server, PROGRESS_PATH)).toHaveLength(1);
      expect(caseRows()).toEqual([[UPLOADED.case_id, "Checking…"]]);

      await act(async () => {
        answers[0]!(json(200, caseProgress(UPLOADED.case_id, "running")));
        await Promise.resolve();
      });
      await waitFor(() =>
        expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]),
      );
      // Only now is the next one sent; answers cannot overtake each other.
      await nextPoll();
      expect(calls(server, PROGRESS_PATH)).toHaveLength(2);
    });

    it("shows a plain error with a retry when the first read fails, not an endless “Checking…”", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      let failing = true;
      const server = fakeServer((call) => {
        if (call.path !== PROGRESS_PATH) {
          return undefined;
        }
        return failing
          ? json(502, errorBody("upstream_unavailable", "<b>wording</b>"))
          : json(200, caseProgress(UPLOADED.case_id, "running"));
      });
      storeCase();
      const user = userEvent.setup({
        advanceTimers: vi.advanceTimersByTime.bind(vi),
      });
      openUploadScreen();

      await waitFor(() =>
        expect(caseRows()[0]?.[1]).toContain("Its status could not be read"),
      );
      expect(caseRows()[0]?.[1]).toContain(
        "The service is not available right now. Please try again.",
      );
      expect(caseRows()[0]?.[1]).not.toContain("wording");
      // It is "received, not started" only when the server says it does not know the case.
      expect(caseRows()[0]?.[1]).not.toContain("Received, not started");
      const before = calls(server, PROGRESS_PATH).length;

      // The retry reads at once, whatever the back-off.
      failing = false;
      await user.click(
        screen.getByRole("button", {
          name: `Check case ${UPLOADED.case_id} again`,
        }),
      );

      await waitFor(() =>
        expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]),
      );
      expect(calls(server, PROGRESS_PATH)).toHaveLength(before + 1);
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("gets there on its own too, when a later read succeeds", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      let failing = true;
      fakeServer((call) => {
        if (call.path !== PROGRESS_PATH) {
          return undefined;
        }
        return failing
          ? Promise.reject(new TypeError("Failed to fetch"))
          : json(200, caseProgress(UPLOADED.case_id, "completed"));
      });
      storeCase();
      openUploadScreen();
      await waitFor(() =>
        expect(caseRows()[0]?.[1]).toContain("Its status could not be read"),
      );

      failing = false;
      await act(() => vi.advanceTimersByTimeAsync(backoffMs(1)));

      expect(caseRows()).toEqual([[UPLOADED.case_id, "Completed"]]);
    });

    it("does not go on reading a case the server does not know", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const server = fakeServer();
      storeCase();
      openUploadScreen();
      await waitFor(() =>
        expect(caseRows()[0]?.[1]).toContain("Received, not started"),
      );

      // Nothing changes for it until it is started: it is not read again.
      await nextPoll();
      await nextPoll();
      await nextPoll();

      expect(calls(server, PROGRESS_PATH)).toHaveLength(1);
    });

    it("reads nothing while the tab is hidden, and catches up when it is shown again", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      let status = "running";
      const server = fakeServer((call) =>
        call.path === PROGRESS_PATH
          ? json(200, caseProgress(UPLOADED.case_id, status))
          : undefined,
      );
      let visibility: DocumentVisibilityState = "visible";
      vi.spyOn(document, "visibilityState", "get").mockImplementation(
        () => visibility,
      );
      storeCase();
      openUploadScreen();
      await waitFor(() =>
        expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]),
      );

      visibility = "hidden";
      document.dispatchEvent(new Event("visibilitychange"));
      status = "awaiting_human";
      await nextPoll();
      await nextPoll();
      expect(calls(server, PROGRESS_PATH)).toHaveLength(1);

      // Shown again: read at once, without waiting for the next interval.
      visibility = "visible";
      await act(async () => {
        document.dispatchEvent(new Event("visibilitychange"));
        await Promise.resolve();
      });
      await waitFor(() =>
        expect(caseRows()).toEqual([
          [UPLOADED.case_id, "Waiting for a decision"],
        ]),
      );
      expect(calls(server, PROGRESS_PATH)).toHaveLength(2);
    });

    it("stops reading when the screen is left", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const server = fakeServer((call) =>
        call.path === PROGRESS_PATH
          ? json(200, caseProgress(UPLOADED.case_id))
          : undefined,
      );
      storeCase();
      const view = openUploadScreen();
      await waitFor(() =>
        expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]),
      );
      const before = calls(server, PROGRESS_PATH).length;

      view.unmount();
      await vi.advanceTimersByTimeAsync(PROGRESS_POLL_MS * 3);

      expect(calls(server, PROGRESS_PATH)).toHaveLength(before);
    });

    it("reads nothing while there is no case to follow", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const server = fakeServer();

      openUploadScreen();
      await nextPoll();

      expect(
        server.calls.filter((call) => call.path.includes("/progress")),
      ).toHaveLength(0);
    });
  });

  it("keeps a case stored by an earlier version of the screen, without its old status", async () => {
    fakeServer((call) =>
      call.path === PROGRESS_PATH
        ? json(200, caseProgress(UPLOADED.case_id, "awaiting_human"))
        : undefined,
    );
    // Story 1.5 stored a status with each case; the status now comes from the server.
    window.sessionStorage.setItem(
      CASES_STORAGE_KEY,
      JSON.stringify([{ ...UPLOADED, status: "running" }]),
    );

    openUploadScreen();

    await waitFor(() =>
      expect(caseRows()).toEqual([
        [UPLOADED.case_id, "Waiting for a decision"],
      ]),
    );
  });

  it("starts a case once when the retry is pressed twice", async () => {
    let answer: (response: Response) => void = () => {};
    let failing = true;
    const server = fakeServer((call) => {
      if (call.path !== START_PATH) {
        return undefined;
      }
      if (failing) {
        return json(502, errorBody("upstream_unavailable", "Down."));
      }
      return new Promise<Response>((resolveAnswer) => {
        answer = resolveAnswer;
      });
    });
    const user = userEvent.setup();
    openUploadScreen();
    await chooseAndUpload(user);
    await screen.findByText(NOT_STARTED);
    failing = false;
    const button = startAgainButton();

    fireEvent.click(button);
    fireEvent.click(button);

    await waitFor(() =>
      expect(caseRows()).toEqual([[UPLOADED.case_id, "Starting…"]]),
    );
    expect(calls(server, START_PATH)).toHaveLength(2);
    answer(json(200, startedCase(UPLOADED.case_id)));
    await waitFor(() =>
      expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]),
    );
  });
});

describe("1.7 a case whose redaction failed", () => {
  const PROGRESS_PATH = `/api/cases/${UPLOADED.case_id}/progress`;
  const UPLOAD_AGAIN =
    "Your document could not be processed. Please upload the document again.";

  afterEach(() => {
    vi.useRealTimers();
  });

  it("asks the customer to upload the document again, once the server says the case failed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // Redaction is under way at first; then it fails and the case with it.
    let failed = false;
    const server = fakeServer((call) =>
      call.path === PROGRESS_PATH && failed
        ? json(200, caseProgress(UPLOADED.case_id, "failed", "failed"))
        : undefined,
    );
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    openUploadScreen();

    await user.upload(fileInput(), casePdf());
    await user.click(uploadButton());
    expect(
      await screen.findByText(
        "Your document was uploaded and its case has started.",
      ),
    ).toBeVisible();
    expect(screen.queryByText(UPLOAD_AGAIN)).toBeNull();

    failed = true;
    await act(() => vi.advanceTimersByTimeAsync(PROGRESS_POLL_MS));

    // Without a reload: the message, as an alert, in the place of the success.
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(UPLOAD_AGAIN);
    expect(
      screen.queryByText(
        "Your document was uploaded and its case has started.",
      ),
    ).toBeNull();
    expect(caseRows()).toEqual([
      [UPLOADED.case_id, "Failed. Please upload the document again."],
    ]);
    // The text comes from the strings module, and the status from the server.
    expect(strings.upload.failed).toBe(UPLOAD_AGAIN);
    // A failed case will not change: it is not read again.
    const reads = server.calls.filter(
      (call) => call.path === PROGRESS_PATH,
    ).length;
    await act(() => vi.advanceTimersByTimeAsync(PROGRESS_POLL_MS * 2));
    expect(
      server.calls.filter((call) => call.path === PROGRESS_PATH),
    ).toHaveLength(reads);
  });

  it("lets the customer upload again straight away", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fakeServer((call) =>
      call.path === PROGRESS_PATH
        ? json(200, caseProgress(UPLOADED.case_id, "failed", "failed"))
        : undefined,
    );
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    openUploadScreen();
    await user.upload(fileInput(), casePdf());
    await user.click(uploadButton());
    // The next read of the case's progress brings the failure.
    await act(() => vi.advanceTimersByTimeAsync(PROGRESS_POLL_MS));
    expect(await screen.findByRole("alert")).toHaveTextContent(UPLOAD_AGAIN);

    // The form is as it was before the upload: a file can be chosen again,
    // and choosing one takes the message about the last file away.
    expect(fileInput()).toBeEnabled();
    await user.upload(fileInput(), casePdf());

    expect(uploadButton()).toBeEnabled();
    expect(screen.queryByText(UPLOAD_AGAIN)).toBeNull();
  });

  it("shows the same for a failed case found in the session after a reload", async () => {
    window.sessionStorage.setItem(
      CASES_STORAGE_KEY,
      JSON.stringify([UPLOADED]),
    );
    fakeServer((call) =>
      call.path === PROGRESS_PATH
        ? json(200, caseProgress(UPLOADED.case_id, "failed", "failed"))
        : undefined,
    );

    openUploadScreen();

    await waitFor(() =>
      expect(caseRows()).toEqual([
        [UPLOADED.case_id, "Failed. Please upload the document again."],
      ]),
    );
    // No start-again button: a failed case is not started a second time.
    expect(screen.queryByRole("button", { name: /Start case/ })).toBeNull();
  });

  it("shows the plain status for a case that failed for another reason", async () => {
    window.sessionStorage.setItem(
      CASES_STORAGE_KEY,
      JSON.stringify([UPLOADED]),
    );
    // The case failed, but not at redaction, which was done.
    fakeServer((call) =>
      call.path === PROGRESS_PATH
        ? json(200, caseProgress(UPLOADED.case_id, "failed", "done"))
        : undefined,
    );

    openUploadScreen();

    await waitFor(() =>
      expect(caseRows()).toEqual([[UPLOADED.case_id, "Failed"]]),
    );
    expect(screen.queryByText(UPLOAD_AGAIN)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("1.9 page badges", () => {
  const PROGRESS_PATH = `/api/cases/${UPLOADED.case_id}/progress`;
  const OTHER_CASE = "019a0000-0000-7000-8000-000000000011";

  afterEach(() => {
    vi.useRealTimers();
  });

  function storeCases(...caseIds: string[]) {
    window.sessionStorage.setItem(
      CASES_STORAGE_KEY,
      JSON.stringify(
        caseIds.map((case_id) => ({
          case_id,
          document_id: UPLOADED.document_id,
        })),
      ),
    );
  }

  function badges(caseId: string = UPLOADED.case_id): string[] {
    const list = screen.queryByRole("list", {
      name: `Pages of case ${caseId}`,
    });
    return list === null
      ? []
      : within(list)
          .getAllByRole("listitem")
          // The badge itself: a page that waits for the customer has its
          // prompt under it (story 1.10).
          .map((item) => item.firstElementChild?.textContent ?? "");
  }

  function reads(server: { calls: RecordedCall[] }): number {
    return server.calls.filter((call) => call.path === PROGRESS_PATH).length;
  }

  async function nextPoll() {
    await act(() => vi.advanceTimersByTimeAsync(PROGRESS_POLL_MS));
  }

  it("shows one badge per page with its number and the status the server gave it, in page order", async () => {
    // The server's list, here out of order on purpose.
    fakeServer((call) =>
      call.path === PROGRESS_PATH
        ? json(
            200,
            caseProgress(UPLOADED.case_id, "awaiting_human", "done", [
              pageProgress(3, "awaiting_triage"),
              pageProgress(1, "extracting"),
              pageProgress(2, "awaiting_customer"),
            ]),
          )
        : undefined,
    );
    storeCases(UPLOADED.case_id);

    openUploadScreen();

    await waitFor(() =>
      expect(badges()).toEqual([
        "Page 1: Being read",
        "Page 2: Needs your answer",
        "Page 3: Waiting for the underwriter",
      ]),
    );
    // Under the case they belong to, beside its own status.
    const [row] = caseRows();
    expect(row?.[0]).toBe(UPLOADED.case_id);
    expect(row?.[1]).toContain("Waiting for a decision");
  });

  it("changes the badges as polling brings new statuses, without a reload, and goes on while the case waits for a person", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let progress = caseProgress(UPLOADED.case_id, "running", "running");
    const server = fakeServer((call) =>
      call.path === PROGRESS_PATH ? json(200, progress) : undefined,
    );
    storeCases(UPLOADED.case_id);

    openUploadScreen();
    await waitFor(() =>
      expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]),
    );
    // Until redaction is done the case has no pages, and no badges.
    expect(badges()).toEqual([]);

    progress = caseProgress(UPLOADED.case_id, "running", "done", [
      pageProgress(1, "uploaded"),
      pageProgress(2, "uploaded"),
    ]);
    await nextPoll();
    expect(badges()).toEqual(["Page 1: Received", "Page 2: Received"]);

    progress = caseProgress(UPLOADED.case_id, "running", "done", [
      pageProgress(1, "classified"),
      pageProgress(2, "uploaded"),
    ]);
    await nextPoll();
    expect(badges()).toEqual(["Page 1: Classified", "Page 2: Received"]);

    progress = caseProgress(UPLOADED.case_id, "awaiting_human", "done", [
      pageProgress(1, "extracting"),
      pageProgress(2, "awaiting_customer"),
    ]);
    await nextPoll();
    expect(badges()).toEqual([
      "Page 1: Being read",
      "Page 2: Needs your answer",
    ]);

    // A case that waits for a person can still change: it is read again,
    // and a later status is shown.
    const before = reads(server);
    progress = caseProgress(UPLOADED.case_id, "awaiting_human", "done", [
      pageProgress(1, "extracted"),
      pageProgress(2, "discarded"),
    ]);
    await nextPoll();
    expect(reads(server)).toBe(before + 1);
    expect(badges()).toEqual(["Page 1: Read", "Page 2: Discarded"]);

    // A finished case is read no more; its badges stay.
    progress = caseProgress(UPLOADED.case_id, "completed", "done", [
      pageProgress(1, "extracted"),
      pageProgress(2, "discarded"),
    ]);
    await nextPoll();
    const finished = reads(server);
    await nextPoll();
    await nextPoll();
    expect(reads(server)).toBe(finished);
    expect(badges()).toEqual(["Page 1: Read", "Page 2: Discarded"]);
  });

  it("shows each case's own pages", async () => {
    fakeServer((call) => {
      if (call.path === PROGRESS_PATH) {
        return json(
          200,
          caseProgress(UPLOADED.case_id, "running", "done", [
            pageProgress(1, "extracting"),
          ]),
        );
      }
      if (call.path === `/api/cases/${OTHER_CASE}/progress`) {
        return json(
          200,
          caseProgress(OTHER_CASE, "failed", "done", [
            pageProgress(1, "classified"),
            pageProgress(2, "failed", "invalid_model_output"),
          ]),
        );
      }
      return undefined;
    });
    storeCases(UPLOADED.case_id, OTHER_CASE);

    openUploadScreen();

    await waitFor(() => expect(badges()).toEqual(["Page 1: Being read"]));
    await waitFor(() =>
      expect(badges(OTHER_CASE)).toEqual([
        "Page 1: Classified",
        "Page 2: Failed",
      ]),
    );
  });

  it("has a wording for every page status, in the strings module", () => {
    const statuses = [
      "uploaded",
      "classified",
      "awaiting_customer",
      "awaiting_triage",
      "extracting",
      "extracted",
      "discarded",
      "denied",
      "failed",
    ];

    expect(Object.keys(strings.pageStatus).sort()).toEqual(statuses.sort());
    for (const wording of Object.values(strings.pageStatus)) {
      expect(wording.trim()).not.toBe("");
    }
    expect(strings.upload.pageBadge(2, strings.pageStatus.denied)).toBe(
      "Page 2: Denied",
    );
  });

  it("shows a status it has no wording for as the server sent it", async () => {
    fakeServer((call) =>
      call.path === PROGRESS_PATH
        ? json(
            200,
            caseProgress(UPLOADED.case_id, "running", "done", [
              pageProgress(1, "archived"),
            ]),
          )
        : undefined,
    );
    storeCases(UPLOADED.case_id);

    openUploadScreen();

    await waitFor(() => expect(badges()).toEqual(["Page 1: archived"]));
  });

  it("works out no status or route in the browser", () => {
    const sources = [
      "src/screens/UploadDocument.tsx",
      "src/cases/caseProgress.ts",
      "src/cases/classifications.ts",
      "src/components/PagePrompt.tsx",
      "src/strings.ts",
      "src/api/client.ts",
    ].map((path) => readFileSync(resolve(process.cwd(), path), "utf8"));

    for (const source of sources) {
      // No threshold and no medical-or-not reach the screen. The confidence
      // does since story 1.10, to be shown as the server gave it: nothing
      // compares it with anything.
      expect(source).not.toMatch(/threshold|is_medical|0\.9/i);
      expect(source).not.toMatch(/confidence\s*[<>]=?|[<>]=?\s*confidence/i);
    }
  });
});
