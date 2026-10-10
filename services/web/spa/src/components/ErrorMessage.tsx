import { ApiError, NetworkError } from "../api/client";
import { strings } from "../strings";

function textFor(error: unknown): string {
  if (error instanceof NetworkError) {
    return strings.errors.unreachable;
  }
  if (error instanceof ApiError && error.code !== null) {
    const byCode: Partial<Record<string, string>> = strings.errors.byCode;
    // Own keys only: a code such as "constructor" must not find a prototype member.
    return (
      (Object.hasOwn(byCode, error.code) ? byCode[error.code] : undefined) ??
      strings.errors.generic
    );
  }
  return strings.errors.generic;
}

const NO_TRACE_ID = "0".repeat(32);

export function ErrorMessage({ error }: { error: unknown }) {
  const traceId = error instanceof ApiError ? error.traceId : null;
  return (
    <div role="alert">
      <p>{textFor(error)}</p>
      {traceId !== null && traceId !== NO_TRACE_ID && (
        <p>
          <small>{strings.errors.reference(traceId)}</small>
        </p>
      )}
    </div>
  );
}
