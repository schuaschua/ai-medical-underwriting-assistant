import { NavLink } from "react-router";
import type { DemoRole } from "../api/contracts.gen";
import { SCREENS } from "../navigation";
import { strings } from "../strings";

/** Lists the screens that exist for the role, and no others. */
export function Navigation({ role }: { role: DemoRole }) {
  return (
    <nav aria-label={strings.navigation.label}>
      <ul>
        {SCREENS[role]
          .filter((screen) => !screen.unlisted)
          .map((screen) => (
            <li key={screen.path}>
              <NavLink to={screen.path}>{screen.label}</NavLink>
            </li>
          ))}
      </ul>
    </nav>
  );
}
