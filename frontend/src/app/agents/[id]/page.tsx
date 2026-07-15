"use client";

import EditorHeader from "@/components/editor/EditorHeader";
import MetaRow from "@/components/editor/MetaRow";
import PromptEditor from "@/components/editor/PromptEditor";
import SelectorRow from "@/components/editor/SelectorRow";
import TestPanel from "@/components/editor/TestPanel";
import WelcomeMessage from "@/components/editor/WelcomeMessage";
import CallSettingsSection from "@/components/editor/sections/CallSettingsSection";
import FunctionsSection from "@/components/editor/sections/FunctionsSection";
import KnowledgeBaseSection from "@/components/editor/sections/KnowledgeBaseSection";
import McpSection from "@/components/editor/sections/McpSection";
import PostCallSection from "@/components/editor/sections/PostCallSection";
import SecuritySection from "@/components/editor/sections/SecuritySection";
import SpeechSettingsSection from "@/components/editor/sections/SpeechSettingsSection";
import TranscriptionSection from "@/components/editor/sections/TranscriptionSection";
import WebhookSection from "@/components/editor/sections/WebhookSection";
import Accordion from "@/components/ui/Accordion";
import { api, type RawAgent, type RawLlm } from "@/lib/api";
import type { Voice } from "@/lib/types";
import { DEFAULT_POST_CALL_ANALYSIS_MODEL } from "@/lib/models";
import {
  AudioLines,
  Captions,
  Headset,
  LayoutGrid,
  Library,
  LineChart,
  Plug,
  ShieldCheck,
  Webhook,
} from "lucide-react";
import Link from "next/link";
import { use, useEffect, useState } from "react";

const num = (v: unknown, fallback: number): number =>
  typeof v === "number" ? v : fallback;
const str = (v: unknown, fallback: string): string =>
  typeof v === "string" ? v : fallback;

