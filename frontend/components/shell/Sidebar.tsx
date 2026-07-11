"use client";

import Logo from "./Logo";
import SidebarFooter from "./SidebarFooter";
import WorkspaceSwitcher from "./WorkspaceSwitcher";
import { cn } from "@/lib/utils";
import {
  Activity,
  BellRing,
  Blocks,
  Bot,
  ChevronLeft,
  Contact,
  Gauge,
  Headphones,
  History,
  KeyRound,
  Library,
  MessageSquareText,
  Phone,
  PhoneOutgoing,
  ScanSearch,
  Settings,
  ShieldCheck,
  Users,
  Webhook,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
}

const GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "BUILD",
    items: [
      { label: "Agents", href: "/agents", icon: Bot },
      { label: "Knowledge Base", href: "/knowledge-base", icon: Library },
    ],
  },
  {
    label: "DEPLOY",
    items: [
      { label: "Phone Numbers", href: "/phone-numbers", icon: Phone },
      { label: "Batch Call", href: "/batch-call", icon: PhoneOutgoing },
    ],
  },
  {
    label: "DATA",
    items: [
      { label: "Call History", href: "/call-history", icon: History },
      { label: "Chat History", href: "/chat-history", icon: MessageSquareText },
      { label: "Contacts", href: "/contacts", icon: Contact },
    ],
  },
  {
    label: "MONITOR",
    items: [
      { label: "Analytics", href: "/analytics", icon: Activity },
      { label: "Live Monitoring", href: "/live-monitoring", icon: Headphones },
      { label: "AI Quality Assurance", href: "/quality-assurance", icon: ScanSearch },
      { label: "Alerting", href: "/alerting", icon: BellRing },
    ],
  },
  {
    label: "SYSTEM",
    items: [
      { label: "Integrations", href: "/integrations", icon: Blocks },
      { label: "Settings", href: "/settings/limits", icon: Settings },
    ],
  },
];

const SETTINGS_ITEMS: NavItem[] = [
  { label: "Limits", href: "/settings/limits", icon: Gauge },
  { label: "Reliability", href: "/settings/reliability", icon: ShieldCheck },
  { label: "API Keys", href: "/settings/api-keys", icon: KeyRound },
  { label: "Webhooks", href: "/settings/webhooks", icon: Webhook },
  { label: "Workspace", href: "/settings/workspace", icon: Users },
];

function NavLink({ item, active }: { item: NavItem; active: boolean }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      className={cn(
        "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] transition-colors",
        active
          ? "bg-white font-medium text-ink shadow-sm border border-line"
          : "text-sub hover:bg-black/4 hover:text-ink border border-transparent",
      )}
    >
      <Icon className="size-4 shrink-0" strokeWidth={1.8} />
      {item.label}
    </Link>
  );
}

export default function Sidebar() {
  const pathname = usePathname();
  const inSettings = pathname.startsWith("/settings");

  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-line bg-app">
      <div className="px-3 pt-4 pb-3">
        <Logo />
      </div>

      {inSettings ? (
        <nav className="grow overflow-y-auto px-3">
          <Link
            href="/agents"
            className="mb-3 flex items-center gap-1.5 px-2 py-1.5 text-xs font-semibold tracking-wide text-sub hover:text-ink"
          >
            <ChevronLeft className="size-3.5" /> GO BACK
          </Link>
          <div className="mb-2 px-2 text-[15px] font-semibold">Settings</div>
          <div className="space-y-0.5">
            {SETTINGS_ITEMS.map((item) => (
              <NavLink key={item.href} item={item} active={pathname === item.href} />
            ))}
          </div>
        </nav>
      ) : (
        <>
          <div className="px-3 pb-1.5">
            <WorkspaceSwitcher />
          </div>
          <nav className="grow space-y-4 overflow-y-auto px-3 py-2.5">
            {GROUPS.map((group) => (
              <div key={group.label}>
                <div className="mb-1 px-2.5 text-[11px] font-semibold tracking-wider text-faint">
                  {group.label}
                </div>
                <div className="space-y-0.5">
                  {group.items.map((item) => (
                    <NavLink
                      key={item.href}
                      item={item}
                      active={
                        item.href === "/settings/limits"
                          ? false
                          : pathname === item.href || pathname.startsWith(item.href + "/")
                      }
                    />
                  ))}
                </div>
              </div>
            ))}
          </nav>
        </>
      )}

      <SidebarFooter />
    </aside>
  );
}
