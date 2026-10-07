// Every piece of user-facing text (coding-style.md rule 18).
import type {
  AuditAction,
  CaseStatus,
  DemoRole,
  ErrorCode,
  PageStatus,
  PageType,
  QueuedBy,
  ReasonEffect,
  RouteDetail,
  Service,
  StageStatus,
  SystemReason,
  Verdict,
} from "./api/contracts.gen";

/** The largest upload, in MB, as the wording states it. The server enforces it. */
export const MAX_UPLOAD_MB = 10;
const TOO_LARGE = `The file is larger than ${MAX_UPLOAD_MB} MB. Choose a smaller PDF.`;

/**
 * A confidence as the server gives it, a number from 0 to 1, worded as a
 * whole percentage such as "96%". Rounded down, so that only a confidence of
 * exactly 1 reads "100%".
 */
export function percentage(share: number): string {
  if (share >= 1) {
    return "100%";
  }
  // The small addition undoes binary rounding: 0.29 times 100 is 28.99…
  return `${Math.min(99, Math.max(0, Math.floor(share * 100 + 1e-9)))}%`;
}

/**
 * A share the server recorded, a number from 0 to 1, worded as the exact
 * percentage: nothing is rounded away, so 92.5 in a hundred reads "92.5%".
 */
export function exactPercentage(share: number): string {
  // Six decimals keep every digit a setting has and drop binary noise.
  return `${Number((share * 100).toFixed(6))}%`;
}

