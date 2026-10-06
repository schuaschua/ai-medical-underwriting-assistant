import { setRole } from "../role/roleStore";
import { DEMO_ROLES, strings } from "../strings";

/** First visit: no role screen is shown until a role is chosen. */
export function ChooseRole() {
  return (
    <section>
      <h2>{strings.chooseRole.heading}</h2>
      <p>{strings.chooseRole.hint}</p>
      {DEMO_ROLES.map((role) => (
        <p key={role}>
          <button type="button" onClick={() => setRole(role)}>
            {strings.chooseRole.continueAs(strings.roles[role])}
          </button>
        </p>
      ))}
    </section>
  );
}
