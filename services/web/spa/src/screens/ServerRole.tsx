import { useEffect, useState } from "react";
import { getMe, type Me } from "../api/client";
import { ErrorMessage } from "../components/ErrorMessage";
import { strings } from "../strings";

type State =
  | { status: "loading" }
  | { status: "loaded"; me: Me }
  | { status: "failed"; error: unknown };

/** Shows the role as the server read it from this browser's calls. */
export function ServerRole() {
  const [state, setState] = useState<State>({ status: "loading" });

  useEffect(() => {
    let current = true;
    getMe().then(
      (me) => {
        if (current) setState({ status: "loaded", me });
      },
      (error: unknown) => {
        if (current) setState({ status: "failed", error });
      },
    );
    return () => {
      current = false;
    };
  }, []);

  if (state.status === "loading") {
    return <p>{strings.home.checking}</p>;
  }
  if (state.status === "failed") {
    return <ErrorMessage error={state.error} />;
  }
  return <p>{strings.home.confirmed(strings.roles[state.me.role])}</p>;
}
