// The chosen demo role: kept in the browser so it survives a reload (AD-9).
// There is no sign-in and nothing is stored on the server.
import { useSyncExternalStore } from "react";
import type { DemoRole } from "../api/contracts.gen";
import { DEMO_ROLES } from "../strings";

export const ROLE_STORAGE_KEY = "aiuw.demo_role";

const listeners = new Set<() => void>();
// Used when the browser refuses storage (private mode, blocked site data).
let fallback: DemoRole | null = null;

function isDemoRole(value: unknown): value is DemoRole {
  return DEMO_ROLES.some((role) => role === value);
}

export function getRole(): DemoRole | null {
  try {
    const stored = window.localStorage.getItem(ROLE_STORAGE_KEY);
    return isDemoRole(stored) ? stored : fallback;
  } catch {
    return fallback;
  }
}

export function setRole(role: DemoRole): void {
  fallback = role;
  try {
    window.localStorage.setItem(ROLE_STORAGE_KEY, role);
  } catch {
    // The role still holds for this page view.
  }
  listeners.forEach((listener) => listener());
}

/** Forget the role. Only tests need this: the app always has a role once chosen. */
export function clearRole(): void {
  fallback = null;
  try {
    window.localStorage.removeItem(ROLE_STORAGE_KEY);
  } catch {
    // Nothing was stored.
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Another tab switching role switches this one too.
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

export function useRole(): DemoRole | null {
  return useSyncExternalStore(subscribe, getRole);
}