export const strings = {
  appTitle: "AI Medical Underwriting Assistant",
  demoNotice: "Demo with synthetic data. There is no sign-in.",
  roles: {
    customer: "Customer",
    underwriter: "Underwriter",
  } satisfies Record<DemoRole, string>,
  chooseRole: {
    heading: "Who are you acting as?",
    hint: "You can switch at any time.",
    continueAs: (role: string) => `Continue as ${role}`,
  },
  roleSwitcher: {
    legend: "Acting as",
  },
  navigation: {
    label: "Screens",
    home: "Home",
    upload: "Upload a document",
    triage: "Triage queue",
    cases: "Cases",
    audit: "Audit trail",
  },
  home: {
    customerHeading: "Customer home",
    customerIntro: "Use “Upload a document” to send us a PDF.",
    underwriterHeading: "Underwriter home",
    underwriterIntro:
      "Use “Triage queue” to accept or deny the pages that are waiting, “Cases” to find a case and open its result, and “Audit trail” to read what was done to it.",
    checking: "Checking with the server…",
    confirmed: (role: string) => `The server sees you as: ${role}.`,
  },
  upload: {
    heading: "Upload a document",
    intro: `Choose one PDF of up to ${MAX_UPLOAD_MB} MB. Use synthetic documents only.`,
    fileLabel: "PDF document",
    submit: "Upload",
    uploading: "Uploading…",
    received: "Your document was received. Starting its case…",
    uploaded: "Your document was uploaded and its case has started.",
    notStarted:
      "Your document was received, but its case has not started. Use “Start it again” in the list below.",
    // A document that could not be redacted is never used unredacted
    // (AD-21): the customer uploads it again.
    failed:
      "Your document could not be processed. Please upload the document again.",
    casesHeading: "Cases uploaded in this session",
    noCases: "No documents uploaded yet.",
    caseColumn: "Case",
    statusColumn: "Status",
    caseChecking: "Checking…",
    caseStarting: "Starting…",
    caseNotStarted: "Received, not started",
    caseUnreadable: "Its status could not be read",
    caseFailed: "Failed. Please upload the document again.",
    checkAgain: "Check again",
    checkAgainFor: (caseId: string) => `Check case ${caseId} again`,
    startAgain: "Start it again",
    startAgainFor: (caseId: string) => `Start case ${caseId} again`,
    pagesOf: (caseId: string) => `Pages of case ${caseId}`,
    pageBadge: (pageNumber: number, status: string) =>
      `Page ${pageNumber}: ${status}`,
  },
  // A time is shown in the viewer's own; this words the UTC time beside it.
  time: {
    utc: (date: string, time: string) => `${date} ${time} UTC`,
  },
  caseStatus: {
    running: "Running",
    awaiting_human: "Waiting for a decision",
    completed: "Completed",
    failed: "Failed",
  } satisfies Record<CaseStatus, string>,
  // A page's status is the server's; this only words it, for the customer
  // who reads it on the upload screen.
  pageStatus: {
    uploaded: "Received",
    classified: "Classified",
    awaiting_customer: "Needs your answer",
    awaiting_triage: "Waiting for the underwriter",
    extracting: "Being read",
    extracted: "Read",
    discarded: "Discarded",
    denied: "Denied",
    failed: "Failed",
  } satisfies Record<PageStatus, string>,
  // Plain words for what the classifier took a page for, as the prompt to
  // the customer names it: "This looks like <these words> (96%)."
  pageType: {
    lab_report: "a laboratory report",
    attending_physician_statement: "a doctor's statement",
    application_form: "an application form",
    id_document: "an identity document",
    invoice: "an invoice or bill",
    other: "a page that is not a medical document",
  } satisfies Record<PageType, string>,
  decision: {
    prompt: (pageType: string, confidence: string) =>
      `This looks like ${pageType} (${confidence}). Discard or keep?`,
    // When what the classifier said could not be read, or names a type this
    // build has no words for.
    promptWithoutType: "This page needs your answer. Discard or keep?",
    answerFor: (pageNumber: number) => `Your answer for page ${pageNumber}`,
    discard: "Discard",
    discardFor: (pageNumber: number) => `Discard page ${pageNumber}`,
    keep: "Keep",
    keepFor: (pageNumber: number) => `Keep page ${pageNumber}`,
    sending: "Saving your answer…",
    saved: "Your answer was saved.",
    tryAgain: "Try again",
    tryAgainFor: (pageNumber: number) =>
      `Try again to save your answer for page ${pageNumber}`,
  },
  triage: {
    heading: "Triage queue",
    intro:
      "Pages the classifier was not sure of, and pages a customer kept. Accept a page to have it read, or deny it.",
    reading: "Reading the queue…",
    empty: "Nothing is waiting.",
    more: "More pages are waiting than are shown here. They appear as these are decided.",
    // A read failed after the queue was shown: what is shown may be out of date.
    stale:
      "The queue could not be read again. What is shown may be out of date.",
    checkAgain: "Check again",
    tableLabel: "Pages waiting for a decision",
    thumbnailColumn: "Page",
    caseColumn: "Case",
    readingColumn: "What the classifier said",
    decisionColumn: "Decision",
    pageOf: (pageNumber: number) => `Page ${pageNumber}`,
    thumbnailOf: (pageNumber: number, caseId: string) =>
      `Thumbnail of page ${pageNumber} of case ${caseId}`,
    thumbnailLoading: "Loading the picture…",
    thumbnailUnavailable: "The picture could not be shown.",
    looksLike: (pageType: string, confidence: string) =>
      `Looks like ${pageType} (${confidence}).`,
    reasonLabel: "Reason:",
    noReading: "What the classifier said of this page could not be read.",
    queuedBy: {
      gate: "Here because the classifier was not sure.",
      customer: "Here because the customer kept it.",
    } satisfies Record<QueuedBy, string>,
    decisionFor: (pageNumber: number, caseId: string) =>
      `Decision for page ${pageNumber} of case ${caseId}`,
    accept: "Accept",
    acceptFor: (pageNumber: number, caseId: string) =>
      `Accept page ${pageNumber} of case ${caseId}`,
    deny: "Deny",
    denyFor: (pageNumber: number, caseId: string) =>
      `Deny page ${pageNumber} of case ${caseId}`,
    sending: "Saving your decision…",
    saved: "Your decision was saved.",
    decidedElsewhere:
      "This page was already decided. It will leave the queue shortly.",
    tryAgain: "Try again",
    tryAgainFor: (pageNumber: number, caseId: string) =>
      `Try again to save your decision for page ${pageNumber} of case ${caseId}`,
  },
  // The underwriter's list of cases. Which cases it holds, their order and
  // every number in it are the server's.
  cases: {
    heading: "Cases",
    intro:
      "Every case that was started, newest first. Open a case's result to read what the AI suggests for it, or its audit trail to read what was done to it.",
    reading: "Reading the cases…",
    empty: "No cases yet.",
    more: "More cases exist than are shown here. Only the newest are listed.",
    // A read failed after the list was shown: what is shown may be out of date.
    stale:
      "The cases could not be read again. What is shown may be out of date.",
    checkAgain: "Check again",
    tableLabel: "Cases, newest first",
    caseColumn: "Case",
    statusColumn: "Status",
    startedColumn: "Started",
    pagesColumn: "Pages",
    waitingColumn: "Pages waiting for a person",
    trailColumn: "Audit trail",
    trail: "Audit trail",
    trailFor: (caseId: string) => `Audit trail of case ${caseId}`,
    resultColumn: "Result",
    result: "Result",
    resultFor: (caseId: string) => `Result of case ${caseId}`,
  },
  // The underwriter's result view (story 2.7). Every verdict, loading,
  // confidence and "found on the page" shown is the server's own; these are
  // only the words for them.
  result: {
    heading: "Result",
    intro:
      "The redacted document beside what the AI suggests for it. Follow a citation to see a quote on its page, and a rule to read it in the manual.",
    noCase: "Open a case's result from the list of cases.",
    toCases: "Go to the cases",
    notACaseId: "The address does not name a case.",
    reading: "Reading the result…",
    unknownCase: "No such case.",
    resultOf: (caseId: string) => `Result of case ${caseId}`,
    caseStatus: (status: string) => `Case status: ${status}.`,
    following: "This screen reads again as the case moves.",
    finished: "The case is finished, so this screen does not read again.",
    // The case is final, but a part kept failing: the reading was given up.
    gaveUp:
      "Part of the result could not be read, and this screen has stopped trying. Choose “Check again” to read it again.",
    caseFailed: (reason: string) => `The case failed. ${reason}`,
    caseFailedNoReason: "The case failed. No reason was recorded.",
    // A read failed after the result was shown: what is shown may be out of date.
    stale:
      "The result could not be read again. What is shown may be out of date.",
    checkAgain: "Check again",
    toTrail: "Audit trail of this case",
    // One part of the screen could not be read; the other parts are shown.
    partFault: (part: string) => `${part} could not be read.`,
    documentHeading: "Redacted document",
    documentReading: "Reading the document…",
    documentNotReady: "The document is not ready yet.",
    documentFault: "The document could not be shown.",
    documentTryAgain: "Try to read the document again",
    documentLoading: "Opening the document…",
    pageCaption: (pageNumber: number, pageCount: number) =>
      `Page ${pageNumber} of ${pageCount}`,
    pageLoading: "Drawing the page…",
    pageFault: "This page could not be drawn.",
    pageNotDrawnYet: "This page is drawn when it comes into view.",
    // Where a cited quote is: said in words, so the highlight is not the only sign.
    citing: (pageNumber: number) =>
      `Page ${pageNumber}: looking for the quote…`,
    cited: (pageNumber: number) =>
      `Page ${pageNumber}: the quote is highlighted.`,
    citeFault: (pageNumber: number) =>
      `Page ${pageNumber}: the highlight could not be shown.`,
    pagesLabel: "Pages of the redacted document",
    highlightOn: (pageNumber: number) =>
      `Highlight of the quote on page ${pageNumber}`,
    verdictHeading: "Suggested verdict",
    noRun: "No suggestion yet.",
    runsFaultPart: "The suggestion",
    runRunning: "The suggestion is being made.",
    runFailed: (reason: string) => `This run failed. ${reason}`,
    runFailedNoReason: "This run failed. No reason was recorded.",
    madeWith: (row: string) => `Made with retrieval row ${row}.`,
    runPicker: "Suggestion on screen, by retrieval row",
    runOption: (row: string, status: string) => `${row} (${status})`,
    moreRuns: "This case has more runs than are listed here.",
    runStatus: {
      running: "running",
      done: "done",
      failed: "failed",
    } satisfies Record<StageStatus, string>,
    verdict: {
      standard: "Standard rates",
      decline: "Decline",
      refer: "Refer to underwriter",
    } satisfies Record<Exclude<Verdict, "loaded">, string>,
    loaded: (loadingPct: number) => `Loaded premium, +${loadingPct} %`,
    // A loaded verdict whose loading the answer does not carry.
    loadedWithoutLoading: "Loaded premium",
    confidence: (confidence: string) => `Confidence: ${confidence}`,
    noConfidence: "The agent gave no confidence.",
    systemReasonsHeading: "Why it is referred",
    systemReason: {
      no_matching_rule: "No rule of the manual matched the facts.",
      conflicting_rules: "The rules that matched do not agree with each other.",
      unverified_quote:
        "A fact the suggestion rests on has a quote that was not found on its page.",
      low_confidence: "The agent was not confident enough of its answer.",
      step_limit:
        "The agent reached the limit on its steps before it answered.",
    } satisfies Record<SystemReason, string>,
    reasonsHeading: "Reasons",
    noReasons: "No reasons were given.",
    rule: (ruleId: string) => `Rule ${ruleId}`,
    openRule: (ruleId: string) => `Read rule ${ruleId} in the manual`,
    effect: {
      none: "No debit",
      decline: "Decline",
    } satisfies Record<Exclude<ReasonEffect, "debit">, string>,
    debit: (debitPct: number) => `Debit +${debitPct} %`,
    debitWithoutFigure: "Debit",
    citedFacts: "Facts it cites:",
    // The facts themselves could not be read: nothing is said about any one of them.
    citedFactsUnread:
      "The facts could not be read, so the facts this reason cites cannot be shown.",
    factNotListed: (factId: string) =>
      `A fact that is not in the list below (${factId}).`,
    ruleHeading: (ruleId: string) => `Rule ${ruleId} in the manual`,
    ruleReading: "Reading the rule…",
    ruleNotInManual: "This rule is not in the manual.",
    ruleImpairment: (impairment: string) => `Impairment: ${impairment}`,
    ruleManualPage: (pageNumber: number) => `Manual page ${pageNumber}`,
    ruleClose: "Close the rule",
    factsHeading: "Facts",
    factsFaultPart: "The facts",
    pagesFaultPart: "The list of pages",
    noFacts: "No facts yet.",
    factsTable: "Facts, in page order",
    factColumn: "Fact",
    quoteColumn: "Quote",
    citationColumn: "Citation",
    cite: (pageNumber: number) => `Page ${pageNumber}`,
    citeFor: (statement: string, pageNumber: number) =>
      `Show the quote for “${statement}” on page ${pageNumber}`,
    // An unverified fact: flagged, with its page stated and nothing to follow.
    quoteNotFound: (pageNumber: number) =>
      `Page ${pageNumber}: quote not found on the page`,
  },
  audit: {
    heading: "Audit trail",
    intro:
      "Every step of a case in the order it was recorded: who or what did it, and when.",
    caseLabel: "Case id",
    caseHint: "Type or paste the id of a case, then choose “Show the trail”.",
    show: "Show the trail",
    notACaseId:
      "That is not a case id. A case id looks like 019a0000-0000-7000-8000-000000000001.",
    reading: "Reading the trail…",
    unknownCase: "No such case.",
    empty: "No events yet.",
    more: "This case has more events than are shown here. Only the first ones are listed.",
    // A read failed after the trail was shown: what is shown may be out of date.
    stale:
      "The trail could not be read again. What is shown may be out of date.",
    checkAgain: "Check again",
    trailOf: (caseId: string) => `Audit trail of case ${caseId}`,
    caseStatus: (status: string) => `Case status: ${status}.`,
    following: "New events appear here as the case moves.",
    // More events exist than one read lists: a later read would list the
    // same first ones, so none is made.
    firstOnly:
      "Only the first events of this case are shown, so the trail is not read again.",
    finished: "The case is finished, so the trail is not read again.",
    timeColumn: "Time",
    actorColumn: "Actor",
    actionColumn: "Action",
    pageColumn: "Page",
    detailColumn: "Detail",
    // A person: the demo role, as recorded.
    human: (role: string) => `${role} (a person)`,
    // An AI step: the service, and what did the work inside it. Both show.
    ai: (service: string, partLabel: string, part: string) =>
      `${service}, ${partLabel} ${part}`,
    // An actor this build does not know: its two parts, and no claim about
    // what the second one is.
    otherActor: (service: string, part: string) => `${service}, ${part}`,
    service: {
      web: "Web service",
      intake: "Intake service",
      classification: "Classification service",
      extraction: "Extraction service",
      retrieval: "Retrieval service",
      verdict: "Verdict service",
      workflow: "Workflow service",
    } satisfies Record<Service, string>,
    // What the second half of an AI actor names, for the services known to
    // call a model deployment, and for `intake`, which calls the redaction
    // service. No other service's second half is given a name.
    actorPart: {
      classification: "model deployment",
      extraction: "model deployment",
      retrieval: "model deployment",
      verdict: "model deployment",
      intake: "redaction service",
    } satisfies Partial<Record<Service, string>>,
    // `workflow` calls no model: its own parts, each in plain words.
    knownActor: {
      "workflow:gate": "Workflow service, rule gate",
      "workflow:case-lifecycle": "Workflow service, the case's lifecycle",
    },
    action: {
      "case.started": "Case started",
      "document.redacted": "Document redacted",
      "page.classified": "Page classified",
      "page.routed": "Page sent on by the gate",
      "page.kept": "Page kept",
      "page.discarded": "Page discarded",
      "page.accepted": "Page accepted",
      "page.denied": "Page denied",
      "facts.extracted": "Facts extracted",
      "verdict.suggested": "Verdict suggested",
      "stage.failed": "Step failed",
      "case.completed": "Case completed",
    } satisfies Record<AuditAction, string>,
    actionOnPage: (action: string, pageNumber: number) =>
      `${action} (page ${pageNumber})`,
    pageOf: (pageNumber: number) => `Page ${pageNumber}`,
    wholeCase: "Whole case",
    // Counts per category of what was redacted; never the values themselves.
    redacted: (counts: string) => `Redacted: ${counts}.`,
    redactedNothing: "Nothing was redacted.",
    redactionCount: (category: string, count: number) => `${category} ${count}`,
    // The route and the confidence the gate asked for, both from the event's
    // detail as the server recorded them; the browser compares nothing.
    routed: (route: string, asked: string) =>
      `Sent to ${route}. Confidence the gate asked for: ${asked}.`,
    route: {
      extracting: "extraction",
      awaiting_customer: "the customer, to keep or discard",
      awaiting_triage: "the underwriter's triage queue",
    } satisfies Record<RouteDetail["route"], string>,
    // Stories 2.5 and 2.6: which retrieval row a suggested verdict was made with.
    suggestedWith: (row: string) => `Made with retrieval row ${row}.`,
    failedBecause: (reason: string) => `Reason: ${reason}`,
    noReason: "No reason was recorded.",
    // Why a step failed, in plain words, for every code of the catalogue.
    // A code this build has no words for is shown as the server named it.
    failure: {
      validation_failed: "The step was given something it could not accept.",
      invalid_role: "The step was called without a valid role.",
      role_not_allowed: "The step was not open to the role that asked.",
      actor_not_human:
        "A decision only a person may make came from something else.",
      not_found: "What the step needed could not be found.",
      method_not_allowed: "The step was called in a way it does not take.",
      file_too_large: "The file was too large.",
      unsupported_file_type: "The file was not a PDF.",
      payload_too_large: "What was sent was too large.",
      unsupported_media_type: "What was sent was not of a type the step takes.",
      too_many_requests: "Too many requests were made at once.",
      in_progress: "The step was still running when it was asked again.",
      not_redacted: "The document had not been redacted yet.",
      not_awaiting_decision: "The page was not waiting for that decision.",
      pages_not_terminal: "Some pages were not finished yet.",
      rule_not_seen: "A rule was asked for that had not been found first.",
      step_limit: "The limit on the agent's steps was reached.",
      retriever_not_available:
        "The way of searching the manual that was asked for is not available yet.",
      stage_timeout: "The step took too long and was stopped.",
      stage_failed: "The step could not be completed.",
      redaction_failed: "The document could not be redacted.",
      invalid_model_output: "The model's answer could not be used.",
      model_unavailable: "The model was not available.",
      upstream_unavailable: "A service the step needed was not available.",
      internal_error: "Something went wrong inside the service.",
    } satisfies Record<ErrorCode, string>,
    linkFromTriage: "Audit trail",
    linkFromTriageFor: (caseId: string) => `Audit trail of case ${caseId}`,
    linkToResult: "Result of this case",
  },
  // A screen whose code is fetched when it is first opened.
  screenLoading: "Opening the screen…",
  notFound: {
    heading: "Page not found",
    body: "This screen does not exist, or is not open to your role.",
    homeLink: "Go to your home screen",
  },
  errors: {
    generic: "Something went wrong. Please try again.",
    unreachable: "The server could not be reached. Please try again.",
    screenFailed:
      "This screen could not be shown. Reload the page to try again.",
    reference: (traceId: string) => `Reference: ${traceId}`,
    byCode: {
      invalid_role: "Choose a role to continue.",
      role_not_allowed: "This action is not open to your role.",
      not_found: "That could not be found.",
      not_awaiting_decision: "This page is no longer waiting for that answer.",
      not_redacted: "The document is not ready yet.",
      method_not_allowed: "That action is not available here.",
      file_too_large: TOO_LARGE,
      payload_too_large: TOO_LARGE,
      unsupported_file_type: "Only PDF files can be uploaded.",
      unsupported_media_type: "Only PDF files can be uploaded.",
      validation_failed:
        "That could not be accepted. Check what you sent and try again.",
      too_many_requests:
        "Too many requests. Please wait a moment and try again.",
      upstream_unavailable:
        "The service is not available right now. Please try again.",
    } satisfies Partial<Record<ErrorCode, string>>,
  },
} as const;

export const DEMO_ROLES = Object.keys(strings.roles) as DemoRole[];
