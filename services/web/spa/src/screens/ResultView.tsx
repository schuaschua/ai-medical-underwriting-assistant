import { useEffect, useId, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import {
  ApiError,
  getDocumentFile,
  getPageBoxes,
  getRule,
} from "../api/client";
import type {
  CaseProgress,
  Fact,
  FactList,
  PageList,
  Reason,
  RuleText,
  VerdictRun,
  VerdictRunList,
} from "../api/contracts.gen";
import {
  auditTrailPath,
  CASE_PARAMETER,
  parseCaseId,
} from "../audit/auditPath";
import { CASE_LIST_PATH } from "../cases/caseList";
import { AgentSteps } from "../components/AgentSteps";
import { ErrorMessage } from "../components/ErrorMessage";
import {
  CitationStatus,
  PdfDocument,
  type CitedQuote,
} from "../components/PdfDocument";
import { judge, useResult, type Part } from "../result/result";
import { percentage, strings } from "../strings";
import "./ResultView.css";

/** The words for a key of a table of strings, or the key itself when there are none. */
function worded(table: Readonly<Record<string, string>>, key: string): string {
  // Own keys only: "constructor" must not find a prototype member.
  return Object.hasOwn(table, key) ? table[key]! : key;
}

function hasCode(error: unknown, code: string): boolean {
  return error instanceof ApiError && error.code === code;
}

/** A citation that was followed: its page, and its boxes as far as they are read. */
type Citation = CitedQuote;

// --- The document -----------------------------------------------------------

type FileState =
  | { kind: "reading" }
  | { kind: "read"; file: Blob }
  | { kind: "failed"; error: unknown };

/**
 * What is said of a citation while no document is on screen to show it on:
 * the highlight could not be shown. It is never claimed.
 */
function NoHighlight({ cited }: { cited: Citation | null }) {
  return <CitationStatus cited={cited} outcome="fault" />;
}

/**
 * The redacted PDF of one document (AD-21). Its bytes are read through the
 * API client, which sends the role header (AD-9), and handed to the
 * renderer: no element of the page fetches the file by itself.
 */
function DocumentFile({
  documentId,
  cited,
}: {
  documentId: string;
  cited: Citation | null;
}) {
  const [state, setState] = useState<FileState>({ kind: "reading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    getDocumentFile(documentId).then(
      (file) => {
        if (current) setState({ kind: "read", file });
      },
      (error: unknown) => {
        if (current) setState({ kind: "failed", error });
      },
    );
    return () => {
      current = false;
    };
  }, [documentId, attempt]);

  if (state.kind === "reading") {
    return (
      <>
        <NoHighlight cited={cited} />
        <p role="status">{strings.result.documentReading}</p>
      </>
    );
  }
  if (state.kind === "failed") {
    return (
      <>
        <NoHighlight cited={cited} />
        {hasCode(state.error, "not_redacted") ? (
          <p role="alert">{strings.result.documentNotReady}</p>
        ) : (
          <>
            <p role="alert">{strings.result.documentFault}</p>
            <ErrorMessage error={state.error} />
          </>
        )}
        <p>
          <button
            type="button"
            onClick={() => {
              setState({ kind: "reading" });
              setAttempt(attempt + 1);
            }}
          >
            {strings.result.documentTryAgain}
          </button>
        </p>
      </>
    );
  }
  return <PdfDocument file={state.file} cited={cited} />;
}

/** The left pane: the redacted document, and in words where a cited quote is. */
function DocumentPane({
  pages,
  cited,
}: {
  pages: Part<PageList>;
  cited: Citation | null;
}) {
  const headingId = useId();
  // A case has one document: every page names it.
  const documentId =
    pages.kind === "read" ? (pages.value.pages[0]?.document_id ?? null) : null;
  return (
    <section className="result-document" aria-labelledby={headingId}>
      <h3 id={headingId}>{strings.result.documentHeading}</h3>
      {/* Where a cited quote is, is said in words by whatever shows the
          document, or says here that there is none to show it on. */}
      {pages.kind === "failed" ? (
        <>
          <NoHighlight cited={cited} />
          <p role="alert">
            {strings.result.partFault(strings.result.pagesFaultPart)}
          </p>
        </>
      ) : documentId === null ? (
        // No pages yet: the document has not been redacted.
        <>
          <NoHighlight cited={cited} />
          <p>{strings.result.documentNotReady}</p>
        </>
      ) : (
        <DocumentFile key={documentId} documentId={documentId} cited={cited} />
      )}
    </section>
  );
}

