import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  classification,
  fakeServer,
  json,
  pageProgress,
  UPLOADED,
} from "../test/server";
import type { PageProgress } from "../api/contracts.gen";
import { useClassifications } from "./classifications";

const CASE: string = UPLOADED.case_id;

/** Pages that wait for the customer, by page number. */
function waitingPages(...pageNumbers: number[]): PageProgress[] {
  return pageNumbers.map(
    (pageNumber) =>
      pageProgress(pageNumber, "awaiting_customer") as PageProgress,
  );
}
const OTHER_CASE = "019a0000-0000-7000-8000-000000000011";

function listed(caseId: string, ...items: unknown[]): Response {
  return json(200, { case_id: caseId, classifications: items });
}

function reads(server: { calls: { path: string }[] }, caseId: string): number {
  return server.calls.filter(
    (call) => call.path === `/api/cases/${caseId}/classifications`,
  ).length;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("4.2 a page classified by both classifiers", () => {
  it("takes the classification of the classifier the case was started with", async () => {
    // Both classifiers read page 1, the other one first in the list; only
    // the chat model read page 2.
    fakeServer((call) =>
      call.path.endsWith("/classifications")
        ? listed(
            CASE,
            classification(CASE, 1, "invoice", 1),
            {
              ...classification(CASE, 1, "other", 0.55),
              contender: "doc-intelligence",
            },
            classification(CASE, 2, "invoice", 1),
          )
        : undefined,
    );
    const pages = waitingPages(1, 2);
    const { result, rerender } = renderHook(
      ({ contender }: { contender: "llm" | "doc-intelligence" }) =>
        useClassifications(CASE, pages, contender),
      { initialProps: { contender: "doc-intelligence" } },
    );
    const first = pageProgress(1, "x").page_id;
    const second = pageProgress(2, "x").page_id;

    await waitFor(() => expect(result.current[first]).toBeDefined());
    expect(result.current[first]).toMatchObject({
      contender: "doc-intelligence",
      page_type: "other",
      confidence: 0.55,
    });
    // Nothing of the other classifier's reading is shown in its place.
    expect(result.current[second]).toBeUndefined();

    rerender({ contender: "llm" });
    await waitFor(() => expect(result.current[first]?.contender).toBe("llm"));
    expect(result.current[second]?.page_type).toBe("invoice");
  });
});

describe("1.10 the classifications of a case", () => {
  it("reads again for a waiting page the list held nothing for", async () => {
    let known = false;
    const server = fakeServer((call) =>
      call.path.endsWith("/classifications")
        ? listed(
            CASE,
            classification(CASE, 1, "invoice", 1),
            ...(known ? [classification(CASE, 2, "other", 0.96)] : []),
          )
        : undefined,
    );
    const waiting = () => waitingPages(1, 2);
    const { result, rerender } = renderHook(
      ({ pages }) => useClassifications(CASE, pages),
      { initialProps: { pages: waiting() } },
    );
    await waitFor(() => expect(Object.keys(result.current)).toHaveLength(1));

    // The next read of the progress: page 2 has no classification yet, so
    // the list is read again; once it has one, it is not.
    known = true;
    rerender({ pages: waiting() });
    await waitFor(() => expect(Object.keys(result.current)).toHaveLength(2));
    expect(result.current[pageProgress(2, "x").page_id]?.page_type).toBe(
      "other",
    );
    expect(reads(server, CASE)).toBe(2);
    rerender({ pages: waiting() });
    await Promise.resolve();
    expect(reads(server, CASE)).toBe(2);
  });

  it("drops what it read for one case when it is given another", async () => {
    const server = fakeServer((call) =>
      call.path === `/api/cases/${CASE}/classifications`
        ? listed(CASE, classification(CASE, 1, "invoice", 1))
        : undefined,
    );
    const pages = waitingPages(1);
    const { result, rerender } = renderHook(
      ({ caseId }) => useClassifications(caseId, pages),
      { initialProps: { caseId: CASE } },
    );
    await waitFor(() => expect(Object.keys(result.current)).toHaveLength(1));

    // The other case has a page with the same id in this stand-in: nothing
    // of the first case's answer is shown for it, and its own list is read.
    rerender({ caseId: OTHER_CASE });

    expect(result.current).toEqual({});
    await waitFor(() => expect(reads(server, OTHER_CASE)).toBe(1));
    expect(result.current).toEqual({});
  });

  it("ignores an answer that comes after the screen was left, or for a case it no longer follows", async () => {
    const release: Record<string, () => void> = {};
    fakeServer((call) => {
      const match = /cases\/([^/]+)\/classifications$/.exec(call.path);
      if (match === null) {
        return undefined;
      }
      const caseId = match[1]!;
      return new Promise<Response>((resolveAnswer) => {
        release[caseId] = () =>
          resolveAnswer(listed(caseId, classification(caseId, 1, "other", 1)));
      });
    });
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const pages = waitingPages(1);
    const { result, rerender, unmount } = renderHook(
      ({ caseId }) => useClassifications(caseId, pages),
      { initialProps: { caseId: CASE } },
    );
    await waitFor(() => expect(release[CASE]).toBeDefined());

    // Another case is followed before the first answer comes.
    rerender({ caseId: OTHER_CASE });
    release[CASE]!();
    await Promise.resolve();
    await Promise.resolve();
    expect(result.current).toEqual({});

    const seen = result.current;
    unmount();
    release[OTHER_CASE]?.();
    await Promise.resolve();
    await Promise.resolve();
    expect(result.current).toBe(seen);
    expect(errors).not.toHaveBeenCalled();
  });
});
