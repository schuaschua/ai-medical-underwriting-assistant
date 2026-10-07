import { useEffect, useRef, useState } from "react";
import { getThumbnail } from "../api/client";
import { strings } from "../strings";

type State = "loading" | "shown" | "failed";

/** How many more times a picture that could not be shown is read again. */
export const THUMBNAIL_RETRIES = 3;

/**
 * The picture of one redacted page. It is read through the API client, so
 * the call carries the role header like every other (AD-9), and drawn on a
 * canvas: the content security policy allows pictures from this origin's
 * addresses only, and bytes held in the page have no such address.
 *
 * `reads` counts the reads of the list the picture belongs to: a picture
 * that could not be shown is read again when the list is, a few times.
 */
export function PageThumbnail({
  address,
  label,
  reads,
}: {
  address: string;
  label: string;
  reads: number;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [state, setState] = useState<State>("loading");
  const [attempt, setAttempt] = useState(0);
  const [seenReads, setSeenReads] = useState(reads);

  // Set while rendering, as React asks for state that follows a prop.
  if (reads !== seenReads) {
    setSeenReads(reads);
    if (state === "failed" && attempt < THUMBNAIL_RETRIES) {
      setAttempt(attempt + 1);
      setState("loading");
    }
  }

  useEffect(() => {
    let current = true;
    getThumbnail(address)
      .then((picture) => createImageBitmap(picture))
      .then(
        (bitmap) => {
          const target = current ? canvas.current : null;
          const context = target?.getContext("2d") ?? null;
          if (target === null || context === null) {
            bitmap.close();
            if (current) setState("failed");
            return;
          }
          target.width = bitmap.width;
          target.height = bitmap.height;
          context.drawImage(bitmap, 0, 0);
          bitmap.close();
          setState("shown");
        },
        () => {
          if (current) setState("failed");
        },
      );
    return () => {
      current = false;
    };
  }, [address, attempt]);

  return (
    <>
      <canvas
        ref={canvas}
        role="img"
        aria-label={label}
        hidden={state !== "shown"}
      />
      {state === "loading" && <small>{strings.triage.thumbnailLoading}</small>}
      {state === "failed" && (
        <small>{strings.triage.thumbnailUnavailable}</small>
      )}
    </>
  );
}
