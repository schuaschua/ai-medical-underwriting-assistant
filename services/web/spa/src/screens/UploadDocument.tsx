import { useId, useRef, useState, type FormEvent } from "react";
import { uploadDocument } from "../api/client";
import {
  stateOf,
  useCaseProgress,
  type CaseState,
} from "../cases/caseProgress";
import { addSessionCase, useSessionCases } from "../cases/sessionCases";
import { forgetUploadKey, uploadKeyFor } from "../cases/uploadKey";
import { ErrorMessage } from "../components/ErrorMessage";
import { strings } from "../strings";

type State =
  | { status: "idle" }
  | { status: "uploading" }
  | { status: "uploaded"; caseId: string }
  | { status: "failed"; error: unknown };

/** What the list says about one case. A status always comes from the server. */
function caseText(state: CaseState): string {
  switch (state.kind) {
    case "checking":
      return strings.upload.caseChecking;
    case "starting":
      return strings.upload.caseStarting;
    case "started":
      return strings.caseStatus[state.status];
    case "not_started":
      return strings.upload.caseNotStarted;
    case "unreadable":
      return strings.upload.caseUnreadable;
  }
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
        (uploadedCase.kind === "not_started" ? (
          <p role="alert">{strings.upload.notStarted}</p>
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