export default function AgentEditorPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [agent, setAgent] = useState<RawAgent | null>(null);
  const [llm, setLlm] = useState<RawLlm | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [voices, setVoices] = useState<Voice[]>([]);

  // Dirty state: fields the user changed but hasn't saved yet. Displayed
  // values are `{...server, ...draft}`; Save PATCHes only the drafts.
  const [agentDraft, setAgentDraft] = useState<Partial<RawAgent>>({});
  const [llmDraft, setLlmDraft] = useState<Partial<RawLlm>>({});
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getAgentDetail(id)
      .then((detail) => {
        if (cancelled) return;
        setAgent(detail.agent);
        setLlm(detail.llm);
      })
      .catch((e) => {
        if (!cancelled) {
          setLoadError(e instanceof Error ? e.message : "Failed to load agent");
        }
      });
    api
      .listVoices()
      .then((v) => !cancelled && setVoices(v))
      .catch(() => {}); // voice list failure degrades to showing the raw id
    return () => {
      cancelled = true;
    };
  }, [id]);

  const setAgentField = <K extends keyof RawAgent & string>(field: K, value: RawAgent[K]) =>
    setAgentDraft((prev) => ({ ...prev, [field]: value }));
  const setLlmField = <K extends keyof RawLlm & string>(field: K, value: RawLlm[K]) =>
    setLlmDraft((prev) => ({ ...prev, [field]: value }));

  const dirty = Object.keys(agentDraft).length > 0 || Object.keys(llmDraft).length > 0;

  const handleSave = async () => {
    if (!agent || saving) return;
    setSaving(true);
    setActionError(null);
    try {
      if (Object.keys(agentDraft).length > 0) {
        setAgent(await api.updateAgent(agent.agent_id, agentDraft));
        setAgentDraft({});
      }
      if (llm && Object.keys(llmDraft).length > 0) {
        setLlm(await api.updateLlm(llm.llm_id, llmDraft));
        setLlmDraft({});
      }
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handlePublish = async () => {
    if (!agent || publishing) return;
    setPublishing(true);
    setActionError(null);
    try {
      setAgent(await api.publishAgent(agent.agent_id));
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Publish failed");
    } finally {
      setPublishing(false);
    }
  };

  if (loadError) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 bg-app">
        <p className="text-[13px] text-bad">{loadError}</p>
        <Link href="/agents" className="text-[13px] font-medium text-accent-deep hover:underline">
          Back to agents
        </Link>
      </div>
    );
  }

  if (!agent) {
    return (
      <div className="flex h-screen items-center justify-center text-sub">
        Loading agent…
      </div>
    );
  }

  // Server value overlaid with unsaved edits.
  const view: RawAgent = { ...agent, ...agentDraft };
  const llmView: RawLlm | null = llm ? { ...llm, ...llmDraft } : null;

  return (
    <div className="flex h-screen flex-col bg-app">
      <EditorHeader
        name={view.agent_name ?? ""}
        onName={(v) => setAgentField("agent_name", v)}
        version={agent.version}
        isPublished={agent.is_published}
        dirty={dirty}
        saving={saving}
        onSave={handleSave}
        publishing={publishing}
        onPublish={handlePublish}
        error={actionError}
      />
      <div className="flex min-h-0 grow gap-3 p-3">
        {/* left: prompt column */}
        <div className="flex min-w-0 flex-[1.6] flex-col overflow-y-auto rounded-xl border border-line bg-card p-4">
          <MetaRow agentId={agent.agent_id} llm={llmView} />
          <div className="mt-3">
            <SelectorRow
              model={llmView?.model ?? ""}
              onModel={llm ? (v) => setLlmField("model", v) : undefined}
              voiceId={view.voice_id}
              onVoice={(v) => setAgentField("voice_id", v)}
              language={view.language}
              onLanguage={(v) => setAgentField("language", v)}
              voices={voices}
            />
          </div>
          {llmView ? (
            <>
              <div className="mt-3 flex min-h-0 grow flex-col">
                <PromptEditor
                  value={llmView.general_prompt ?? ""}
                  onChange={(v) => setLlmField("general_prompt", v)}
                  agentVariables={Object.keys(llmView.default_dynamic_variables ?? {})}
                />
              </div>
              <WelcomeMessage
                startSpeaker={llmView.start_speaker}
                onStartSpeaker={(v) => setLlmField("start_speaker", v)}
                message={llmView.begin_message ?? ""}
                onMessage={(v) => setLlmField("begin_message", v)}
                pause={num(view.begin_message_delay_ms, 0) / 1000}
              />
            </>
          ) : (
            <p className="mt-4 text-[13px] text-sub">
              This agent uses a conversation flow; prompt editing is not available yet.
            </p>
          )}
        </div>

        {/* middle: settings accordions */}
        <div className="min-w-0 flex-[1.1] overflow-y-auto rounded-xl border border-line bg-card">
          <Accordion icon={LayoutGrid} title="Functions" defaultOpen>
            {llmView ? (
              <FunctionsSection
                tools={llmView.general_tools ?? []}
                onChange={(tools) => setLlmField("general_tools", tools)}
              />
            ) : (
              <p className="text-[13px] text-sub">Not available for conversation-flow agents.</p>
            )}
          </Accordion>
          <Accordion icon={Library} title="Knowledge Base">
            {llmView ? (
              <KnowledgeBaseSection
                attachedIds={llmView.knowledge_base_ids ?? []}
                onChange={(ids) => setLlmField("knowledge_base_ids", ids)}
              />
            ) : (
              <p className="text-[13px] text-sub">Not available for conversation-flow agents.</p>
            )}
          </Accordion>
          <Accordion icon={AudioLines} title="Speech Settings">
            <SpeechSettingsSection
              ambientSound={str(view.ambient_sound, "none")}
              onAmbientSound={(v) => setAgentField("ambient_sound", v)}
              responsiveness={view.responsiveness}
              onResponsiveness={(v) => setAgentField("responsiveness", v)}
              interruptionSensitivity={view.interruption_sensitivity}
              onInterruptionSensitivity={(v) => setAgentField("interruption_sensitivity", v)}
              reminderTriggerMs={view.reminder_trigger_ms}
              onReminderTriggerMs={(v) => setAgentField("reminder_trigger_ms", v)}
              reminderMaxCount={view.reminder_max_count}
              onReminderMaxCount={(v) => setAgentField("reminder_max_count", v)}
            />
          </Accordion>
          <Accordion icon={Captions} title="Realtime Transcription Settings">
            <TranscriptionSection
              denoisingMode={str(view.denoising_mode, "noise-cancellation")}
              onDenoisingMode={(v) => setAgentField("denoising_mode", v)}
              sttMode={str(view.stt_mode, "fast")}
              onSttMode={(v) => setAgentField("stt_mode", v)}
              keywords={view.boosted_keywords ?? []}
              onKeywords={(k) => setAgentField("boosted_keywords", k.length ? k : null)}
            />
          </Accordion>
          <Accordion icon={Headset} title="Call Settings">
            <CallSettingsSection
              voicemail={view.enable_voicemail_detection}
              onVoicemail={(v) => setAgentField("enable_voicemail_detection", v)}
              endCallAfterSilenceMs={num(view.end_call_after_silence_ms, 600000)}
              onEndCallAfterSilenceMs={(v) => setAgentField("end_call_after_silence_ms", v)}
              maxCallDurationMs={num(view.max_call_duration_ms, 3600000)}
              onMaxCallDurationMs={(v) => setAgentField("max_call_duration_ms", v)}
            />
          </Accordion>
          <Accordion icon={LineChart} title="Post-Call Data Extraction">
            <PostCallSection
              model={str(view.post_call_analysis_model, DEFAULT_POST_CALL_ANALYSIS_MODEL)}
              onModel={(v) => setAgentField("post_call_analysis_model", v)}
            />
          </Accordion>
          <Accordion icon={ShieldCheck} title="Security & Fallback Settings">
            <SecuritySection
              optOut={Boolean(view.opt_out_sensitive_data_storage)}
              onOptOut={(v) => setAgentField("opt_out_sensitive_data_storage", v)}
            />
          </Accordion>
          <Accordion icon={Webhook} title="Webhook Settings">
            <WebhookSection
              url={view.webhook_url ?? ""}
              onUrl={(v) => setAgentField("webhook_url", v || null)}
            />
          </Accordion>
          <Accordion icon={Plug} title="MCPs">
            <McpSection />
          </Accordion>
        </div>

        {/* right: test panel */}
        <div className="min-w-0 flex-1 overflow-hidden rounded-xl border border-line bg-card">
          <TestPanel />
        </div>
      </div>
    </div>
  );
}
