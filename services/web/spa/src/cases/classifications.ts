// What the classifier said of the pages of a case, for the prompt that asks
// the customer about a page. Read from the server; nothing here works a type
// or a confidence out (AD-7, AD-19).
import { useEffect, useRef, useState } from "react";
import { getClassifications } from "../api/client";
import type { Classification, PageProgress } from "../api/contracts.gen";

export type ClassificationsByPage = Readonly<Record<string, Classification>>;

const NONE: ClassificationsByPage = {};

/** The pages of a case that wait for the customer's answer. */
function awaitingCustomer(pages: readonly PageProgress[]): string[] {
  return pages
    .filter((page) => page.page_status === "awaiting_customer")
    .map((page) => page.page_id);
}

/**
 * The classification of each page of a case, by page id. It is read once a
 * page waits for the customer, and not before. A read that fails, or that
 * lists nothing for a waiting page, is tried again when the case's progress
 * is next read.
 */
export function useClassifications(
  caseId: string,
  pages: readonly PageProgress[],
): ClassificationsByPage {
  const [held, setHeld] = useState<{
    caseId: string;
    byPage: ClassificationsByPage;
  }>({ caseId, byPage: NONE });
  // The case followed now (none once the screen is left), the waiting pages
  // a classification was found for, and whether a read is out.
  const followed = useRef<string | null>(null);
  const foundFor = useRef<Set<string>>(new Set());
  const inFlight = useRef(false);

  useEffect(() => {
    followed.current = caseId;
    foundFor.current = new Set();
    return () => {
      followed.current = null;
    };
  }, [caseId]);

  useEffect(() => {
    const waiting = awaitingCustomer(pages);
    if (
      inFlight.current ||
      waiting.every((pageId) => foundFor.current.has(pageId))
    ) {
      return;
    }
    inFlight.current = true;
    getClassifications(caseId)
      .then(
        (listed) => {
          if (followed.current !== caseId) {
            // The screen was left, or follows another case by now.
            return;
          }
          const found: Record<string, Classification> = {};
          for (const classification of listed.classifications) {
            // One classifier runs for a case; its reading of a page is the first.
            found[classification.page_id] ??= classification;
          }
          waiting
            .filter((pageId) => Object.hasOwn(found, pageId))
            .forEach((pageId) => foundFor.current.add(pageId));
          setHeld({ caseId, byPage: found });
        },
        () => {
          // The prompt is shown without the type, and the read is tried again.
        },
      )
      .finally(() => {
        inFlight.current = false;
      });
  }, [caseId, pages]);

  // What was read for another case says nothing about this one.
  return held.caseId === caseId ? held.byPage : NONE;
}
