import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
// The renderer's worker, built as a file of its own and served by `web`
// like every other script: the content security policy allows scripts and
// workers from this origin only (security.md rule 25), so it is not loaded
// from another site, nor from an address made up in the page.
import workerAddress from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { PageBoxes } from "../api/contracts.gen";
import { strings } from "../strings";

// Set in the module that renders the document, as the library asks.
pdfjs.GlobalWorkerOptions.workerSrc = workerAddress;

/** How wide a page is drawn, in CSS pixels, when the pane's width is not known. */
export const PAGE_WIDTH_PX = 720;
/** The narrowest a page is drawn, however narrow the pane. */
const MIN_PAGE_WIDTH_PX = 240;
/** The room the sheet's own border takes beside the page. */
const SHEET_BORDER_PX = 2;

// The policy allows no WebAssembly to be compiled in the page. This version
// of the renderer (pdf.js 6.3) evaluates no code from text and has no
// option for it, so there is nothing else to turn off.
const OPTIONS = { useWasm: false } as const;

/** The element a page is drawn in; it names its page in `data-page-number`. */
const SHEET = ".result-sheet";
const HIGHLIGHT = ".result-highlight";

/** Whether the browser can say which pages are in view. One that cannot draws every page. */
function canSeeWhatIsInView(): boolean {
  return typeof IntersectionObserver !== "undefined";
}

/** The quote a citation points at: its page, and the boxes of its words. */
export interface CitedQuote {
  pageNumber: number;
  /** Counts the citations followed, so that the same one scrolls again. */
  request: number;
  /** The boxes once read: of that page, and on it (the API client checks both). */
  boxes: PageBoxes | "reading" | "failed";
}

/**
 * In words, where a cited quote is and whether its highlight is on screen:
 * the highlight is never the only sign, and is never claimed when it is not
 * drawn.
 */
export function CitationStatus({
  cited,
  outcome,
}: {
  cited: CitedQuote | null;
  outcome: "pending" | "drawn" | "fault";
}) {
  return (
    <p role="status">
      {cited === null
        ? ""
        : outcome === "drawn"
          ? strings.result.cited(cited.pageNumber)
          : outcome === "fault"
            ? strings.result.citeFault(cited.pageNumber)
            : strings.result.citing(cited.pageNumber)}
    </p>
  );
}

/** A length along a page as a share of the page's own, for a box drawn over it. */
function share(length: number, of: number): string {
  return `${(length / of) * 100}%`;
}

/**
 * The boxes of a quote's words, drawn over the page they are on (AD-14).
 * A box is in PDF points from the page's top-left corner; it is placed as a
 * share of the page's size, so it sits on its word at whatever width the
 * page is drawn. The boxes are `intake`'s: nothing is looked for here.
 */
function Highlight({ boxes }: { boxes: PageBoxes }) {
  return (
    <div
      className="result-highlights"
      role="img"
      aria-label={strings.result.highlightOn(boxes.page_number)}
    >
      {boxes.boxes.map((box, index) => (
        <div
          // The position in the answer: two boxes may name the same word.
          key={index}
          className="result-highlight"
          style={{
            left: share(box.x0, boxes.page_width),
            top: share(box.y0, boxes.page_height),
            width: share(box.x1 - box.x0, boxes.page_width),
            height: share(box.y1 - box.y0, boxes.page_height),
          }}
        />
      ))}
    </div>
  );
}

/**
 * Scroll the pane, and nothing around it, so that an element inside it is a
 * third of the way down: the window stays where the underwriter has it.
 */
function bringIntoPane(pane: HTMLElement, element: Element): void {
  const offset =
    element.getBoundingClientRect().top - pane.getBoundingClientRect().top;
  pane.scrollTop += offset - pane.clientHeight / 3;
}

/**
 * The redacted PDF, page under page, in a pane that scrolls by itself. A
 * page is drawn when it comes into view, not before, so a long document
 * does not draw all its pages at once, and as wide as the pane is. Neither
 * the text layer nor the annotation layer is drawn: a quote is shown by
 * `intake`'s boxes, never found in the browser's own text, and the
 * document's links are not offered.
 */
