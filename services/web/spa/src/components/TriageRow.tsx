import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError, decidePage, mayBeStored } from "../api/client";
import type { Decision, TriagePage } from "../api/contracts.gen";
import { auditTrailPath } from "../audit/auditPath";
import { percentage, strings } from "../strings";
import { ErrorMessage } from "./ErrorMessage";
import { PageThumbnail } from "./PageThumbnail";

type State =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "saved" }
  /**
   * The server said the page no longer waits: someone decided it elsewhere.
   * `atRead`: the number of the newest read of the queue at that moment.
   */
  | { kind: "decided_elsewhere"; atRead: number }
  | { kind: "failed"; error: unknown };

// A read that was out when the refusal came may be older than it; the one
// after that is not. From then on a page still listed does wait after all.
const READS_UNTIL_LISTED_AGAIN = 2;

function isDecidedElsewhere(error: unknown): boolean {
  return error instanceof ApiError && error.code === "not_awaiting_decision";
}

/** What the classifier said of the page, in plain words; nothing is worked out here. */
function Reading({ page }: { page: TriagePage }) {
  if (page.page_type === null || page.confidence === null) {
    return <p>{strings.triage.noReading}</p>;
  }
  return (
    <>
      <p>
        {strings.triage.looksLike(
          // A type this build has no words for is shown as the server named it.
          Object.hasOwn(strings.pageType, page.page_type)
            ? strings.pageType[page.page_type]
            : page.page_type,
          percentage(page.confidence),
        )}
      </p>
      {/* The model's own sentence: shown as text, never as HTML. */}
      <p>
        {strings.triage.reasonLabel} {page.reason}
      </p>
    </>
  );
}

/**
 * One page of the triage queue: its thumbnail, what the classifier said,
 * and Accept and Deny (AD-10). It decides nothing itself: the server does.
 */
export function TriageRow({
  page,
  listed,
  reads,
  onDecided,
  onHeld,
}: {
  page: TriagePage;
  /** Whether the newest read of the queue lists the page. */
  listed: boolean;
  /** Counts the reads of the queue that were answered. */
  reads: number;
  /** A decision was answered, saved or refused: the queue is to be read again. */
  onDecided: () => void;
  /**
   * Whether the row must stay also once the server lists the page no longer:
   * a decision is being sent, or may be stored without the case having been
   * told, and the row is where its answer and the way to send it again are.
   */
  onHeld: (page: TriagePage, held: boolean) => void;
}) {
  const [state, setState] = useState<State>({ kind: "idle" });
  // A call that failed may have stored the decision without the case being
  // told. The same decision sent again is safe, and is what tells it; the
  // other one would be refused and the first never told. So from then on
  // only that decision can be sent, whatever a later call is refused with,
  // until the server says it is stored or that the page waits no longer.
  const [owed, setOwed] = useState<Decision | null>(null);
  const sending = state.kind === "sending";
  const held = sending || owed !== null;
  // The newest read's number, for an answer that comes later than this render.
  const newestRead = useRef(reads);
  useEffect(() => {
    newestRead.current = reads;
  }, [reads]);

  // Told whenever the queue is read again too; telling the same twice changes nothing.
  useEffect(() => {
    onHeld(page, held);
  }, [onHeld, page, held]);

  function send(decision: Decision) {
    if (sending) {
      return;
    }
    setState({ kind: "sending" });
    decidePage(page.case_id, page.page_id, decision).then(
      () => {
        setOwed(null);
        setState({ kind: "saved" });
        onDecided();
      },
      (error: unknown) => {
        if (isDecidedElsewhere(error)) {
          setOwed(null);
          setState({ kind: "decided_elsewhere", atRead: newestRead.current });
          onDecided();
          return;
        }
        if (mayBeStored(error)) {
          setOwed(decision);
        }
        setState({ kind: "failed", error });
      },
    );
  }

  // Decided elsewhere, until reads that came after still list the page:
  // then it does wait, and can be decided here.
  const decidedElsewhere =
    state.kind === "decided_elsewhere" &&
    !(listed && reads >= state.atRead + READS_UNTIL_LISTED_AGAIN);
  const settled = state.kind === "saved" || decidedElsewhere;
  const { page_number: pageNumber, case_id: caseId } = page;
  return (
    <tr>
      <td>
        <PageThumbnail
          address={page.thumbnail_path}
          label={strings.triage.thumbnailOf(pageNumber, caseId)}
          reads={reads}
        />
      </td>
      <td>
        <code>{caseId}</code>
        <br />
        {strings.triage.pageOf(pageNumber)}
        <br />
        <Link
          to={auditTrailPath(caseId)}
          aria-label={strings.audit.linkFromTriageFor(caseId)}
        >
          {strings.audit.linkFromTriage}
        </Link>
      </td>
      <td>
        <Reading page={page} />
        {page.queued_by != null &&
          Object.hasOwn(strings.triage.queuedBy, page.queued_by) && (
            <p>{strings.triage.queuedBy[page.queued_by]}</p>
          )}
      </td>
      <td>
        <div
          role="group"
          aria-label={strings.triage.decisionFor(pageNumber, caseId)}
        >
          {state.kind === "saved" && (
            <p role="status">{strings.triage.saved}</p>
          )}
          {decidedElsewhere && (
            <p role="status">{strings.triage.decidedElsewhere}</p>
          )}
          {!settled &&
            (owed === null ? (
              <p>
                <button
                  type="button"
                  disabled={sending}
                  aria-label={strings.triage.acceptFor(pageNumber, caseId)}
                  onClick={() => send("accept")}
                >
                  {strings.triage.accept}
                </button>{" "}
                <button
                  type="button"
                  disabled={sending}
                  aria-label={strings.triage.denyFor(pageNumber, caseId)}
                  onClick={() => send("deny")}
                >
                  {strings.triage.deny}
                </button>
              </p>
            ) : (
              <p>
                <button
                  type="button"
                  disabled={sending}
                  aria-label={strings.triage.tryAgainFor(pageNumber, caseId)}
                  onClick={() => send(owed)}
                >
                  {strings.triage.tryAgain}
                </button>
              </p>
            ))}
          {sending && <p role="status">{strings.triage.sending}</p>}
          {state.kind === "failed" && <ErrorMessage error={state.error} />}
        </div>
      </td>
    </tr>
  );
}
