import { useState } from "react";
import { decidePage, mayBeStored } from "../api/client";
import type {
  Classification,
  Decision,
  PageProgress,
} from "../api/contracts.gen";
import { percentage, strings } from "../strings";
import { ErrorMessage } from "./ErrorMessage";

type State =
  | { kind: "idle" }
  /** `repeating`: the answer being sent again, after a call that may have stored it. */
  | { kind: "sending"; repeating: Decision | null }
  | { kind: "answered" }
  | { kind: "failed"; decision: Decision; error: unknown };

/** What the prompt says: the type and confidence the server gave, in plain words. */
function promptText(classification: Classification | undefined): string {
  if (
    classification === undefined ||
    !Object.hasOwn(strings.pageType, classification.page_type)
  ) {
    return strings.decision.promptWithoutType;
  }
  return strings.decision.prompt(
    strings.pageType[classification.page_type],
    percentage(classification.confidence),
  );
}

/**
 * Asks the customer about one page that waits for them: discard it or keep
 * it (AD-10). It is shown for a page the server reports as
 * `awaiting_customer`, and decides nothing itself: the server does.
 */
export function PagePrompt({
  caseId,
  page,
  classification,
  onAnswered,
}: {
  caseId: string;
  page: PageProgress;
  classification: Classification | undefined;
  /** The answer was saved: the case's progress is to be read again. */
  onAnswered: () => void;
}) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const awaiting = page.page_status === "awaiting_customer";
  const sending = state.kind === "sending";
  // A call that failed may have stored the answer without the case being
  // told. The same answer sent again is safe, and is what tells it; the
  // other answer would be refused and the first one never told. So from
  // then on only the same answer can be sent, also once the server shows
  // the page moved on, until a call settles it.
  const mustRepeat =
    state.kind === "failed" && mayBeStored(state.error)
      ? state.decision
      : state.kind === "sending"
        ? state.repeating
        : null;

  function send(decision: Decision) {
    if (sending) {
      return;
    }
    setState({ kind: "sending", repeating: mustRepeat });
    decidePage(caseId, page.page_id, decision).then(
      () => {
        setState({ kind: "answered" });
        onAnswered();
      },
      (error: unknown) => setState({ kind: "failed", decision, error }),
    );
  }

  if (state.kind === "answered") {
    // Until the next read of the case shows the page's new status.
    return awaiting ? <p role="status">{strings.decision.saved}</p> : null;
  }
  if (!awaiting && mustRepeat === null) {
    return null;
  }
  return (
    <div role="group" aria-label={strings.decision.answerFor(page.page_number)}>
      {awaiting && <p>{promptText(classification)}</p>}
      {mustRepeat === null ? (
        <p>
          <button
            type="button"
            disabled={sending}
            aria-label={strings.decision.discardFor(page.page_number)}
            onClick={() => send("discard")}
          >
            {strings.decision.discard}
          </button>{" "}
          <button
            type="button"
            disabled={sending}
            aria-label={strings.decision.keepFor(page.page_number)}
            onClick={() => send("keep")}
          >
            {strings.decision.keep}
          </button>
        </p>
      ) : (
        <p>
          <button
            type="button"
            disabled={sending}
            aria-label={strings.decision.tryAgainFor(page.page_number)}
            onClick={() => send(mustRepeat)}
          >
            {strings.decision.tryAgain}
          </button>
        </p>
      )}
      {sending && <p role="status">{strings.decision.sending}</p>}
      {state.kind === "failed" && <ErrorMessage error={state.error} />}
    </div>
  );
}
