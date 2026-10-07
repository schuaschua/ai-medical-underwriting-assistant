// Every piece of user-facing text (coding-style.md rule 18).
import type { CaseStatus, DemoRole, ErrorCode } from "./api/contracts.gen";

/** The largest upload, in MB, as the wording states it. The server enforces it. */
export const MAX_UPLOAD_MB = 10;
const TOO_LARGE = `The file is larger than ${MAX_UPLOAD_MB} MB. Choose a smaller PDF.`;

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
  },
  home: {
    customerHeading: "Customer home",
    customerIntro: "Use “Upload a document” to send us a PDF.",
    underwriterHeading: "Underwriter home",
    underwriterIntro: "The triage queue will be available here.",
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
  },
  caseStatus: {
    running: "Running",
    awaiting_human: "Waiting for a decision",
    completed: "Completed",
    failed: "Failed",
  } satisfies Record<CaseStatus, string>,
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
