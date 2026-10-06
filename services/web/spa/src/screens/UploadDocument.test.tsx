import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
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
import { CASES_STORAGE_KEY } from "../cases/sessionCases";
import { ROLE_STORAGE_KEY } from "../role/roleStore";
import { MAX_UPLOAD_MB, strings } from "../strings";
import { errorBody, fakeServer, json, UPLOADED } from "../test/server";

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
    // One call: the file itself as the body, declared as a PDF, as the customer.
    const [upload] = server.calls;
    expect(server.calls).toHaveLength(1);
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
    const server = fakeServer(
      () =>
        new Promise<Response>((resolveAnswer) => {
          answer = resolveAnswer;
        }),
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
    expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]);
  });

  it("lists every case uploaded in this session, newest first", async () => {
    const second = {
      ...UPLOADED,
      case_id: "019a0000-0000-7000-8000-000000000003",
    };
    let uploads = 0;
    fakeServer(() => json(201, uploads++ === 0 ? UPLOADED : second));
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

    // A reload in the same browser session still shows them.
    firstView.unmount();
    openUploadScreen();
    expect(caseRows()).toHaveLength(2);
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
      reachable
        ? json(201, UPLOADED)
        : Promise.reject(new TypeError("Failed to fetch")),
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

    await screen.findByRole("table");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each([
    ["no case id", { document_id: UPLOADED.document_id, status: "running" }],
    ["an empty case id", { ...UPLOADED, case_id: "" }],
    ["an unknown status", { ...UPLOADED, status: "archived" }],
    [
      "a status that is a prototype member",
      { ...UPLOADED, status: "toString" },
    ],
    ["no document id", { case_id: UPLOADED.case_id, status: "running" }],
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
    const server = fakeServer(
      () =>
        new Promise<Response>((resolveAnswer) => {
          answer = resolveAnswer;
        }),
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
        { case_id: 7, document_id: "x", status: "running" },
        { ...UPLOADED, case_id: "other", status: "constructor" },
        null,
      ]),
    );

    openUploadScreen();

    expect(caseRows()).toEqual([[UPLOADED.case_id, "Running"]]);
  });

  it("shows no cases when the stored list is not JSON", () => {
    fakeServer();
    window.sessionStorage.setItem(CASES_STORAGE_KEY, "{not json");

    openUploadScreen();

    expect(screen.getByText("No documents uploaded yet.")).toBeVisible();
  });
});
