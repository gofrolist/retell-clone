"use client";

import AgentsTable from "@/components/agents/AgentsTable";
import CreateAgentModal from "@/components/agents/CreateAgentModal";
import SecondaryPanel from "@/components/shell/SecondaryPanel";
import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import Pagination from "@/components/ui/Pagination";
import SearchInput from "@/components/ui/SearchInput";
import { api } from "@/lib/api";
import type { AgentFolder } from "@/lib/types";
import { useApiData } from "@/lib/useApiData";
import { cn } from "@/lib/utils";
import { Bot, ChevronDown, Folder, MoreVertical, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

type FolderModalState = { mode: "create" } | { mode: "rename"; folder: AgentFolder } | null;

function FolderModal({
  state,
  onClose,
  onSaved,
}: {
  state: FolderModalState;
  onClose: () => void;
  onSaved: (folder: AgentFolder) => void;
}) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setName(state?.mode === "rename" ? state.folder.folder_name : "");
    setError(null);
  }, [state]);

  const save = async () => {
    if (!state) return;
    setSaving(true);
    setError(null);
    try {
      const folder =
        state.mode === "create"
          ? await api.createAgentFolder(name.trim())
          : await api.renameAgentFolder(state.folder.folder_id, name.trim());
      onSaved(folder);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save folder");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={state !== null}
      onClose={onClose}
      title={state?.mode === "rename" ? "Rename Folder" : "Add Folder"}
      width="max-w-md"
      footer={
        <>
          {error && <span className="mr-auto text-[12.5px] text-bad">{error}</span>}
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button variant="primary" onClick={save} disabled={saving || !name.trim()}>
            {saving ? "Saving…" : state?.mode === "rename" ? "Save" : "Create"}
          </Button>
        </>
      }
    >
      <Field label="Folder Name">
        <TextInput
          placeholder="Enter"
          value={name}
          autoFocus
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && name.trim() && !saving) save();
          }}
        />
      </Field>
    </Modal>
  );
}

function FoldersPanel({
  folders,
  selectedId,
  onSelect,
  onAdd,
  onRename,
  onDelete,
}: {
  folders: AgentFolder[];
  selectedId: string | null;
  onSelect: (folderId: string | null) => void;
  onAdd: () => void;
  onRename: (folder: AgentFolder) => void;
  onDelete: (folder: AgentFolder) => void;
}) {
  const [menuFor, setMenuFor] = useState<string | null>(null);

  return (
    <div className="p-3">
      <button
        onClick={() => onSelect(null)}
        className={cn(
          "flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-[13.5px] cursor-pointer",
          selectedId === null
            ? "bg-white font-medium shadow-sm border border-line"
            : "text-sub hover:bg-black/4 hover:text-ink",
        )}
      >
        <Bot className="size-4 text-sub shrink-0" strokeWidth={1.8} />
        All Agents
      </button>
      <div className="mt-5 mb-1 flex items-center justify-between px-2">
        <span className="text-[11px] font-semibold tracking-wider text-faint">FOLDERS</span>
        <button
          onClick={onAdd}
          className="rounded p-0.5 text-faint hover:bg-black/5 hover:text-ink cursor-pointer"
          aria-label="Add folder"
        >
          <Plus className="size-3.5" />
        </button>
      </div>
      {folders.map((f) => (
        <div key={f.folder_id} className="group relative">
          <button
            onClick={() => onSelect(f.folder_id)}
            className={cn(
              "flex w-full items-center gap-2.5 rounded-lg px-3 py-2 pr-8 text-[13.5px] cursor-pointer",
              selectedId === f.folder_id
                ? "bg-white font-medium shadow-sm border border-line"
                : "text-sub hover:bg-black/4 hover:text-ink",
            )}
          >
            <Folder className="size-4 shrink-0" strokeWidth={1.8} />
            <span className="truncate">{f.folder_name}</span>
          </button>
          <button
            onClick={() => setMenuFor(menuFor === f.folder_id ? null : f.folder_id)}
            className={cn(
              "absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-faint hover:bg-black/5 hover:text-ink cursor-pointer",
              menuFor === f.folder_id ? "opacity-100" : "opacity-0 group-hover:opacity-100",
            )}
            aria-label={`Folder actions for ${f.folder_name}`}
            aria-expanded={menuFor === f.folder_id}
          >
            <MoreVertical className="size-3.5" />
          </button>
          {menuFor === f.folder_id && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setMenuFor(null)} />
              <div className="absolute right-1 top-8 z-20 w-36 rounded-lg border border-line bg-white py-1 shadow-lg">
                <button
                  onClick={() => {
                    setMenuFor(null);
                    onRename(f);
                  }}
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-app cursor-pointer"
                >
                  <Pencil className="size-3.5 text-sub" />
                  Rename
                </button>
                <button
                  onClick={() => {
                    setMenuFor(null);
                    onDelete(f);
                  }}
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] text-bad hover:bg-red-50 cursor-pointer"
                >
                  <Trash2 className="size-3.5" />
                  Delete
                </button>
              </div>
            </>
          )}
        </div>
      ))}
      {folders.length === 0 && (
        <p className="px-3 py-1.5 text-[12.5px] text-faint">No folders yet.</p>
      )}
    </div>
  );
}