// --- Citations ----------------------------------------------------------------

/**
 * A fact's citation. A fact the server found on its page has a control that
 * shows the quote there. One it did not find is flagged, with its page
 * stated, and offers no highlight. Whether a quote was found is the
 * server's answer (`quote_verified`); nothing here checks a quote.
 */
function FactCitation({
  fact,
  onCite,
}: {
  fact: Fact;
  onCite: (fact: Fact) => void;
}) {
  if (
    !fact.quote_verified ||
    fact.quote_start === null ||
    fact.quote_end === null
  ) {
    return <strong>{strings.result.quoteNotFound(fact.page_number)}</strong>;
  }
  return (
    <button
      type="button"
      aria-label={strings.result.citeFor(fact.statement, fact.page_number)}
      onClick={() => onCite(fact)}
    >
      {strings.result.cite(fact.page_number)}
    </button>
  );
}

// --- The rule panel -----------------------------------------------------------

type RuleState =
  | { kind: "reading" }
  | { kind: "read"; rule: RuleText }
  | { kind: "failed"; error: unknown };

/** The manual's text of one rule, with its impairment and manual page, as text. */
function RulePanel({
  ruleId,
  onClose,
}: {
  ruleId: string;
  onClose: () => void;
}) {
  const [state, setState] = useState<RuleState>({ kind: "reading" });
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);

  // The panel opens beside what was chosen: focus goes to it, so a keyboard
  // or screen reader user is where the rule is.
  useEffect(() => {
    heading.current?.focus();
  }, []);

  useEffect(() => {
    let current = true;
    getRule(ruleId).then(
      (rule) => {
        if (current) setState({ kind: "read", rule });
      },
      (error: unknown) => {
        if (current) setState({ kind: "failed", error });
      },
    );
    return () => {
      current = false;
    };
  }, [ruleId]);

  return (
    <aside aria-labelledby={headingId}>
      <h4 id={headingId} ref={heading} tabIndex={-1}>
        {strings.result.ruleHeading(ruleId)}
      </h4>
      {state.kind === "reading" && (
        <p role="status">{strings.result.ruleReading}</p>
      )}
      {state.kind === "failed" &&
        (hasCode(state.error, "not_found") ? (
          <p role="alert">{strings.result.ruleNotInManual}</p>
        ) : (
          <ErrorMessage error={state.error} />
        ))}
      {state.kind === "read" && (
        <>
          <p>{strings.result.ruleImpairment(state.rule.impairment)}</p>
          <p>{strings.result.ruleManualPage(state.rule.manual_page)}</p>
          {/* security rule 22: the manual's text is text, never HTML. */}
          <p className="result-rule-text">{state.rule.text}</p>
        </>
      )}
      <p>
        <button type="button" onClick={onClose}>
          {strings.result.ruleClose}
        </button>
      </p>
    </aside>
  );
}

// --- The verdict and its reasons ----------------------------------------------

/** A run's verdict in plain words, with its loading when it is loaded. */
function verdictText(run: VerdictRun): string {
  if (run.verdict === null) {
    return "";
  }
  if (run.verdict === "loaded") {
    return run.loading_pct === null
      ? strings.result.loadedWithoutLoading
      : strings.result.loaded(run.loading_pct);
  }
  return worded(strings.result.verdict, run.verdict);
}

/** A reason's effect in plain words, with its debit when it has one. */
function effectText(reason: Reason): string {
  if (reason.effect === "debit") {
    return reason.debit_pct === null
      ? strings.result.debitWithoutFigure
      : strings.result.debit(reason.debit_pct);
  }
  return worded(strings.result.effect, reason.effect);
}

/**
 * One reason: its rule, its effect, and the facts it cites, each with its
 * page, its quote and its citation. `facts` is null when the case's facts
 * could not be read: that is said as what it is, not of each fact.
 */
