import { strings } from "../strings";

/**
 * A time the server recorded: local to the viewer, with the UTC time as its
 * title. Text that is no time is shown as it came.
 */
export function LocalTime({ at }: { at: string }) {
  const time = new Date(at);
  if (Number.isNaN(time.getTime())) {
    return <>{at}</>;
  }
  const utc = time.toISOString();
  return (
    <time
      dateTime={at}
      title={strings.time.utc(utc.slice(0, 10), utc.slice(11, 19))}
    >
      {time.toLocaleString()}
    </time>
  );
}
