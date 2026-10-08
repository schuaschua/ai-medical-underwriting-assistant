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
import { PROGRESS_POLL_MS } from "../cases/caseProgress";
import { UPLOAD_KEY_STORAGE_KEY } from "../cases/uploadKey";
import { CASES_STORAGE_KEY } from "../cases/sessionCases";
import { backoffMs } from "../polling/backoff";
import { ROLE_STORAGE_KEY } from "../role/roleStore";
import { strings } from "../strings";
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
      "a file that is not a PDF",
      415,
      "unsupported_file_type",
      "Only PDF files can be uploaded.",
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

  it.each([
    [
      "the start is refused behind the server",
      () =>
        json(502, errorBody("upstream_unavailable", "<b>server wording</b>")),
      "The service is not available right now. Please try again.",
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
