import { strings } from "../strings";
import { ServerRole } from "./ServerRole";

export function CustomerHome() {
  return (
    <section>
      <h2>{strings.home.customerHeading}</h2>
      <p>{strings.home.customerIntro}</p>
      <ServerRole />
    </section>
  );
}
