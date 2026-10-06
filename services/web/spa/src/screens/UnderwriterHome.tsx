import { strings } from "../strings";
import { ServerRole } from "./ServerRole";

export function UnderwriterHome() {
  return (
    <section>
      <h2>{strings.home.underwriterHeading}</h2>
      <p>{strings.home.underwriterIntro}</p>
      <ServerRole />
    </section>
  );
}
