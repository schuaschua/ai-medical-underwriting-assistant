// Every piece of user-facing text (coding-style.md rule 18).
import type { DemoRole, ErrorCode } from "./api/contracts.gen";

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
  },
  home: {
    customerHeading: "Customer home",
    customerIntro: "Uploading a document will be available here.",
    underwriterHeading: "Underwriter home",
    underwriterIntro: "The triage queue will be available here.",
    checking: "Checking with the server…",
    confirmed: (role: string) => `The server sees you as: ${role}.`,
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
    } satisfies Partial<Record<ErrorCode, string>>,
  },
} as const;

export const DEMO_ROLES = Object.keys(strings.roles) as DemoRole[];
