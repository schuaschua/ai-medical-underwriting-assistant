import { Link } from "react-router";
import { auditTrailPath } from "../audit/auditPath";
import { useCaseList } from "../cases/caseList";
import { ErrorMessage } from "../components/ErrorMessage";
import { LocalTime } from "../components/LocalTime";
import { resultPath } from "../result/resultPath";
import { strings } from "../strings";

/** The words for a case status, or the status itself when this build has none. */
function statusText(status: string): string {
  // Own keys only: "constructor" must not find a prototype member.
  return Object.hasOwn(strings.caseStatus, status)
    ? strings.caseStatus[status as keyof typeof strings.caseStatus]
    : status;
}

/**
 * The underwriter's list of cases: every case the server lists, newest first
 * as it orders them, each with a link to its result and to its audit trail. Nothing here
 * chooses, orders or counts (AD-19): the status and the numbers are the
 * server's.
 */
export function CaseList() {
  const { state, refresh } = useCaseList();

  if (state.kind === "reading") {
    return (
      <section>
        <h2>{strings.cases.heading}</h2>
        <p role="status">{strings.cases.reading}</p>
      </section>
    );
  }
  if (state.kind === "unreadable") {
    return (
      <section>
        <h2>{strings.cases.heading}</h2>
        <ErrorMessage error={state.error} />
        <p>
          <button type="button" onClick={refresh}>
            {strings.cases.checkAgain}
          </button>
        </p>
      </section>
    );
  }

  const { cases, has_more: hasMore } = state.value;
  return (
    <section>
      <h2>{strings.cases.heading}</h2>
      <p>{strings.cases.intro}</p>
      {state.staleError !== null && (
        <div>
          <p role="alert">{strings.cases.stale}</p>
          <p>
            <button type="button" onClick={refresh}>
              {strings.cases.checkAgain}
            </button>
          </p>
        </div>
      )}
      {cases.length === 0 ? (
        <p>{strings.cases.empty}</p>
      ) : (
        <table aria-label={strings.cases.tableLabel}>
          <thead>
            <tr>
              <th scope="col">{strings.cases.caseColumn}</th>
              <th scope="col">{strings.cases.statusColumn}</th>
              <th scope="col">{strings.cases.startedColumn}</th>
              <th scope="col">{strings.cases.pagesColumn}</th>
              <th scope="col">{strings.cases.waitingColumn}</th>
              <th scope="col">{strings.cases.resultColumn}</th>
              <th scope="col">{strings.cases.trailColumn}</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((listed) => (
              <tr key={listed.case_id}>
                <th scope="row">
                  <code>{listed.case_id}</code>
                </th>
                <td>{statusText(listed.case_status)}</td>
                <td>
                  <LocalTime at={listed.started_at} />
                </td>
                <td>{listed.page_count}</td>
                <td>{listed.waiting_page_count}</td>
                <td>
                  <Link
                    to={resultPath(listed.case_id)}
                    aria-label={strings.cases.resultFor(listed.case_id)}
                  >
                    {strings.cases.result}
                  </Link>
                </td>
                <td>
                  <Link
                    to={auditTrailPath(listed.case_id)}
                    aria-label={strings.cases.trailFor(listed.case_id)}
                  >
                    {strings.cases.trail}
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {hasMore && <p>{strings.cases.more}</p>}
    </section>
  );
}
