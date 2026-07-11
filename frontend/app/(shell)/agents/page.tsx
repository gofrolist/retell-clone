"use client";

import AgentsTable from "@/components/agents/AgentsTable";
import CreateAgentModal from "@/components/agents/CreateAgentModal";
import SecondaryPanel from "@/components/shell/SecondaryPanel";
import Button from "@/components/ui/Button";
import Pagination from "@/components/ui/Pagination";
import SearchInput from "@/components/ui/SearchInput";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/types";
import { Bot, ChevronDown, Folder, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

function FoldersPanel() {
  return (
    <div className="p-3">
      <button className="flex w-full items-center gap-2.5 rounded-lg bg-white px-3 py-2.5 text-[13.5px] font-medium shadow-sm border border-line cursor-pointer">
        <Bot className="size-4 text-sub" strokeWidth={1.8} />
        All Agents
      </button>
      <div className="mt-5 mb-1 flex items-center justify-between px-2">
        <span className="text-[11px] font-semibold tracking-wider text-faint">FOLDERS</span>
        <button className="rounded p-0.5 text-faint hover:bg-black/5 hover:text-ink cursor-pointer" aria-label="Add folder">
          <Plus className="size-3.5" />
        </button>
      </div>
      <button className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] text-sub hover:bg-black/4 hover:text-ink cursor-pointer">
        <Folder className="size-4" strokeWidth={1.8} />
        Template Agents
      </button>
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
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [page, setPage] = useState(1);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setAgents(await api.listAgents());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load agents");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

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
      await load();
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
      agents.filter((a) =>
        a.agent_name.toLowerCase().includes(query.toLowerCase()),
      ),
    [agents, query],
  );

  return (
    <SecondaryPanel panel={<FoldersPanel />}>
      <div className="flex h-full flex-col px-6 pt-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h1 className="text-[17px] font-semibold">All Agents</h1>
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

        {importError && (
          <div className="mb-3 flex items-center justify-between gap-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[12.5px] text-red-600">
            <span>{importError}</span>
            <button
              onClick={() => setImportError(null)}
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
              <Button className="mt-3" onClick={load}>
                Retry
              </Button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="py-16 text-center text-[13px] text-sub">
              {agents.length === 0
                ? "No agents yet. Create or import one to get started."
                : "No agents match your search."}
            </div>
          ) : (
            <AgentsTable
              agents={filtered}
              onDeleted={(agentId) =>
                setAgents((prev) => prev.filter((a) => a.agent_id !== agentId))
              }
            />
          )}
        </div>

        <Pagination page={page} totalPages={1} onPage={setPage} />
      </div>
      <CreateAgentModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </SecondaryPanel>
  );
}
