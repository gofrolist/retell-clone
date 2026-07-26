"use client";

import Button from "@/components/ui/Button";
import SearchInput from "@/components/ui/SearchInput";
import { SIDE_PANEL_WIDTH } from "@/components/editor/panelLayout";
import { type RawAgentVersion } from "@/lib/api";
import { formatDateTimeZone } from "@/lib/utils";
import { ChevronDown, GitBranch, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";

/**
 * Version history side panel — the third editor column, next to the settings
 * accordions.
 *
 * Mirrors the publishing model in docs/AGENT_VERSIONING.md: published versions
 * are immutable and one of them is live; at most one draft exists and it is
 * always the newest version, so it is the only editable entry. "Restore" opens
 * a fresh draft from an old version rather than rewriting history.
 */
export default function VersionsPanel({
  versions,
  loading,
  error,
  selected,
  onSelect,
  onClose,
  onRestore,
  onDiscard,
  onPublish,
  busy,
}: {
  versions: RawAgentVersion[] | null;
  loading: boolean;
  error: string | null;
  /** Version currently shown in the editor. */
  selected: number;
  onSelect: (version: number) => void;
  onClose: () => void;
  onRestore: (version: number) => void;
  onDiscard: (version: number) => void;
  onPublish: (version: number) => void;
  busy: boolean;
}) {
  const [query, setQuery] = useState("");
  const [showDrafts, setShowDrafts] = useState(true);
  const [showPublished, setShowPublished] = useState(true);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return versions ?? [];
    return (versions ?? []).filter((v) =>
      [`v${v.version}`, v.version_title, v.version_description, v.agent_name]
        .filter(Boolean)
        .some((field) => String(field).toLowerCase().includes(q)),
    );
  }, [versions, query]);

  const drafts = matches.filter((v) => !v.is_published);
  const published = matches.filter((v) => v.is_published);
  // The newest version is the working copy; everything below it is frozen.
  const latest = versions?.[0]?.version;

  return (
    <div
      className={`${SIDE_PANEL_WIDTH} flex flex-col overflow-hidden rounded-xl border border-line bg-card`}
    >
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-3">
        <span className="text-[13px] font-semibold">Versions</span>
        <button
          onClick={onClose}
          aria-label="Close versions"
          className="ml-auto rounded-md p-1 text-sub hover:bg-app cursor-pointer"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="shrink-0 p-3">
        <SearchInput value={query} onChange={setQuery} placeholder="Search versions…" />
      </div>

      <div className="min-h-0 grow overflow-y-auto px-2 pb-3">
        {error ? (
          <p className="px-2 py-3 text-[13px] text-bad">{error}</p>
        ) : loading && versions === null ? (
          <p className="px-2 py-3 text-[13px] text-sub">Loading…</p>
        ) : matches.length === 0 ? (
          <p className="px-2 py-3 text-[13px] text-sub">
            {query ? "No versions match your search." : "No versions yet."}
          </p>
        ) : (
          <>
            {drafts.length > 0 && (
              <Group
                label="Draft"
                open={showDrafts}
                onToggle={() => setShowDrafts((v) => !v)}
                count={drafts.length}
              >
                {drafts.map((v) => (
                  <VersionRow
                    key={v.version}
                    version={v}
                    selected={v.version === selected}
                    editable={v.version === latest}
                    onSelect={() => onSelect(v.version)}
                    busy={busy}
                    actions={
                      <>
                        <Button size="sm" variant="primary" onClick={() => onPublish(v.version)}>
                          Publish
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={() => onDiscard(v.version)}
                          title="Discard this draft"
                        >
                          <Trash2 className="size-3.5" />
                          Discard
                        </Button>
                      </>
                    }
                  />
                ))}
              </Group>
            )}
            {published.length > 0 && (
              <Group
                label="Published"
                open={showPublished}
                onToggle={() => setShowPublished((v) => !v)}
                count={published.length}
              >
                {published.map((v) => (
                  <VersionRow
                    key={v.version}
                    version={v}
                    selected={v.version === selected}
                    editable={v.version === latest}
                    onSelect={() => onSelect(v.version)}
                    busy={busy}
                    actions={
                      <>
                        <Button
                          size="sm"
                          onClick={() => onRestore(v.version)}
                          title="Open a draft with this version's config"
                        >
                          <GitBranch className="size-3.5" />
                          Restore
                        </Button>
                        {!v.is_live && (
                          <Button
                            size="sm"
                            variant="primary"
                            onClick={() => onPublish(v.version)}
                            title="Send live calls back to this version"
                          >
                            Make live
                          </Button>
                        )}
                      </>
                    }
                  />
                ))}
              </Group>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Group({
  label,
  count,
  open,
  onToggle,
  children,
}: {
  label: string;
  count: number;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-1">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-1 rounded-lg px-2 py-1.5 text-[12px] font-semibold uppercase tracking-wide text-faint hover:bg-app cursor-pointer"
      >
        {label}
        <span className="font-normal normal-case tracking-normal">({count})</span>
        <ChevronDown
          className={`ml-auto size-3.5 transition-transform ${open ? "" : "-rotate-90"}`}
        />
      </button>
      {open && children}
    </div>
  );
}

function VersionRow({
  version,
  selected,
  editable,
  onSelect,
  actions,
  busy,
}: {
  version: RawAgentVersion;
  selected: boolean;
  editable: boolean;
  onSelect: () => void;
  actions: React.ReactNode;
  busy: boolean;
}) {
  const stamp = version.is_published
    ? version.published_timestamp ?? version.last_modification_timestamp
    : version.last_modification_timestamp;
  return (
    <div
      className={`rounded-lg border px-2.5 py-2 transition-colors ${
        selected ? "border-accent bg-accent/5" : "border-transparent hover:bg-app"
      }`}
    >
      <button onClick={onSelect} className="w-full text-left cursor-pointer">
        <div className="flex items-start gap-2">
          <span
            aria-hidden
            className={`mt-1 size-3 shrink-0 rounded-full border-2 ${
              selected ? "border-accent bg-accent" : "border-line"
            }`}
          />
          <div className="min-w-0 grow">
            <div className="flex items-baseline gap-1.5">
              <span className="text-[13px] font-semibold">
                {version.is_published ? "" : "Draft "}V{version.version}
              </span>
              {version.version_title && (
                <span className="truncate text-[12px] text-sub">{version.version_title}</span>
              )}
              {version.is_live && (
                <span className="ml-auto shrink-0 rounded-full bg-green-50 px-1.5 py-0.5 text-[11px] font-medium text-green-700">
                  Live
                </span>
              )}
              {!version.is_published && editable && (
                <span className="ml-auto shrink-0 rounded-full bg-app px-1.5 py-0.5 text-[11px] font-medium text-sub">
                  Editing
                </span>
              )}
            </div>
            {version.version_description && (
              <p className="mt-0.5 line-clamp-2 text-[12px] text-sub">
                {version.version_description}
              </p>
            )}
            <p className="mt-0.5 text-[11px] text-faint">
              {version.is_published ? "Published" : "Updated"} {formatDateTimeZone(stamp)}
            </p>
            {version.base_version !== null && (
              <p className="text-[11px] text-faint">From V{version.base_version}</p>
            )}
          </div>
        </div>
      </button>
      {selected && (
        <div className="mt-2 flex flex-wrap gap-1.5 pl-5" aria-busy={busy}>
          {actions}
        </div>
      )}
    </div>
  );
}
