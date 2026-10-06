import type { DemoRole } from "../api/contracts.gen";
import { setRole } from "../role/roleStore";
import { DEMO_ROLES, strings } from "../strings";

export function RoleSwitcher({ role }: { role: DemoRole }) {
  return (
    <fieldset>
      <legend>{strings.roleSwitcher.legend}</legend>
      {DEMO_ROLES.map((option) => (
        <label key={option}>
          <input
            type="radio"
            name="demo-role"
            value={option}
            checked={option === role}
            onChange={() => setRole(option)}
          />
          {strings.roles[option]}
        </label>
      ))}
    </fieldset>
  );
}
