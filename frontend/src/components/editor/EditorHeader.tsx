"use client";

import Button from "@/components/ui/Button";
import { PillTabs } from "@/components/ui/Tabs";
import {
  ChevronLeft,
  History,
  MoreHorizontal,
  Share2,
  Sparkles,
  Tag,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

export default function EditorHeader({
  name,
  onName,
  version,
  isPublished,
  dirty,
  saving,
  onSave,
  publishing,
  onPublish,
  error,
}: {
  name: string;
  onName: (v: string) => void;
  version: number;
  isPublished: boolean;
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  publishing: boolean;
  onPublish: () => void;
  error?: string | null;
}) {
  const [tab, setTab] = useState("create");
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-card px-4">
      <Link
        href="/agents"
        className="rounded-md p-1.5 text-sub hover:bg-app"
        aria-label="Back to agents"
      >
        <ChevronLeft className="size-4.5" />
      </Link>
      <input
        value={name}
        onChange={(e) => onName(e.target.value)}
        placeholder="Untitled agent"
        aria-label="Agent name"
        className="w-64 truncate rounded-md border border-transparent bg-transparent px-1.5 py-0.5 text-[15px] font-semibold outline-none transition-colors hover:border-line focus:border-accent"
      />
      <span className="inline-flex items-center gap-1 rounded-md border border-line bg-app px-2 py-0.5 text-xs font-medium text-sub">
        <Tag className="size-3" />
        {isPublished ? "Published" : "Draft"}
      </span>

      <div className="mx-auto">
        {/* Simulation has no backend yet; only the Create view exists. */}
        <PillTabs
          tabs={[{ key: "create", label: "Create" }]}
          active={tab}
          onChange={setTab}
        />
      </div>

      {error && <span className="max-w-64 truncate text-xs text-bad" title={error}>{error}</span>}
      <button
        disabled
        title="Not available yet"
        className="rounded-md p-1.5 text-sub opacity-40 cursor-not-allowed"
        aria-label="More"
      >
        <MoreHorizontal className="size-4" />
      </button>
      <button
        disabled
        title="Not available yet"
        className="rounded-md p-1.5 text-sub opacity-40 cursor-not-allowed"
        aria-label="Share"
      >
        <Share2 className="size-4" />
      </button>
      <Button size="sm" disabled title="Version history not available yet">
        <History className="size-3.5" />
        V{version}
      </Button>
      <Button size="sm" variant="primary" onClick={onSave} disabled={!dirty || saving}>
        {saving ? "Saving…" : "Save"}
      </Button>
      <Button
        size="sm"
        onClick={onPublish}
        disabled={publishing}
        title={dirty ? "You have unsaved changes; Publish uses the last saved version" : undefined}
      >
        {publishing ? "Publishing…" : "Publish"}
      </Button>
      <Button
        size="sm"
        variant="secondary"
        className="text-accent-deep opacity-40 cursor-not-allowed"
        disabled
        title="Not available yet"
      >
        <Sparkles className="size-3.5" />
        Conductor
      </Button>
    </header>
  );
}
