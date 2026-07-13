"use client";

// Google Sign-In via Google Identity Services (GIS). Posts the GIS credential
// to the backend (POST /auth/google) and stores the returned Architeq session
// in localStorage — see lib/auth.ts. Rendered outside the (shell) group so it
// never shows the sidebar and is never route-guarded.

import Logo from "@/components/shell/Logo";
import { API_BASE } from "@/lib/api";
import { getValidSession, saveSession } from "@/lib/auth";
import { useRouter } from "next/navigation";
import Script from "next/script";
import { useCallback, useEffect, useRef, useState } from "react";

const API = API_BASE;
const GOOGLE_CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID;

interface GisCredentialResponse {
  credential: string;
}

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: {
            client_id: string;
            callback: (response: GisCredentialResponse) => void;
          }) => void;
          renderButton: (
            parent: HTMLElement,
            options: Record<string, string | number>,
          ) => void;
        };
      };
    };
  }
}

export default function LoginPage() {
  const router = useRouter();
  const buttonRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Invite links land here as /login?invite=<token>; the token rides along on
  // /auth/google, where a matching Google email redeems the invite. Read via
  // window.location (not useSearchParams) to skip the Suspense requirement.
  const [inviteToken, setInviteToken] = useState<string | null>(null);
  useEffect(() => {
    setInviteToken(new URLSearchParams(window.location.search).get("invite"));
  }, []);

  // Already signed in? Straight to the dashboard — unless following an invite
  // link, which may belong to a different Google account.
  useEffect(() => {
    const hasInvite = new URLSearchParams(window.location.search).has("invite");
    if (!hasInvite && getValidSession()) router.replace("/agents");
  }, [router]);

  const handleCredential = useCallback(
    async (response: GisCredentialResponse) => {
      setError(null);
      setBusy(true);
      try {
        const res = await fetch(`${API}/auth/google`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            id_token: response.credential,
            ...(inviteToken ? { invite_token: inviteToken } : {}),
          }),
        });
        if (res.ok) {
          const data = (await res.json()) as {
            token: string;
            expires_at: number;
            email: string;
            name?: string;
            picture?: string;
          };
          saveSession({
            token: data.token,
            expires_at: data.expires_at,
            email: data.email,
            name: data.name,
            picture: data.picture,
          });
          router.replace("/agents");
          return;
        }
        // The backend puts the actionable reason in `detail` — e.g. a 503
        // can be "Google Sign-In is not configured" OR "No workspace
        // provisioned yet (run architeq_api.seed)". Prefer it over guessing
        // from the status code.
        let detail = "";
        try {
          detail = String(((await res.json()) as { detail?: unknown }).detail ?? "");
        } catch {
          // non-JSON error body — fall through to status-based messages
        }
        if (res.status === 403) {
          setError(
            detail ||
              "This Google account is not allowed to access Architeq. Ask a workspace admin to add your email to the allowlist.",
          );
        } else if (res.status === 401) {
          setError("Google rejected the sign-in token. Please try again.");
        } else if (res.status === 503) {
          setError(
            detail ||
              "Sign-in is not configured on the backend (503). Set the Google auth settings on the API server, or use the API-key dev mode.",
          );
        } else {
          setError(detail || `Sign-in failed (${res.status} ${res.statusText}).`);
        }
      } catch {
        setError(
          `Could not reach the Architeq API at ${API}. Check NEXT_PUBLIC_API_URL and that the backend is running.`,
        );
      } finally {
        setBusy(false);
      }
    },
    [router, inviteToken],
  );

  const initGoogle = useCallback(() => {
    if (!GOOGLE_CLIENT_ID || !window.google || !buttonRef.current) return;
    window.google.accounts.id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      callback: handleCredential,
    });
    buttonRef.current.replaceChildren(); // idempotent across re-mounts
    window.google.accounts.id.renderButton(buttonRef.current, {
      type: "standard",
      theme: "outline",
      size: "large",
      text: "signin_with",
      shape: "rectangular",
      logo_alignment: "left",
      width: 320,
    });
  }, [handleCredential]);

  // The GIS script may already be on the page (client-side back-navigation);
  // onReady covers fresh loads, this covers re-mounts where onReady already ran.
  useEffect(() => {
    initGoogle();
  }, [initGoogle]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-app px-4">
      {GOOGLE_CLIENT_ID && (
        <Script src="https://accounts.google.com/gsi/client" onReady={initGoogle} />
      )}

      <div className="w-full max-w-sm rounded-2xl border border-line bg-card p-8 shadow-sm">
        <div className="flex justify-center">
          <Logo />
        </div>
        <h1 className="mt-5 text-center text-[17px] font-semibold text-ink">
          Sign in to Architeq
        </h1>
        <p className="mt-1 text-center text-[13px] text-sub">
          Build, deploy and monitor AI voice agents
        </p>

        {inviteToken && (
          <p className="mt-4 rounded-lg border border-line bg-app px-3 py-2 text-[12.5px] text-sub">
            <span className="font-medium text-ink">You&apos;ve been invited.</span>{" "}
            Sign in with the Google account the invite was sent to and
            you&apos;ll join the workspace automatically.
          </p>
        )}

        <div className="mt-6 flex min-h-11 items-center justify-center">
          {GOOGLE_CLIENT_ID ? (
            <div ref={buttonRef} aria-busy={busy} />
          ) : (
            <div className="w-full space-y-3">
              <p className="rounded-lg border border-line bg-app px-3 py-2 text-[12.5px] text-sub">
                <span className="font-medium text-ink">Dev mode:</span>{" "}
                NEXT_PUBLIC_GOOGLE_CLIENT_ID is not set, so Google Sign-In is
                disabled. The dashboard will use NEXT_PUBLIC_API_KEY or mock
                data.
              </p>
              <button
                onClick={() => router.replace("/agents")}
                className="w-full rounded-lg border border-line bg-white px-3 py-2 text-[13.5px] font-medium text-ink shadow-sm hover:bg-app cursor-pointer"
              >
                Continue without signing in (dev)
              </button>
            </div>
          )}
        </div>

        {busy && (
          <p className="mt-4 text-center text-[12.5px] text-sub">Signing in…</p>
        )}
        {error && (
          <p role="alert" className="mt-4 text-center text-[12.5px] text-bad">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
