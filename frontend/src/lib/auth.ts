// Dashboard session management (Google Sign-In → Arhiteq session JWT).
// The session is issued by POST /auth/google on the backend and stored in
// localStorage; lib/api.ts sends it as `Authorization: Bearer <token>`.

export const SESSION_KEY = "arhiteq_session";

export interface Session {
  /** Arhiteq session JWT. */
  token: string;
  /** Unix seconds. */
  expires_at: number;
  email: string;
  name?: string;
  picture?: string;
  /** Active workspace — a claim inside the token, mirrored here for display. */
  workspace_id?: string;
}

function parseSession(raw: string | null): Session | null {
  if (!raw) return null;
  try {
    const s = JSON.parse(raw) as Session;
    return typeof s?.token === "string" && s.token ? s : null;
  } catch {
    return null;
  }
}

/** Read the stored session (null on the server, when absent, or malformed). */
export function getSession(): Session | null {
  if (typeof window === "undefined") return null;
  return parseSession(window.localStorage.getItem(SESSION_KEY));
}

// ---- useSyncExternalStore adapters (referentially stable snapshots) --------

let cachedRaw: string | null = null;
let cachedSession: Session | null = null;

/** Snapshot for useSyncExternalStore: stable object identity per raw value. */
export function getSessionSnapshot(): Session | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(SESSION_KEY);
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cachedSession = parseSession(raw);
  }
  return cachedSession;
}

export function getServerSessionSnapshot(): Session | null {
  return null;
}

/** Subscribe for useSyncExternalStore (cross-tab login/logout via `storage`). */
export function subscribeSession(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

export function saveSession(session: Session): void {
  window.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

/** expires_at is unix seconds; 30s of slack so requests don't race expiry. */
export function isExpired(session: Session): boolean {
  return session.expires_at * 1000 - 30_000 <= Date.now();
}

/** Convenience: the current session iff it exists and is not expired. */
export function getValidSession(): Session | null {
  const s = getSession();
  return s && !isExpired(s) ? s : null;
}

/**
 * Adopt a session re-issued for another workspace, then hard-navigate.
 *
 * The active workspace is a claim in the token, so switching is purely
 * "replace the stored session". A full page load (not a router push) is what
 * makes the whole dashboard follow: every page holds workspace-scoped rows in
 * component state that a client-side navigation would leave stale.
 */
export function enterWorkspace(
  next: { token: string; expires_at: number; workspace_id: string },
  href = "/agents",
): void {
  const current = getSession();
  saveSession({
    email: current?.email ?? "",
    name: current?.name,
    picture: current?.picture,
    token: next.token,
    expires_at: next.expires_at,
    workspace_id: next.workspace_id,
  });
  window.location.href = href;
}

/** Clear the session and return to the login screen. */
export function logout(): void {
  try {
    window.localStorage.removeItem(SESSION_KEY);
  } finally {
    window.location.href = "/login";
  }
}
