import { useId, useRef, useState, type FormEvent } from "react";
import { uploadDocument } from "../api/client";
import type { PageProgress } from "../api/contracts.gen";
import {
  stateOf,
  useCaseProgress,
  type CaseState,
} from "../cases/caseProgress";
import { useClassifications } from "../cases/classifications";
import { addSessionCase, useSessionCases } from "../cases/sessionCases";
import { forgetUploadKey, uploadKeyFor } from "../cases/uploadKey";
import { ErrorMessage } from "../components/ErrorMessage";
import { PagePrompt } from "../components/PagePrompt";
import { strings } from "../strings";

type State =
  | { status: "idle" }
  | { status: "uploading" }
  | { status: "uploaded"; caseId: string }
  | { status: "failed"; error: unknown };

/** Whether the server reported the case as failed at redaction (AD-21). */
function redactionFailed(state: CaseState): boolean {
  return (
    state.kind === "started" &&
    state.status === "failed" &&
    state.redactionFailed === true
  );
}

/** What the list says about one case. A status always comes from the server. */
function caseText(state: CaseState): string {
  switch (state.kind) {
    case "checking":
      return strings.upload.caseChecking;
    case "starting":
      return strings.upload.caseStarting;
    case "started":
      // The server said the redaction failed: what to do about it is said
      // with the status. A case that failed otherwise shows its status alone.
      return redactionFailed(state)
        ? strings.upload.caseFailed
        : strings.caseStatus[state.status];
    case "not_started":
      return strings.upload.caseNotStarted;
    case "unreadable":
      return strings.upload.caseUnreadable;
  }
}

/**
 * One badge per page: its number and the status the server gave it, in page
 * order. Nothing here works a status out (AD-7, AD-19). A page that waits
 * for the customer has its prompt under its badge (AD-10).
 */
function PageBadges({
  caseId,
  pages,
  onAnswered,
}: {
  caseId: string;
  pages: readonly PageProgress[];
  onAnswered: () => void;
}) {
  const classifications = useClassifications(caseId, pages);
  if (pages.length === 0) {
    return null;
  }
  const inPageOrder = [...pages].sort(
    (one, other) => one.page_number - other.page_number,
  );
  return (
    <ul aria-label={strings.upload.pagesOf(caseId)}>
      {inPageOrder.map((page) => (
        <li key={page.page_id}>
          <span>
            {strings.upload.pageBadge(
              page.page_number,
              // A status this build has no wording for is shown as it came.
              Object.hasOwn(strings.pageStatus, page.page_status)
                ? strings.pageStatus[page.page_status]
                : page.page_status,
            )}
          </span>
          <PagePrompt
            caseId={caseId}
            page={page}
            classification={
              Object.hasOwn(classifications, page.page_id)
                ? classifications[page.page_id]
                : undefined
            }
            onAnswered={onAnswered}
          />
        </li>
      ))}
    </ul>
  );
}

/** The customer's upload screen: one PDF in, and the cases uploaded so far. */
export function UploadDocument() {
  const [state, setState] = useState<State>({ status: "idle" });
  const [hasFile, setHasFile] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const fileInputId = useId();
  const cases = useSessionCases();
  const { states, start, check } = useCaseProgress(
    cases.map((item) => item.case_id),
  );
  const uploading = state.status === "uploading";
  const uploadedCase =
    state.status === "uploaded" ? stateOf(states, state.caseId) : null;
  // What the server says of the case just uploaded: not started, or failed
  // at redaction.
  const uploadedCaseAlert =
    uploadedCase === null
      ? null
      : uploadedCase.kind === "not_started"
        ? strings.upload.notStarted
        : redactionFailed(uploadedCase)
          ? strings.upload.failed
          : null;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const file = fileInput.current?.files?.[0];
    if (file === undefined || uploading) {
      return;
    }
    setState({ status: "uploading" });
    // Size and type are checked by the server (security.md rule 20); the
    // file is sent as it is. One key per file: sending that file again after
    // a failure, or after a reload, sends the same key, so the server
    // answers with the first case, not a second.
    uploadDocument(file, uploadKeyFor(file)).then(
      (uploaded) => {
        addSessionCase(uploaded);
        // The second half of an upload (AD-2). If it fails, the case stays
        // listed as received, with a way to try again.
        start(uploaded.case_id);
        if (fileInput.current !== null) {
          fileInput.current.value = "";
        }
        forgetUploadKey();
        setHasFile(false);
        setState({ status: "uploaded", caseId: uploaded.case_id });
      },
      (error: unknown) => setState({ status: "failed", error }),
    );
  }

  return (
    <section>
      <h2>{strings.upload.heading}</h2>
      <p>{strings.upload.intro}</p>
      <form onSubmit={submit}>
        <p>
          <label htmlFor={fileInputId}>{strings.upload.fileLabel}</label>{" "}
          <input
            id={fileInputId}
            ref={fileInput}
            type="file"
            accept="application/pdf,.pdf"
            disabled={uploading}
            onChange={(event) => {
              setHasFile((event.target.files?.length ?? 0) > 0);
              // What was said about the last file does not hold for this one.
              setState({ status: "idle" });
            }}
          />
        </p>
        <p>
          <button type="submit" disabled={!hasFile || uploading}>
            {strings.upload.submit}
          </button>
        </p>
      </form>
      {uploading && (
        <p role="status">
          {/* No value: the browser shows it as "in progress". */}
          <progress aria-label={strings.upload.uploading} />{" "}
          {strings.upload.uploading}
        </p>
      )}
      {uploadedCase !== null &&
        (uploadedCaseAlert !== null ? (
          <p role="alert">{uploadedCaseAlert}</p>
        ) : (
          <p role="status">
            {uploadedCase.kind === "started"
              ? strings.upload.uploaded
              : strings.upload.received}
          </p>
        ))}
      {state.status === "failed" && <ErrorMessage error={state.error} />}

      <h3>{strings.upload.casesHeading}</h3>
      {cases.length === 0 ? (
        <p>{strings.upload.noCases}</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">{strings.upload.caseColumn}</th>
              <th scope="col">{strings.upload.statusColumn}</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((item) => {
              const caseState = stateOf(states, item.case_id);
              return (
                <tr key={item.case_id}>
                  <td>
                    <code>{item.case_id}</code>
                  </td>
                  <td>
                    {caseText(caseState)}
                    {caseState.kind === "started" && (
                      <PageBadges
                        caseId={item.case_id}
                        pages={caseState.pages}
                        // The page's new status is the server's to say.
                        onAnswered={() => check(item.case_id)}
                      />
                    )}
                    {caseState.kind === "not_started" && (
                      <>
                        {" "}
                        <button
                          type="button"
                          aria-label={strings.upload.startAgainFor(
                            item.case_id,
                          )}
                          onClick={() => start(item.case_id)}
                        >
                          {strings.upload.startAgain}
                        </button>
                        {caseState.error !== null && (
                          <ErrorMessage error={caseState.error} />
                        )}
                      </>
                    )}
                    {caseState.kind === "unreadable" && (
                      <>
                        {" "}
                        <button
                          type="button"
                          aria-label={strings.upload.checkAgainFor(
                            item.case_id,
                          )}
                          onClick={() => check(item.case_id)}
                        >
                          {strings.upload.checkAgain}
                        </button>
                        <ErrorMessage error={caseState.error} />
                      </>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
