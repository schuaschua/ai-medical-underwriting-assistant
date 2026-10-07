import type {
  AuditRecord,
  CaseProgress,
  RouteDetail,
} from "../api/contracts.gen";
import { exactPercentage, strings } from "../strings";
import { LocalTime } from "./LocalTime";

// W3C trace context: the all-zero id means "no trace".
const NO_TRACE_ID = "0".repeat(32);

/** The words for a key of a table of strings, or the key itself when there are none. */
function worded(table: Readonly<Record<string, string>>, key: string): string {
  // Own keys only: "constructor" must not find a prototype member.
  return Object.hasOwn(table, key) ? table[key]! : key;
}

/**
 * The actor as recorded (AD-8). A person: the demo role. An AI step: the
 * service and, after the first colon, what did the work in it (the model
 * deployment, or the rule). Both parts are shown; neither is worked out.
 */
function actorText(event: AuditRecord): string {
  if (event.actor_kind === "human") {
    return strings.audit.human(worded(strings.roles, event.actor));
  }
  if (Object.hasOwn(strings.audit.knownActor, event.actor)) {
    return worded(strings.audit.knownActor, event.actor);
  }
  const separator = event.actor.indexOf(":");
  if (separator < 0) {
    return event.actor;
  }
  const service = event.actor.slice(0, separator);
  const part = event.actor.slice(separator + 1);
  const serviceWords = worded(strings.audit.service, service);
  // Only a service known to call one has its second half named a model
  // deployment; any other actor shows its two parts and no more.
  return Object.hasOwn(strings.audit.actorPart, service)
    ? strings.audit.ai(
        serviceWords,
        worded(strings.audit.actorPart, service),
        part,
      )
    : strings.audit.otherActor(serviceWords, part);
}

function isRouteDetail(detail: AuditRecord["detail"]): detail is RouteDetail {
  return (
    detail !== null &&
    typeof (detail as Record<string, unknown>).route === "string" &&
    typeof (detail as Record<string, unknown>).threshold === "number"
  );
}

/**
 * What the event's detail says: the redaction counts, the gate's route and
 * threshold, the retrieval row of a suggested verdict, or the reason a step failed. Nothing else of an event is shown.
 */
function detailText(event: AuditRecord): string {
  if (event.action === "stage.failed") {
    return event.error_code == null
      ? strings.audit.noReason
      : strings.audit.failedBecause(
          worded(strings.audit.failure, event.error_code),
        );
  }
  if (event.action === "page.routed" && isRouteDetail(event.detail)) {
    return strings.audit.routed(
      worded(strings.audit.route, event.detail.route),
      // As recorded, to the digit: the gate's own number, not a rounding of it.
      exactPercentage(event.detail.threshold),
    );
  }
  if (event.action === "verdict.suggested" && event.detail !== null) {
    const row = (event.detail as Record<string, unknown>).retriever_config;
    // The row as recorded: the browser names it and decides nothing by it.
    return typeof row === "string" ? strings.audit.suggestedWith(row) : "";
  }
  if (event.action === "document.redacted" && event.detail !== null) {
    const counts = Object.entries(event.detail)
      .filter(([, count]) => typeof count === "number")
      .map(([category, count]) =>
        strings.audit.redactionCount(category, count as number),
      );
    return counts.length === 0
      ? strings.audit.redactedNothing
      : strings.audit.redacted(counts.join(", "));
  }
  return "";
}

/**
 * One event of a case's audit trail: time, actor, action, page and detail.
 * A "verdict suggested" event also offers its run's steps (story 2.8): the
 * event's reference is the run (AD-8, AD-15).
 */
export function AuditEventRow({
  event,
  pages,
  stepsOpen,
  onToggleSteps,
}: {
  event: AuditRecord;
  /** The case's pages as the server lists them: where a page's number comes from. */
  pages: CaseProgress["pages"];
  /** Whether this event's run has its steps open under the trail. */
  stepsOpen: boolean;
  /** Open or close the steps of a run; the control is handed on, for the focus to return to. */
  onToggleSteps: (runId: string, opener: HTMLButtonElement) => void;
}) {
  const pageNumber =
    event.page_id === null
      ? null
      : (pages.find((page) => page.page_id === event.page_id)?.page_number ??
        null);
  const action = worded(strings.audit.action, event.action);
  return (
    <tr>
      <td>
        <LocalTime at={event.occurred_at} />
      </td>
      <td>{actorText(event)}</td>
      <td>
        {pageNumber === null
          ? action
          : strings.audit.actionOnPage(action, pageNumber)}
      </td>
      <td>
        {event.page_id === null ? (
          strings.audit.wholeCase
        ) : pageNumber === null ? (
          // The server lists no such page: shown by the id the event names.
          <code>{event.page_id}</code>
        ) : (
          strings.audit.pageOf(pageNumber)
        )}
      </td>
      <td>
        {detailText(event)}
        {event.action === "verdict.suggested" && (
          <>
            {" "}
            <button
              type="button"
              aria-expanded={stepsOpen}
              onClick={(click) => onToggleSteps(event.ref, click.currentTarget)}
            >
              {strings.steps.openFromTrail}
            </button>
          </>
        )}
        {event.action === "stage.failed" && event.trace_id !== NO_TRACE_ID && (
          // What to look for in the logs, worded as an error's reference is.
          <>
            {" "}
            <small>{strings.errors.reference(event.trace_id)}</small>
          </>
        )}
      </td>
    </tr>
  );
}
