"use client";

// Client-side route guard. Rule: when NEXT_PUBLIC_GOOGLE_CLIENT_ID is set,
// pages require a valid (non-expired) session and redirect to /login
// otherwise. When it is unset (dev mode) everything renders freely and the
// dashboard falls back to NEXT_PUBLIC_API_KEY / mock data.

import {
  getServerSessionSnapshot,
  getSessionSnapshot,
  isExpired,
  subscribeSession,
} from "@/lib/auth";
import { useRouter } from "next/navigation";
import { useEffect, useSyncExternalStore } from "react";

const AUTH_REQUIRED = Boolean(process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID);

const emptySubscribe = () => () => {};
/** false during SSR/hydration, true once the client store is readable. */
function useHydrated(): boolean {
  return useSyncExternalStore(
    emptySubscribe,
    () => true,
    () => false,
  );
}

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const hydrated = useHydrated();
  const session = useSyncExternalStore(
    subscribeSession,
    getSessionSnapshot,
    getServerSessionSnapshot,
  );
  const allowed = !AUTH_REQUIRED || (session !== null && !isExpired(session));

  useEffect(() => {
    // Only redirect once the real (client) session snapshot is in play.
    if (hydrated && !allowed) router.replace("/login");
  }, [hydrated, allowed, router]);

  // Render nothing until allowed to avoid flashing protected UI.
  if (!allowed) return null;
  return <>{children}</>;
}
