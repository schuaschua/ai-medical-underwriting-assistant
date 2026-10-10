// The idempotency key of the upload under way, kept against the file it is
// for. It is in session storage, so a reload in the middle of an upload
// retries with the same key and the server answers with the first case.
import { newIdempotencyKey } from "../api/client";

export const UPLOAD_KEY_STORAGE_KEY = "aiuw.upload_key";

interface StoredKey {
  name: string;
  size: number;
  last_modified: number;
  key: string;
}

// Used when the browser refuses storage (private mode, blocked site data).
let fallback: StoredKey | null = null;

function read(): StoredKey | null {
  let raw: string | null;
  try {
    raw = window.sessionStorage.getItem(UPLOAD_KEY_STORAGE_KEY);
  } catch {
    return fallback;
  }
  if (raw === null) {
    return fallback;
  }
  try {
    const value = JSON.parse(raw) as Partial<StoredKey> | null;
    return value !== null &&
      typeof value === "object" &&
      typeof value.name === "string" &&
      typeof value.size === "number" &&
      typeof value.last_modified === "number" &&
      typeof value.key === "string" &&
      value.key !== ""
      ? (value as StoredKey)
      : null;
  } catch {
    return null;
  }
}

/**
 * The key to send with this file: the one already made for it, if its
 * upload was begun and not finished, else a new one, which is remembered.
 */
export function uploadKeyFor(file: File): string {
  const stored = read();
  if (
    stored !== null &&
    stored.name === file.name &&
    stored.size === file.size &&
    stored.last_modified === file.lastModified
  ) {
    return stored.key;
  }
  const made: StoredKey = {
    name: file.name,
    size: file.size,
    last_modified: file.lastModified,
    key: newIdempotencyKey(),
  };
  fallback = made;
  try {
    window.sessionStorage.setItem(UPLOAD_KEY_STORAGE_KEY, JSON.stringify(made));
  } catch {
    // The key still holds for this page view.
  }
  return made.key;
}

/** The upload is done: its key is not used again. */
export function forgetUploadKey(): void {
  fallback = null;
  try {
    window.sessionStorage.removeItem(UPLOAD_KEY_STORAGE_KEY);
  } catch {
    // Nothing was stored.
  }
}
