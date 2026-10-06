import { Component, type ReactNode } from "react";
import { strings } from "../strings";

// The one class component: React offers error boundaries only as classes
// (coding-style.md rule 14 otherwise asks for function components).
export class ErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  render(): ReactNode {
    if (this.state.failed) {
      return (
        <div role="alert">
          <p>{strings.errors.screenFailed}</p>
        </div>
      );
    }
    return this.props.children;
  }
}
