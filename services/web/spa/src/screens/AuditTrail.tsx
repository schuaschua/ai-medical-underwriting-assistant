import { useId, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { CASE_PARAMETER, parseCaseId } from "../audit/auditPath";
import { useAuditTrail } from "../audit/auditTrail";
import { isFinalStatus } from "../cases/caseProgress";
import { AuditEventRow } from "../components/AuditEventRow";
import { ErrorMessage } from "../components/ErrorMessage";
import { resultPath } from "../result/resultPath";
import { strings } from "../strings";

/** The trail of one case, as the server answers it and in its order (AD-8, AD-19). */
function Trail({ caseId }: { caseId: string }) {
  const { state, refresh } = useAuditTrail(caseId);
  const checkAgain = (
    <p>
      <button type="button" onClick={refresh}>
        {strings.audit.checkAgain}
      </button>
    </p>
  );

  if (state.kind === "reading") {
    return <p role="status">{strings.audit.reading}</p>;
  }
  if (state.kind === "unknown") {
    return (
      <>
        <p role="alert">{strings.audit.unknownCase}</p>
        {checkAgain}
      </>
    );
  }
  if (state.kind === "unreadable") {
    return (
      <>
        <ErrorMessage error={state.error} />
        {checkAgain}
      </>
    );
  }

  const { trail, progress } = state;
  const status = progress.case_status;
  return (
    <>
      <p>
        {strings.audit.caseStatus(
          Object.hasOwn(strings.caseStatus, status)
            ? strings.caseStatus[status]
            : status,
        )}{" "}
        {isFinalStatus(status)
          ? strings.audit.finished
          : trail.has_more
            ? strings.audit.firstOnly
            : strings.audit.following}
      </p>
      <p>
        <Link to={resultPath(caseId)}>{strings.audit.linkToResult}</Link>
      </p>
      {state.staleError !== null && (
        <div>
          <p role="alert">{strings.audit.stale}</p>
          {checkAgain}
        </div>
      )}
      {trail.events.length === 0 ? (
        <p>{strings.audit.empty}</p>
      ) : (
        <table aria-label={strings.audit.trailOf(caseId)}>
          <thead>
            <tr>
              <th scope="col">{strings.audit.timeColumn}</th>
              <th scope="col">{strings.audit.actorColumn}</th>
              <th scope="col">{strings.audit.actionColumn}</th>
              <th scope="col">{strings.audit.pageColumn}</th>
              <th scope="col">{strings.audit.detailColumn}</th>
            </tr>
          </thead>
          <tbody>
            {trail.events.map((event) => (
              <AuditEventRow
                // AD-8: one event per case, page, action and reference.
                key={`${event.action}:${event.page_id ?? ""}:${event.ref}`}
                event={event}
                pages={progress.pages}
              />
            ))}
          </tbody>
        </table>
      )}
      {trail.has_more && <p>{strings.audit.more}</p>}
    </>
  );
}

/**
 * The field for a case id. What it holds at first, and whether it is
 * refused, come from the address alone; the screen mounts it anew whenever
 * the address names something else, so the two cannot go out of step.
 */
function CaseField({
  named,
  invalid,
  onSubmit,
}: {
  /** What the address names, or null if it names nothing. */
  named: string | null;
  invalid: boolean;
  onSubmit: (typed: string) => void;
}) {
  const [typed, setTyped] = useState(named ?? "");
  const fieldId = useId();
  const hintId = useId();
  const errorId = useId();

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit(typed);
  }

  return (
    <form onSubmit={submit} noValidate>
      <p>
        <label htmlFor={fieldId}>{strings.audit.caseLabel}</label>{" "}
        <input
          id={fieldId}
          type="text"
          size={40}
          autoComplete="off"
          spellCheck={false}
          value={typed}
          aria-invalid={invalid}
          aria-describedby={invalid ? `${hintId} ${errorId}` : hintId}
          onChange={(change) => setTyped(change.target.value)}
        />{" "}
        <button type="submit">{strings.audit.show}</button>
      </p>
      <p id={hintId}>
        <small>{strings.audit.caseHint}</small>
      </p>
      {invalid && (
        <p id={errorId} role="alert">
          {strings.audit.notACaseId}
        </p>
      )}
    </form>
  );
}

/**
 * The underwriter's audit trail screen: a field for a case id, and that
 * case's events. The address names the case, and everything shown follows
 * from the address: a row of the triage queue links straight to a trail,
 * and back and forward move between the trails that were looked at.
 */
export function AuditTrail() {
  const [parameters, setParameters] = useSearchParams();
  const named = parameters.get(CASE_PARAMETER);
  const caseId = named === null ? null : parseCaseId(named);

  function show(typed: string) {
    // What was typed goes into the address as the case id it is, or as it
    // was typed when it is none: that address is refused below, with no call.
    setParameters({ [CASE_PARAMETER]: parseCaseId(typed) ?? typed });
  }

  return (
    <section>
      <h2>{strings.audit.heading}</h2>
      <p>{strings.audit.intro}</p>
      <CaseField
        // A new address is a new field: its text and its refusal start over.
        key={`field:${named ?? ""}`}
        named={named}
        // An address that names something that is not a case id is refused.
        invalid={named !== null && caseId === null}
        onSubmit={show}
      />
      {caseId !== null && (
        // A new case is a new trail: nothing of the one before it stays.
        <Trail key={`trail:${caseId}`} caseId={caseId} />
      )}
    </section>
  );
}
