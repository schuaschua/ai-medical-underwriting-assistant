// The screens that exist, per demo role. A story that adds a screen adds it
// here; navigation and routing both read this one table.
import { lazy, type ReactElement } from "react";
import type { DemoRole } from "./api/contracts.gen";
import { AUDIT_PATH } from "./audit/auditPath";
import { CASE_LIST_PATH } from "./cases/caseList";
import { RESULT_PATH } from "./result/resultPath";
import { AuditTrail } from "./screens/AuditTrail";
import { CaseList } from "./screens/CaseList";
import { CustomerHome } from "./screens/CustomerHome";
import { TriageQueue } from "./screens/TriageQueue";
import { UnderwriterHome } from "./screens/UnderwriterHome";
import { UploadDocument } from "./screens/UploadDocument";
import { strings } from "./strings";

// The result view draws PDFs, and the library for that is large: the screen
// and the library are fetched when the screen is first opened, so no other
// screen, the customer's included, loads them.
const ResultView = lazy(() =>
  import("./screens/ResultView").then((module) => ({
    default: module.ResultView,
  })),
);

export interface Screen {
  path: string;
  label: string;
  element: ReactElement;
  /** Left out of the navigation: the screen is reached from a case, not by itself. */
  unlisted?: true;
}

const HOME_PATHS = {
  customer: "/customer",
  underwriter: "/underwriter",
} satisfies Record<DemoRole, string>;

export const SCREENS: Record<DemoRole, readonly Screen[]> = {
  customer: [
    {
      path: HOME_PATHS.customer,
      label: strings.navigation.home,
      element: <CustomerHome />,
    },
    {
      path: `${HOME_PATHS.customer}/upload`,
      label: strings.navigation.upload,
      element: <UploadDocument />,
    },
  ],
  underwriter: [
    {
      path: HOME_PATHS.underwriter,
      label: strings.navigation.home,
      element: <UnderwriterHome />,
    },
    {
      path: `${HOME_PATHS.underwriter}/triage`,
      label: strings.navigation.triage,
      element: <TriageQueue />,
    },
    {
      // AD-9: for the underwriter only; the customer has no such screen.
      path: CASE_LIST_PATH,
      label: strings.navigation.cases,
      element: <CaseList />,
    },
    {
      // AD-9: for the underwriter only; the customer has no such screen.
      path: AUDIT_PATH,
      label: strings.navigation.audit,
      element: <AuditTrail />,
    },
    {
      // AD-9: for the underwriter only. Reached from the case list and from
      // a case's audit trail, which name the case in the address.
      path: RESULT_PATH,
      label: strings.result.heading,
      element: <ResultView />,
      unlisted: true,
    },
  ],
};

export function homePath(role: DemoRole): string {
  return HOME_PATHS[role];
}
