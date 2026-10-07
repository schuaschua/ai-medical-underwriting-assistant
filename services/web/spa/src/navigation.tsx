// The screens that exist, per demo role. A story that adds a screen adds it
// here; navigation and routing both read this one table.
import type { ReactElement } from "react";
import type { DemoRole } from "./api/contracts.gen";
import { AUDIT_PATH } from "./audit/auditPath";
import { CASE_LIST_PATH } from "./cases/caseList";
import { AuditTrail } from "./screens/AuditTrail";
import { CaseList } from "./screens/CaseList";
import { CustomerHome } from "./screens/CustomerHome";
import { TriageQueue } from "./screens/TriageQueue";
import { UnderwriterHome } from "./screens/UnderwriterHome";
import { UploadDocument } from "./screens/UploadDocument";
import { strings } from "./strings";

export interface Screen {
  path: string;
  label: string;
  element: ReactElement;
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
  ],
};

export function homePath(role: DemoRole): string {
  return HOME_PATHS[role];
}
