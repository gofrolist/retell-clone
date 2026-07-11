"use client";

// Truthful data-source indicator. If the backend is unreachable or rejects
// our credentials, say so loudly instead of rendering stale/empty tables that
// look real. Demo mode (NEXT_PUBLIC_DEMO_MODE=true) is labelled explicitly.

import { getBackendStatus, subscribeBackendStatus, type BackendStatus } from "@/lib/api";
import { AlertTriangle, FlaskConical } from "lucide-react";
import { useSyncExternalStore } from "react";

const MESSAGES: Partial<Record<BackendStatus, { text: string; tone: "error" | "info" }>> = {
  unreachable: {
    text: "Backend unreachable — data cannot be loaded. Start the API (make api) or check NEXT_PUBLIC_API_URL.",
    tone: "error",
  },
  unauthorized: {
    text: "Not authorized — sign in, or set NEXT_PUBLIC_API_KEY for local development.",
    tone: "error",
  },
  demo: {
    text: "Demo mode — you are looking at canned sample data, not your workspace.",
    tone: "info",
  },
};

export default function BackendBanner() {
  const status = useSyncExternalStore(
    subscribeBackendStatus,
    getBackendStatus,
    () => "unknown" as BackendStatus,
  );
  const msg = MESSAGES[status];
  if (!msg) return null;

  return (
    <div
      role="alert"
      className={`flex items-center gap-2 px-4 py-2 text-[13px] font-medium ${
        msg.tone === "error"
          ? "bg-red-50 text-red-800 border-b border-red-200"
          : "bg-amber-50 text-amber-800 border-b border-amber-200"
      }`}
    >
      {msg.tone === "error" ? (
        <AlertTriangle className="size-4 shrink-0" />
      ) : (
        <FlaskConical className="size-4 shrink-0" />
      )}
      {msg.text}
    </div>
  );
}
