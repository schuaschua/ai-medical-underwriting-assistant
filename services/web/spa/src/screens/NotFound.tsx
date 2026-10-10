import { Link } from "react-router";
import type { DemoRole } from "../api/contracts.gen";
import { homePath } from "../navigation";
import { strings } from "../strings";

export function NotFound({ role }: { role: DemoRole }) {
  return (
    <section>
      <h2>{strings.notFound.heading}</h2>
      <p>{strings.notFound.body}</p>
      <p>
        <Link to={homePath(role)}>{strings.notFound.homeLink}</Link>
      </p>
    </section>
  );
}
