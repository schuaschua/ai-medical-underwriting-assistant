// The screens that exist, per demo role. A story that adds a screen adds it
// here; navigation and routing both read this one table.
import type { ReactElement } from "react";
import type { DemoRole } from "./api/contracts.gen";
import { CustomerHome } from "./screens/CustomerHome";
import { UnderwriterHome } from "./screens/UnderwriterHome";
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
  ],
  underwriter: [
    {
      path: HOME_PATHS.underwriter,
      label: strings.navigation.home,
      element: <UnderwriterHome />,
    },
  ],
};

export function homePath(role: DemoRole): string {
  return HOME_PATHS[role];
}
