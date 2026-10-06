// The cases uploaded from this browser tab, newest first. They are kept in
// session storage: there is no sign-in, so the server cannot say which cases
// are "yours", and the list ends with the browser session.
import { useSyncExternalStore } from "react";
import { isUploadedCase } from "../api/client";
import type { UploadedCase } from "../api/contracts.gen";

export const CASES_STORAGE_KEY = "aiuw.session_cases";

const NO_CASES: readonly UploadedCase[] = [];
const listeners = new Set<() => void>();
// Used when the browser refuses storage (private mode, blocked site data).
let fallback: readonly UploadedCase[] = NO_CASES;
// The last stored text and what it parsed to, so an unchanged list is the
// same array each time React asks for it.
let cached: { raw: string; cases: readonly UploadedCase[] } | null = null;

function parse(raw: string): readonly UploadedCase[] {
  try {
    const value: unknown = JSON.parse(raw);
    return Array.isArray(value) ? value.filter(isUploadedCase) : NO_CASES;
  } catch {
    return NO_CASES;
  }
}

export function getSessionCases(): readonly UploadedCase[] {
  let raw: string | null;
  try {
    raw = window.sessionStorage.getItem(CASES_STORAGE_KEY);
  } catch {
    return fallback;
  }
  if (raw === null) {
    return fallback;
  }
  if (cached === null || cached.raw !== raw) {
    cached = { raw, cases: parse(raw) };
  }
  return cached.cases;
}

function store(cases: readonly UploadedCase[]): void {
  fallback = cases;
  try {
    if (cases.length === 0) {
      window.sessionStorage.removeItem(CASES_STORAGE_KEY);
    } else {
      window.sessionStorage.setItem(CASES_STORAGE_KEY, JSON.stringify(cases));
    }
  } catch {
    // The list still holds for this page view.
  }
  listeners.forEach((listener) => listener());
}

/** Put a newly uploaded case at the top of the list. */
export function addSessionCase(uploaded: UploadedCase): void {
  store([
    uploaded,
    ...getSessionCases().filter((item) => item.case_id !== uploaded.case_id),
  ]);
}

/** Forget every case. Only tests need this. */
export function clearSessionCases(): void {
  store(NO_CASES);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useSessionCases(): readonly UploadedCase[] {
  return useSyncExternalStore(subscribe, getSessionCases);
}