// Fields the backend's CreateAgentRequest accepts (backend/app/schemas.py).
// Anything else in an imported Retell export is dropped instead of 422-ing.
const AGENT_IMPORT_FIELDS = [
  "agent_id",
  "agent_name",
  "voice_id",
  "voice_model",
  "voice_temperature",
  "voice_speed",
  "volume",
  "language",
  "responsiveness",
  "interruption_sensitivity",
  "enable_backchannel",
  "backchannel_frequency",
  "backchannel_words",
  "reminder_trigger_ms",
  "reminder_max_count",
  "ambient_sound",
  "ambient_sound_volume",
  "webhook_url",
  "boosted_keywords",
  "pronunciation_dictionary",
  "normalize_for_speech",
  "end_call_after_silence_ms",
  "max_call_duration_ms",
  "voicemail_option",
  "enable_voicemail_detection",
  "post_call_analysis_data",
  "post_call_analysis_model",
  "begin_message_delay_ms",
  "stt_mode",
  "denoising_mode",
  "opt_out_sensitive_data_storage",
] as const;

const LLM_IMPORT_FIELDS = [
  "model",
  "model_temperature",
  "general_prompt",
  "general_tools",
  "states",
  "starting_state",
  "begin_message",
  "start_speaker",
  "default_dynamic_variables",
  "knowledge_base_ids",
] as const;

function pick(
  src: Record<string, unknown>,
  fields: readonly string[],
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    if (src[f] !== undefined && src[f] !== null) out[f] = src[f];
  }
  return out;
}

