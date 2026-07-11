"use client";

import {
  getServerSessionSnapshot,
  getSessionSnapshot,
  logout,
  subscribeSession,
} from "@/lib/auth";
import {
  BadgeCheck,
  ChevronsUpDown,
  CircleHelp,
  LogOut,
  Megaphone,
} from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

function AccountRow() {
  // Session lives in localStorage: null during SSR/hydration, then the real
  // value — useSyncExternalStore keeps that transition hydration-safe.
  const session = useSyncExternalStore(
    subscribeSession,
    getSessionSnapshot,
    getServerSessionSnapshot,
  );
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const email = session?.email ?? "Not signed in";
  const name = session?.name ?? email;
  const initial = (session?.name ?? session?.email ?? "?")
    .charAt(0)
    .toUpperCase();

  return (
    <div ref={rootRef} className="relative">
      {open && (
        <div className="absolute bottom-full left-0 z-20 mb-1.5 w-full rounded-lg border border-line bg-white p-1 shadow-lg">
          <div className="px-2.5 py-2">
            <div className="truncate text-[13px] font-medium text-ink">{name}</div>
            <div className="truncate text-xs text-sub">{email}</div>
          </div>
          <div className="my-0.5 border-t border-line" />
          {session ? (
            <button
              onClick={logout}
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-[13px] text-ink hover:bg-app cursor-pointer"
            >
              <LogOut className="size-3.5 text-sub" /> Sign out
            </button>
          ) : (
            <div className="px-2.5 py-2 text-xs text-sub">
              Dev mode — using API key / mock data.
            </div>
          )}
        </div>
      )}
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-lg border border-line bg-white px-2.5 py-2 shadow-sm hover:bg-app cursor-pointer"
      >
        {session?.picture ? (
          // eslint-disable-next-line @next/next/no-img-element -- tiny remote avatar; next/image needs remotePatterns config
          <img
            src={session.picture}
            alt=""
            referrerPolicy="no-referrer"
            className="size-5.5 shrink-0 rounded-full object-cover"
          />
        ) : (
          <span className="flex size-5.5 items-center justify-center rounded-full bg-gradient-to-br from-amber-400 to-rose-400 text-[10px] font-semibold text-white shrink-0">
            {initial}
          </span>
        )}
        <span className="grow truncate text-left text-[13px]">{email}</span>
        <ChevronsUpDown className="size-3.5 text-faint shrink-0" />
      </button>
    </div>
  );
}

export default function SidebarFooter() {
  // The plan pill and Help/Updates have no destinations yet — rendered as
  // static labels (no click affordance) instead of dead buttons.
  return (
    <div className="space-y-2 px-3 pb-3">
      <div className="flex w-full items-center rounded-lg border border-line bg-white px-2.5 py-2 shadow-sm cursor-default">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-xs font-medium text-accent-deep">
          <BadgeCheck className="size-3.5" />
          Pay As You Go
        </span>
      </div>
      <AccountRow />
      <div className="flex items-center justify-between px-1 pt-1">
        <span className="inline-flex items-center gap-1.5 text-[13px] text-sub cursor-default">
          <CircleHelp className="size-4" /> Help
        </span>
        <span className="inline-flex items-center gap-1.5 text-[13px] text-sub cursor-default">
          <Megaphone className="size-4" /> Updates
        </span>
      </div>
    </div>
  );
}
