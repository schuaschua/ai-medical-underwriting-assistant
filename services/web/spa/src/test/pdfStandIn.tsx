// A stand-in for the PDF renderer (`react-pdf`). jsdom has no canvas and no
// worker, so the tests draw nothing: the stand-in says which pages the
// screen asked it to draw, and with what. That the real renderer draws a
// real PDF under the content security policy is checked in a browser.
import { useEffect, type ReactNode } from "react";

let pageCount = 3;

/** How many pages the next document the stand-in opens has. */
export function setStandInPageCount(count: number): void {
  pageCount = count;
}

export const pdfjs = { GlobalWorkerOptions: { workerSrc: "" } };

export function Document({
  file,
  children,
  onLoadSuccess,
}: {
  file: Blob;
  children?: ReactNode;
  onLoadSuccess?: (pdf: { numPages: number }) => void;
}) {
  useEffect(() => {
    onLoadSuccess?.({ numPages: pageCount });
    // Once per document, as the renderer opens it once.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the callback is new on every render; the document is not
  }, [file]);
  return (
    <div role="document" aria-label={`A PDF of ${file.size} bytes`}>
      {children}
    </div>
  );
}

export function Page({
  pageNumber,
  width,
  renderTextLayer,
  renderAnnotationLayer,
  onRenderSuccess,
}: {
  pageNumber: number;
  width?: number;
  renderTextLayer?: boolean;
  renderAnnotationLayer?: boolean;
  onRenderSuccess?: () => void;
}) {
  useEffect(() => {
    onRenderSuccess?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- drawn once per page
  }, [pageNumber]);
  return (
    <div
      role="img"
      aria-label={`Drawn page ${pageNumber}`}
      data-width={width}
      data-text-layer={String(renderTextLayer)}
      data-annotation-layer={String(renderAnnotationLayer)}
    />
  );
}
