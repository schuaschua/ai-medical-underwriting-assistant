import { useCallback, useState } from "react";
import type { TriagePage } from "../api/contracts.gen";
import { ErrorMessage } from "../components/ErrorMessage";
import { TriageRow } from "../components/TriageRow";
import { strings } from "../strings";
import { useTriageQueue } from "../triage/triageQueue";

/**
 * The underwriter's triage queue: the pages that wait for a decision, across
 * cases, as the server lists them and in its order. Nothing here chooses or
 * orders pages (AD-7, AD-19).
 */
export function TriageQueue() {
  const { state, refresh } = useTriageQueue();
  // Pages whose decision is being sent, or may be stored without the case
  // having been told: their rows stay, with what the call answers and the
  // way to send it again, also once the server lists them no longer.
  const [held, setHeld] = useState<Readonly<Record<string, TriagePage>>>({});

  const noteHeld = useCallback((page: TriagePage, isHeld: boolean) => {
    setHeld((before) => {
      if (Object.hasOwn(before, page.page_id) === isHeld) {
        return before;
      }
      const next = { ...before };
      if (isHeld) {
        next[page.page_id] = page;
      } else {
        delete next[page.page_id];
      }
      return next;
    });
  }, []);

  if (state.kind === "reading") {
    return (
      <section>
        <h2>{strings.triage.heading}</h2>
        <p role="status">{strings.triage.reading}</p>
      </section>
    );
  }
  if (state.kind === "unreadable") {
    return (
      <section>
        <h2>{strings.triage.heading}</h2>
        <ErrorMessage error={state.error} />
        <p>
          <button type="button" onClick={refresh}>
            {strings.triage.checkAgain}
          </button>
        </p>
      </section>
    );
  }

  const listed = new Set(state.queue.pages.map((page) => page.page_id));
  const pages = [
    ...state.queue.pages,
    ...Object.values(held).filter((page) => !listed.has(page.page_id)),
  ];
  return (
    <section>
      <h2>{strings.triage.heading}</h2>
      <p>{strings.triage.intro}</p>
      {state.staleError !== null && (
        <div>
          <p role="alert">{strings.triage.stale}</p>
          <p>
            <button type="button" onClick={refresh}>
              {strings.triage.checkAgain}
            </button>
          </p>
        </div>
      )}
      {pages.length === 0 ? (
        <p>{strings.triage.empty}</p>
      ) : (
        <table aria-label={strings.triage.tableLabel}>
          <thead>
            <tr>
              <th scope="col">{strings.triage.thumbnailColumn}</th>
              <th scope="col">{strings.triage.caseColumn}</th>
              <th scope="col">{strings.triage.readingColumn}</th>
              <th scope="col">{strings.triage.decisionColumn}</th>
            </tr>
          </thead>
          <tbody>
            {pages.map((page) => (
              <TriageRow
                key={page.page_id}
                page={page}
                // Whether the page left the queue is the server's to say.
                onDecided={refresh}
                onHeld={noteHeld}
                listed={listed.has(page.page_id)}
                reads={state.number}
              />
            ))}
          </tbody>
        </table>
      )}
      {state.queue.has_more && <p>{strings.triage.more}</p>}
    </section>
  );
}