export default function AgentsPage() {
  const { data, setData: setAgents, loading, error, reload } = useApiData(
    () => api.listAgents(),
  );
  const agents = useMemo(() => data ?? [], [data]);
  const { data: folderData, setData: setFolders } = useApiData(() => api.listAgentFolders());
  const folders = useMemo(() => folderData ?? [], [folderData]);
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(null);
  const [folderModal, setFolderModal] = useState<FolderModalState>(null);
  const [folderError, setFolderError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [page, setPage] = useState(1);
  const fileRef = useRef<HTMLInputElement>(null);

  const selectedFolder = folders.find((f) => f.folder_id === selectedFolderId) ?? null;

  const upsertFolder = (folder: AgentFolder) => {
    setFolders((prev) => {
      const rest = (prev ?? []).filter((f) => f.folder_id !== folder.folder_id);
      return [...rest, folder].sort((a, b) => a.folder_name.localeCompare(b.folder_name));
    });
  };

  const deleteFolder = async (folder: AgentFolder) => {
    if (
      !window.confirm(
        `Delete folder "${folder.folder_name}"? Agents in it are kept and moved out of the folder.`,
      )
    )
      return;
    setFolderError(null);
    try {
      await api.deleteAgentFolder(folder.folder_id);
      setFolders((prev) => (prev ?? []).filter((f) => f.folder_id !== folder.folder_id));
      setAgents((prev) =>
        (prev ?? []).map((a) =>
          a.folder_id === folder.folder_id ? { ...a, folder_id: null } : a,
        ),
      );
      if (selectedFolderId === folder.folder_id) setSelectedFolderId(null);
    } catch (e) {
      setFolderError(
        `Failed to delete "${folder.folder_name}": ${e instanceof Error ? e.message : "unknown error"}`,
      );
    }
  };

  const handleImportFile = async (file: File) => {
    setImporting(true);
    setImportError(null);
    try {
      const parsed: unknown = JSON.parse(await file.text());
      if (typeof parsed !== "object" || parsed === null) {
        throw new Error("Not a Retell agent JSON export");
      }
      const root = parsed as Record<string, unknown>;
      const rawAgent = (
        typeof root.agent === "object" && root.agent !== null ? root.agent : root
      ) as Record<string, unknown>;

      const payload = pick(rawAgent, AGENT_IMPORT_FIELDS);

      // Retell exports may inline the LLM config; recreate it so the
      // response_engine points at an LLM that exists in this workspace.
      const embeddedLlm =
        root.retellLlmData ?? root.retell_llm_data ?? root.llm_data ?? root.retell_llm;
      let responseEngine = rawAgent.response_engine as
        | { type?: string; llm_id?: string }
        | undefined;
      if (typeof embeddedLlm === "object" && embeddedLlm !== null) {
        const llm = await api.createLlm(
          pick(embeddedLlm as Record<string, unknown>, LLM_IMPORT_FIELDS),
        );
        responseEngine = { type: "retell-llm", llm_id: llm.llm_id };
      } else if (!responseEngine?.type) {
        const llm = await api.createLlm({});
        responseEngine = { type: "retell-llm", llm_id: llm.llm_id };
      }
      payload.response_engine = responseEngine;
      if (!payload.voice_id) payload.voice_id = "cartesia-sonic-english";

      await api.createAgent(payload);
      await reload();
    } catch (e) {
      setImportError(
        e instanceof SyntaxError
          ? "Import failed: file is not valid JSON"
          : `Import failed: ${e instanceof Error ? e.message : "unknown error"}`,
      );
    } finally {
      setImporting(false);
    }
  };

  const filtered = useMemo(
    () =>
      agents.filter(
        (a) =>
          a.agent_name.toLowerCase().includes(query.toLowerCase()) &&
          (selectedFolderId === null || a.folder_id === selectedFolderId),
      ),
    [agents, query, selectedFolderId],
  );

  return (
    <SecondaryPanel
      panel={
        <FoldersPanel
          folders={folders}
          selectedId={selectedFolderId}
          onSelect={setSelectedFolderId}
          onAdd={() => setFolderModal({ mode: "create" })}
          onRename={(folder) => setFolderModal({ mode: "rename", folder })}
          onDelete={deleteFolder}
        />
      }
    >
      <div className="flex h-full flex-col px-6 pt-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h1 className="text-[17px] font-semibold">
            {selectedFolder?.folder_name ?? "All Agents"}
          </h1>
          <div className="flex items-center gap-2">
            <SearchInput value={query} onChange={setQuery} className="w-64" />
            <input
              ref={fileRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) handleImportFile(file);
              }}
            />
            <Button disabled={importing} onClick={() => fileRef.current?.click()}>
              {importing ? "Importing…" : "Import"}
            </Button>
            <Button variant="primary" onClick={() => setCreateOpen(true)}>
              Create an Agent
              <ChevronDown className="size-3.5" />
            </Button>
          </div>
        </div>

        {(importError ?? folderError) && (
          <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[12.5px] text-red-600">
            <span>{importError ?? folderError}</span>
            <button
              onClick={() => {
                setImportError(null);
                setFolderError(null);
              }}
              className="font-medium hover:underline cursor-pointer"
            >
              Dismiss
            </button>
          </div>
        )}

        <div className="min-h-0 grow overflow-y-auto">
          {loading ? (
            <div className="py-16 text-center text-[13px] text-sub">Loading agents…</div>
          ) : error ? (
            <div className="py-16 text-center">
              <p className="text-[13px] text-bad">{error}</p>
              <Button className="mt-3" onClick={reload}>
                Retry
              </Button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="py-16 text-center text-[13px] text-sub">
              {agents.length === 0
                ? "No agents yet. Create or import one to get started."
                : query
                  ? "No agents match your search."
                  : "This folder is empty. Move agents here from the row menu."}
            </div>
          ) : (
            <AgentsTable
              agents={filtered}
              folders={folders}
              onDeleted={(agentId) =>
                setAgents((prev) => (prev ?? []).filter((a) => a.agent_id !== agentId))
              }
              onMoved={(agentId, folderId) =>
                setAgents((prev) =>
                  (prev ?? []).map((a) =>
                    a.agent_id === agentId ? { ...a, folder_id: folderId } : a,
                  ),
                )
              }
            />
          )}
        </div>

        <Pagination page={page} totalPages={1} onPage={setPage} />
      </div>
      <CreateAgentModal open={createOpen} onClose={() => setCreateOpen(false)} />
      <FolderModal
        state={folderModal}
        onClose={() => setFolderModal(null)}
        onSaved={upsertFolder}
      />
    </SecondaryPanel>
  );
}
