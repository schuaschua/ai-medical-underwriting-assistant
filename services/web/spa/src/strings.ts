// Every piece of user-facing text (coding-style.md rule 18).
import type {
  CaseStatus,
  DemoRole,
  ErrorCode,
  PageStatus,
  PageType,
  QueuedBy,
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
  },
  home: {
    customerHeading: "Customer home",
    customerIntro: "Use “Upload a document” to send us a PDF.",
    underwriterHeading: "Underwriter home",
    underwriterIntro:
      "Use “Triage queue” to accept or deny the pages that are waiting.",
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
