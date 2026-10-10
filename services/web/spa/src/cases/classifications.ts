// What the classifier said of the pages of a case, for the prompt that asks
// the customer about a page. Read from the server; nothing here works a type
// or a confidence out (AD-7, AD-19).
import { useEffect, useRef, useState } from "react";
import { getClassifications } from "../api/client";
import type {
  Classification,
  ClassifierContender,
  PageProgress,
} from "../api/contracts.gen";

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
 *
 * A page may have a classification of each classifier. `contender` is the
 * one the server says the case was started with: only its classifications
 * are taken, since the page was routed on them. While the server has not
 * said, the first classification listed for a page is taken.
 */
export function useClassifications(
  caseId: string,
  pages: readonly PageProgress[],
  contender?: ClassifierContender | null,
): ClassificationsByPage {
  const [held, setHeld] = useState<{
    caseId: string;
    byPage: ClassificationsByPage;
  }>({ caseId, byPage: NONE });
  // The case followed now (none once the screen is left), the waiting pages
  // a classification was found for, and whether a read is out.
  const followed = useRef<string | null>(null);
  // The classifier the case is known to run with now. A read takes its
  // result only if this is still the one it was started with.
  const wanted = useRef<ClassifierContender | null | undefined>(contender);
  const foundFor = useRef<Set<string>>(new Set());
  const inFlight = useRef(false);

  useEffect(() => {
    followed.current = caseId;
    wanted.current = contender;
    foundFor.current = new Set();
    return () => {
      followed.current = null;
    };
    // Read anew when the server names another classifier for the case.
  }, [caseId, contender]);

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
          if (followed.current !== caseId || wanted.current !== contender) {
            // The screen was left, or follows another case by now; or the
            // server has named the case's classifier since this read was
            // sent, and what it would take may be the other one's reading.
            // The list is read again when the progress is next read.
            return;
          }
          const found: Record<string, Classification> = {};
          for (const classification of listed.classifications) {
            if (contender != null && classification.contender !== contender) {
              // Another classifier's reading: the case was not routed on it.
              continue;
            }
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
  }, [caseId, pages, contender]);

  // What was read for another case says nothing about this one.
  return held.caseId === caseId ? held.byPage : NONE;
}