function ReasonItem({
  reason,
  facts,
  onCite,
  onOpenRule,
}: {
  reason: Reason;
  facts: readonly Fact[] | null;
  onCite: (fact: Fact) => void;
  onOpenRule: (ruleId: string, opener: HTMLButtonElement) => void;
}) {
  return (
    <li>
      <button
        type="button"
        aria-label={strings.result.openRule(reason.rule_id)}
        onClick={(click) => onOpenRule(reason.rule_id, click.currentTarget)}
      >
        {strings.result.rule(reason.rule_id)}
      </button>{" "}
      {effectText(reason)}
      <br />
      {facts === null ? (
        strings.result.citedFactsUnread
      ) : (
        <>
          {strings.result.citedFacts}
          <ul>
            {reason.fact_ids.map((factId, index) => {
              const fact = facts.find((listed) => listed.fact_id === factId);
              return (
                // The position too: an answer may name a fact twice.
                <li key={`${index}:${factId}`}>
                  {fact === undefined ? (
                    // Not shown as a finding of its own: it has no page or quote here.
                    strings.result.factNotListed(factId)
                  ) : (
                    <>
                      {fact.statement} <q>{fact.quote}</q>{" "}
                      <FactCitation fact={fact} onCite={onCite} />
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </li>
  );
}

/**
 * The run on screen: its label, its verdict, its confidence, why it refers,
 * and its reasons. Everything is the run's own payload (AD-10): the screen
 * adds no verdict, no loading and no control that would decide anything.
 */
function RunOnScreen({
  run,
  facts,
  onCite,
}: {
  run: VerdictRun;
  facts: readonly Fact[] | null;
  onCite: (fact: Fact) => void;
}) {
  const [openRule, setOpenRule] = useState<string | null>(null);
  // The control the open rule was chosen with: focus returns to it.
  const opener = useRef<HTMLButtonElement | null>(null);
  // Story 2.8: whether the agent's steps of this run are open under it.
  const [stepsOpen, setStepsOpen] = useState(false);
  const stepsOpener = useRef<HTMLButtonElement | null>(null);
  return (
    <>
      <p>{strings.result.madeWith(run.retriever_config)}</p>
      {/* AD-10: the label, exactly as the run's payload carries it. */}
      <p>
        <strong>{run.label}</strong>
      </p>
      <p>
        <button
          type="button"
          ref={stepsOpener}
          aria-expanded={stepsOpen}
          onClick={() => setStepsOpen((open) => !open)}
        >
          {strings.steps.openFromResult}
        </button>
      </p>
      {run.status === "running" && (
        <p role="status">{strings.result.runRunning}</p>
      )}
      {run.status === "failed" && (
        <p role="alert">
          {run.error_code === null
            ? strings.result.runFailedNoReason
            : strings.result.runFailed(
                worded(strings.audit.failure, run.error_code),
              )}
        </p>
      )}
      {run.verdict !== null && (
        <>
          <p>
            <strong>{verdictText(run)}</strong>
          </p>
          <p>
            {run.confidence === null
              ? strings.result.noConfidence
              : strings.result.confidence(percentage(run.confidence))}
          </p>
          {run.system_reasons.length > 0 && (
            <>
              <h4>{strings.result.systemReasonsHeading}</h4>
              <ul>
                {run.system_reasons.map((code, index) => (
                  <li key={`${index}:${code}`}>
                    {worded(strings.result.systemReason, code)}
                  </li>
                ))}
              </ul>
            </>
          )}
          <h3>{strings.result.reasonsHeading}</h3>
          {run.reasons.length === 0 ? (
            <p>{strings.result.noReasons}</p>
          ) : (
            <div className="result-reasons">
              <ul>
                {run.reasons.map((reason, index) => (
                  <ReasonItem
                    // The position too: two reasons may cite one rule.
                    key={`${index}:${reason.rule_id}`}
                    reason={reason}
                    facts={facts}
                    onCite={onCite}
                    onOpenRule={(ruleId, button) => {
                      opener.current = button;
                      setOpenRule(ruleId);
                    }}
                  />
                ))}
              </ul>
              {openRule !== null && (
                // A new rule is a new panel: nothing of the one before stays.
                <RulePanel
                  key={openRule}
                  ruleId={openRule}
                  onClose={() => {
                    setOpenRule(null);
                    opener.current?.focus();
                  }}
                />
              )}
            </div>
          )}
        </>
      )}
      {stepsOpen && (
        // AD-15: the searches and rule reads behind this run, read only.
        <AgentSteps
          runId={run.verdict_run_id}
          facts={facts}
          headingLevel={4}
          onClose={() => {
            setStepsOpen(false);
            stepsOpener.current?.focus();
          }}
        />
      )}
    </>
  );
}

/** The verdict pane: which run is on screen, a way to pick another, and that run. */
function VerdictPane({
  runs,
  facts,
  onCite,
}: {
  runs: Part<VerdictRunList>;
  facts: readonly Fact[] | null;
  onCite: (fact: Fact) => void;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const pickerId = useId();

  if (runs.kind === "failed") {
    return (
      <p role="alert">
        {strings.result.partFault(strings.result.runsFaultPart)}
      </p>
    );
  }
  const listed = runs.value.verdict_runs;
  // The run that was picked, or the first the server lists.
  const run = listed.find((one) => one.verdict_run_id === picked) ?? listed[0];
  if (run === undefined) {
    return <p>{strings.result.noRun}</p>;
  }
  return (
    <>
      {listed.length > 1 && (
        <p>
          <label htmlFor={pickerId}>{strings.result.runPicker}</label>{" "}
          <select
            id={pickerId}
            value={run.verdict_run_id}
            onChange={(change) => setPicked(change.target.value)}
          >
            {listed.map((one) => (
              <option key={one.verdict_run_id} value={one.verdict_run_id}>
                {strings.result.runOption(
                  one.retriever_config,
                  worded(strings.result.runStatus, one.status),
                )}
              </option>
            ))}
          </select>
        </p>
      )}
      {runs.value.has_more && <p>{strings.result.moreRuns}</p>}
      {/* A new run is a new pane: an opened rule does not stay for another run. */}
      <RunOnScreen
        key={run.verdict_run_id}
        run={run}
        facts={facts}
        onCite={onCite}
      />
    </>
  );
}

// --- The facts ----------------------------------------------------------------

/** The case's facts in the order the server lists them, each with its quote and citation. */
function FactsPane({
  facts,
  onCite,
}: {
  facts: Part<FactList>;
  onCite: (fact: Fact) => void;
}) {
  if (facts.kind === "failed") {
    return (
      <p role="alert">
        {strings.result.partFault(strings.result.factsFaultPart)}
      </p>
    );
  }
  if (facts.value.facts.length === 0) {
    return <p>{strings.result.noFacts}</p>;
  }
  return (
    <table aria-label={strings.result.factsTable}>
      <thead>
        <tr>
          <th scope="col">{strings.result.factColumn}</th>
          <th scope="col">{strings.result.quoteColumn}</th>
          <th scope="col">{strings.result.citationColumn}</th>
        </tr>
      </thead>
      <tbody>
        {facts.value.facts.map((fact, index) => (
          <tr key={`${index}:${fact.fact_id}`}>
            {/* security rule 22: the model's words are text, never HTML. */}
            <th scope="row">{fact.statement}</th>
            <td>
              <q>{fact.quote}</q>
            </td>
            <td>
              <FactCitation fact={fact} onCite={onCite} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- The screen ---------------------------------------------------------------

/**
 * What the case is doing, whether this screen still reads, and why the case
 * failed if it did, as the server reports it. "Finished" is said only when
 * the reading has really stopped with everything in hand.
 */
function CaseLine({
  progress,
  reading,
}: {
  progress: CaseProgress;
  reading: "following" | "finished" | "gave up";
}) {
  const status = progress.case_status;
  return (
    <>
      <p>
        {strings.result.caseStatus(worded(strings.caseStatus, status))}{" "}
        {reading === "finished"
          ? strings.result.finished
          : reading === "following"
            ? strings.result.following
            : ""}
      </p>
      {reading === "gave up" && <p role="alert">{strings.result.gaveUp}</p>}
      {status === "failed" && (
        <p role="alert">
          {progress.error_code == null
            ? strings.result.caseFailedNoReason
            : strings.result.caseFailed(
                worded(strings.audit.failure, progress.error_code),
              )}
        </p>
      )}
    </>
  );
}

function isStale(part: Part<unknown>): boolean {
  return part.kind === "read" && part.staleError !== null;
}

/** The result of one case: the document on the left; the verdict, the reasons and the facts on the right. */
function Result({ caseId }: { caseId: string }) {
  const { state, refresh } = useResult(caseId);
  const [cited, setCited] = useState<Citation | null>(null);
  // Counts the citations followed: an answer for an earlier one is dropped.
  const requests = useRef(0);
  const checkAgain = (
    <p>
      <button type="button" onClick={refresh}>
        {strings.result.checkAgain}
      </button>
    </p>
  );

  /**
   * Show a fact's quote on its page. The boxes are `intake`'s, asked for
   * with the fact's own offsets (AD-14); the page is shown whether or not
   * they can be read, and whatever shows the document says which it was.
   */
  function cite(fact: Fact) {
    if (fact.quote_start === null || fact.quote_end === null) {
      return;
    }
    requests.current += 1;
    const request = requests.current;
    const pageNumber = fact.page_number;
    setCited({ pageNumber, request, boxes: "reading" });
    getPageBoxes(
      fact.page_id,
      pageNumber,
      fact.quote_start,
      fact.quote_end,
    ).then(
      (boxes) => {
        if (requests.current === request) {
          setCited({ pageNumber, request, boxes });
        }
      },
      () => {
        if (requests.current === request) {
          setCited({ pageNumber, request, boxes: "failed" });
        }
      },
    );
  }

  if (state.kind === "reading") {
    return <p role="status">{strings.result.reading}</p>;
  }
  if (state.kind === "unreadable") {
    return (
      <>
        {hasCode(state.error, "not_found") ? (
          <p role="alert">{strings.result.unknownCase}</p>
        ) : (
          <ErrorMessage error={state.error} />
        )}
        {checkAgain}
      </>
    );
  }

  const { progress, pages, facts, runs } = state.value;
  const listedFacts = facts.kind === "read" ? facts.value.facts : null;
  // Whether the screen still reads by itself, by the rule the reading uses.
  const judgement = judge(state.value);
  return (
    <>
      <p>
        <code>{caseId}</code>
      </p>
      <CaseLine
        progress={progress}
        reading={
          judgement !== "settled" || state.staleError !== null
            ? "following"
            : state.value.failedReads > 0
              ? "gave up"
              : "finished"
        }
      />
      <p>
        <Link to={auditTrailPath(caseId)}>{strings.result.toTrail}</Link>
      </p>
      {(state.staleError !== null ||
        isStale(pages) ||
        isStale(facts) ||
        isStale(runs)) && <p role="alert">{strings.result.stale}</p>}
      {checkAgain}
      <div className="result-panes">
        <DocumentPane pages={pages} cited={cited} />
        <div className="result-findings">
          <section aria-label={strings.result.verdictHeading}>
            <h3>{strings.result.verdictHeading}</h3>
            <VerdictPane runs={runs} facts={listedFacts} onCite={cite} />
          </section>
          <section aria-label={strings.result.factsHeading}>
            <h3>{strings.result.factsHeading}</h3>
            <FactsPane facts={facts} onCite={cite} />
          </section>
        </div>
      </div>
    </>
  );
}

/**
 * The underwriter's result view (story 2.7, AD-10, AD-14). The address
 * names the case. The screen shows what the services hold and decides
 * nothing: the verdict is a suggestion, and nothing here approves, overrides
 * or changes it.
 */
export function ResultView() {
  const [parameters] = useSearchParams();
  const named = parameters.get(CASE_PARAMETER);
  const caseId = named === null ? null : parseCaseId(named);

  return (
    <section>
      <h2>{strings.result.heading}</h2>
      <p>{strings.result.intro}</p>
      {caseId === null ? (
        <>
          {named === null ? (
            <p>{strings.result.noCase}</p>
          ) : (
            <p role="alert">{strings.result.notACaseId}</p>
          )}
          <p>
            <Link to={CASE_LIST_PATH}>{strings.result.toCases}</Link>
          </p>
        </>
      ) : (
        // A new case is a new result: nothing of the one before it stays.
        <Result key={caseId} caseId={caseId} />
      )}
    </section>
  );
}