export function PdfDocument({
  file,
  cited,
}: {
  /** The PDF's bytes, read through the API client. */
  file: Blob;
  cited: CitedQuote | null;
}) {
  const [pageCount, setPageCount] = useState<number | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  // The pages that have come into view, those drawn so far, and those that
  // could not be drawn.
  const [seen, setSeen] = useState<ReadonlySet<number>>(new Set());
  const [drawn, setDrawn] = useState<ReadonlySet<number>>(new Set());
  const [faulty, setFaulty] = useState<ReadonlySet<number>>(new Set());
  const [pageWidth, setPageWidth] = useState(PAGE_WIDTH_PX);
  const pane = useRef<HTMLDivElement>(null);

  // The page is as wide as the pane; the boxes are shares of it and follow.
  useEffect(() => {
    const element = pane.current;
    if (element === null || typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(() => {
      const room = element.clientWidth - SHEET_BORDER_PX;
      if (room > 0) {
        setPageWidth(Math.max(MIN_PAGE_WIDTH_PX, Math.floor(room)));
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (pageCount === null || !canSeeWhatIsInView()) {
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      // Against the browser's own view: a page the scrolling pane cuts off
      // is not in view, and is not drawn.
      const arrived = entries
        .filter((entry) => entry.isIntersecting)
        .map((entry) => {
          observer.unobserve(entry.target);
          return Number((entry.target as HTMLElement).dataset.pageNumber);
        });
      if (arrived.length > 0) {
        setSeen((before) => new Set([...before, ...arrived]));
      }
    });
    for (const sheet of pane.current?.querySelectorAll(SHEET) ?? []) {
      observer.observe(sheet);
    }
    return () => observer.disconnect();
  }, [pageCount]);

  const citedPage = cited?.pageNumber ?? null;
  const citedRequest = cited?.request ?? null;
  const boxes =
    cited !== null && typeof cited.boxes === "object" ? cited.boxes : null;
  // The highlight is on screen only when its page is one of this document's
  // and has been drawn, with boxes to place on it.
  const highlighted =
    boxes !== null &&
    boxes.boxes.length > 0 &&
    citedPage !== null &&
    drawn.has(citedPage);
  const outcome =
    cited === null || citedPage === null
      ? "pending"
      : highlighted
        ? "drawn"
        : loadFailed ||
            cited.boxes === "failed" ||
            (boxes !== null && boxes.boxes.length === 0) ||
            (pageCount !== null && citedPage > pageCount) ||
            faulty.has(citedPage)
          ? "fault"
          : "pending";

  // First the page, at once; then the highlight itself once it is drawn, so
  // a quote low on its page is in view too.
  useEffect(() => {
    const element = pane.current;
    if (element === null || citedPage === null || pageCount === null) {
      return;
    }
    const target =
      (highlighted ? element.querySelector(HIGHLIGHT) : null) ??
      element.querySelector(`${SHEET}[data-page-number="${citedPage}"]`);
    if (target !== null) {
      bringIntoPane(element, target);
    }
  }, [citedPage, citedRequest, pageCount, highlighted]);

  return (
    <>
      <CitationStatus cited={cited} outcome={outcome} />
      <div
        ref={pane}
        className="result-document-pages"
        role="region"
        aria-label={strings.result.pagesLabel}
        // Focusable, so the pane can be scrolled with the keyboard.
        tabIndex={0}
      >
        <Document
          file={file}
          options={OPTIONS}
          suspense={false}
          loading={<p role="status">{strings.result.documentLoading}</p>}
          error={<p role="alert">{strings.result.documentFault}</p>}
          noData={<p role="alert">{strings.result.documentFault}</p>}
          onLoadSuccess={(pdf) => setPageCount(pdf.numPages)}
          onLoadError={() => setLoadFailed(true)}
        >
          {pageCount !== null &&
            Array.from({ length: pageCount }, (_, index) => index + 1).map(
              (pageNumber) => {
                // A cited page is drawn whether or not it has been in view.
                const wanted =
                  !canSeeWhatIsInView() ||
                  seen.has(pageNumber) ||
                  pageNumber === citedPage;
                const failed = () =>
                  setFaulty((before) => new Set([...before, pageNumber]));
                return (
                  <div key={pageNumber} className="result-page">
                    <p>
                      <small>
                        {strings.result.pageCaption(pageNumber, pageCount)}
                      </small>
                    </p>
                    <div
                      data-page-number={pageNumber}
                      className={
                        drawn.has(pageNumber)
                          ? "result-sheet"
                          : "result-sheet result-sheet-waiting"
                      }
                      style={{ width: pageWidth }}
                    >
                      {wanted ? (
                        <Page
                          pageNumber={pageNumber}
                          width={pageWidth}
                          renderTextLayer={false}
                          renderAnnotationLayer={false}
                          loading={
                            <p role="status">{strings.result.pageLoading}</p>
                          }
                          error={<p role="alert">{strings.result.pageFault}</p>}
                          onLoadError={failed}
                          onRenderError={failed}
                          onRenderSuccess={() =>
                            setDrawn(
                              (before) => new Set([...before, pageNumber]),
                            )
                          }
                        />
                      ) : (
                        <p>
                          <small>{strings.result.pageNotDrawnYet}</small>
                        </p>
                      )}
                      {/* Over the page once it is drawn: before that there
                          is no page of the right size to place the boxes on. */}
                      {highlighted &&
                        boxes !== null &&
                        pageNumber === citedPage && <Highlight boxes={boxes} />}
                    </div>
                  </div>
                );
              },
            )}
        </Document>
      </div>
    </>
  );
}
