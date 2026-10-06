import { useId, useRef, useState, type FormEvent } from "react";
import { uploadDocument } from "../api/client";
import { addSessionCase, useSessionCases } from "../cases/sessionCases";
import { ErrorMessage } from "../components/ErrorMessage";
import { strings } from "../strings";

type State =
  | { status: "idle" }
  | { status: "uploading" }
  | { status: "uploaded" }
  | { status: "failed"; error: unknown };

/** The customer's upload screen: one PDF in, and the cases uploaded so far. */
export function UploadDocument() {
  const [state, setState] = useState<State>({ status: "idle" });
  const [hasFile, setHasFile] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const fileInputId = useId();
  const cases = useSessionCases();
  const uploading = state.status === "uploading";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const file = fileInput.current?.files?.[0];
    if (file === undefined || uploading) {
      return;
    }
    setState({ status: "uploading" });
    // Size and type are checked by the server (security.md rule 20); the
    // file is sent as it is.
    uploadDocument(file).then(
      (uploaded) => {
        addSessionCase(uploaded);
        if (fileInput.current !== null) {
          fileInput.current.value = "";
        }
        setHasFile(false);
        setState({ status: "uploaded" });
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
      {state.status === "uploaded" && (
        <p role="status">{strings.upload.uploaded}</p>
      )}
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
            {cases.map((item) => (
              <tr key={item.case_id}>
                <td>
                  <code>{item.case_id}</code>
                </td>
                <td>{strings.caseStatus[item.status]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
