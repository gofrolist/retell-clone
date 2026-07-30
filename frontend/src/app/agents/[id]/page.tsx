"use client";

import EditorHeader, { type SaveState } from "@/components/editor/EditorHeader";
import MetaRow from "@/components/editor/MetaRow";
import VersionsPanel from "@/components/editor/VersionsPanel";
import PromptEditor from "@/components/editor/PromptEditor";
import SelectorRow from "@/components/editor/SelectorRow";
import WelcomeMessage from "@/components/editor/WelcomeMessage";
import { SIDE_PANEL_WIDTH } from "@/components/editor/panelLayout";
import CallSettingsSection from "@/components/editor/sections/CallSettingsSection";
import FunctionsSection, { type Tool } from "@/components/editor/sections/FunctionsSection";
import KnowledgeBaseSection from "@/components/editor/sections/KnowledgeBaseSection";
import McpSection from "@/components/editor/sections/McpSection";
import PostCallSection from "@/components/editor/sections/PostCallSection";
import SecuritySection, {
  DynamicVariablesRow,
} from "@/components/editor/sections/SecuritySection";
import SpeechSettingsSection from "@/components/editor/sections/SpeechSettingsSection";
import TranscriptionSection from "@/components/editor/sections/TranscriptionSection";
import WebhookSection from "@/components/editor/sections/WebhookSection";
import TestPanel from "@/components/editor/TestPanel";
import SimulationTab from "@/components/simulation/SimulationTab";
import FlowEditor from "@/components/flow/FlowEditor";
import { flowReducer, stampToolIds, type FlowAction } from "@/components/flow/flowModel";
import Accordion from "@/components/ui/Accordion";
import { Field, TextInput } from "@/components/ui/Field";
import {
  api,
  rawAgentFromChatAgent,
  type McpServer,
  type RawAgent,
  type RawAgentVersion,
  type RawConversationFlow,
  type RawLlm,
} from "@/lib/api";
import type { Voice } from "@/lib/types";
import { DEFAULT_POST_CALL_ANALYSIS_MODEL } from "@/lib/models";
import {
  AudioLines,
  Braces,
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
import { use, useCallback, useEffect, useRef, useState } from "react";

/** How long editing pauses before the draft is saved. */
const AUTOSAVE_MS = 800;

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
  const [flow, setFlow] = useState<RawConversationFlow | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [voices, setVoices] = useState<Voice[]>([]);
  // Chat agent: no voice, telephony, versions or call simulation. Everything
  // it does have (prompt, tools, knowledge base, webhook) is edited here too.
  const [isChat, setIsChat] = useState(false);

  // Edits the user has made but that haven't reached the draft yet. Displayed
  // values are `{...server, ...draft}`; the autosave timer PATCHes the drafts.
  const [agentDraft, setAgentDraft] = useState<Partial<RawAgent>>({});
  const [llmDraft, setLlmDraft] = useState<Partial<RawLlm>>({});
  const [flowDraft, setFlowDraft] = useState<Partial<RawConversationFlow>>({});
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [publishing, setPublishing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  // "create" edits the agent; "simulation" runs test cases and manual tests.
  const [tab, setTab] = useState("create");

  const [versions, setVersions] = useState<RawAgentVersion[] | null>(null);
  const [versionsError, setVersionsError] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [versionBusy, setVersionBusy] = useState(false);

  // The newest version is the working copy; every older one is frozen, so
  // viewing one puts the editor in read-only mode. Strictly `<`, never `!==`:
  // `versions` is refreshed asynchronously after a save, so between the PATCH
  // response (agent already on the new draft) and that refetch the agent is
  // *ahead* of the list — treating that as read-only would lock the editor
  // and drop keystrokes mid-typing.
  const latestVersion = versions?.[0]?.version ?? agent?.version ?? 0;
  const readOnly = agent != null && agent.version < latestVersion;

  const loadVersions = useCallback(async () => {
    try {
      setVersions(await api.getAgentVersions(id));
      setVersionsError(null);
    } catch (e) {
      setVersionsError(e instanceof Error ? e.message : "Failed to load versions");
    }
  }, [id]);

  useEffect(() => {
    let cancelled = false;
    api
      .getAgentDetail(id)
      .then((detail) => {
        if (cancelled) return;
        setAgent(detail.agent);
        setLlm(detail.llm);
        setFlow(detail.flow);
        setIsChat(detail.is_chat);
        // Chat agents have no version endpoints — asking would 404.
        if (!detail.is_chat) void loadVersions();
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
  }, [id, loadVersions]);

  // --------------------------------------------------------------- autosave
  // Refs mirror the pending edits so the debounce timer and the unmount flush
  // read current values without re-arming on every keystroke.
  const agentDraftRef = useRef(agentDraft);
  const llmDraftRef = useRef(llmDraft);
  const flowDraftRef = useRef(flowDraft);
  const llmIdRef = useRef<string | null>(null);
  const flowIdRef = useRef<string | null>(null);
  useEffect(() => {
    agentDraftRef.current = agentDraft;
    llmDraftRef.current = llmDraft;
    flowDraftRef.current = flowDraft;
    llmIdRef.current = llm?.llm_id ?? null;
    flowIdRef.current = flow?.conversation_flow_id ?? null;
  });

  /** Save pending edits; returns the agent as the server now has it. */
  const flush = useCallback(async (): Promise<RawAgent | null> => {
    const pendingAgent = agentDraftRef.current;
    const pendingLlm = llmDraftRef.current;
    const pendingFlow = flowDraftRef.current;
    const llmId = llmIdRef.current;
    const flowId = flowIdRef.current;
    if (
      !Object.keys(pendingAgent).length &&
      !Object.keys(pendingLlm).length &&
      !Object.keys(pendingFlow).length
    )
      return null;
    setSaveState("saving");
    setActionError(null);
    let saved: RawAgent | null = null;
    try {
      if (Object.keys(pendingAgent).length) {
        saved = isChat
          ? rawAgentFromChatAgent(await api.updateChatAgent(id, pendingAgent))
          : await api.updateAgent(id, pendingAgent);
        setAgent(saved);
        // Only clear what we sent: edits made while the request was in flight
        // must survive for the next flush.
        setAgentDraft((prev) => omitSent(prev, pendingAgent));
      }
      if (llmId && Object.keys(pendingLlm).length) {
        setLlm(await api.updateLlm(llmId, pendingLlm));
        setLlmDraft((prev) => omitSent(prev, pendingLlm));
        // A prompt edit forks the agent's draft server-side, so the agent's
        // version moved even though we didn't PATCH it. Without this refresh
        // the editor still thinks it is on the published version and locks
        // itself read-only. Chat agents don't version, so there's nothing to
        // re-read (and /get-agent would 404 on them anyway).
        if (!isChat && !Object.keys(pendingAgent).length) {
          saved = await api.getAgent(id);
          setAgent(saved);
        }
      }
      if (flowId && Object.keys(pendingFlow).length) {
        setFlow(await api.updateConversationFlow(flowId, pendingFlow));
        setFlowDraft((prev) => omitSent(prev, pendingFlow));
        // A flow edit forks the agent's draft server-side (update-conversation-flow
        // seeds and touches every agent using the flow), so the agent's version
        // moved even though we did not PATCH it -- exactly like the LLM branch
        // above. Without this refresh the editor still believes it is on the
        // published version and locks itself read-only mid-edit.
        if (!isChat && !Object.keys(pendingAgent).length) {
          saved = await api.getAgent(id);
          setAgent(saved);
        }
      }
      setSaveState("saved");
      // The first edit after a publish opens a draft — reflect that in the panel.
      if (!isChat) void loadVersions();
    } catch (e) {
      setSaveState("error");
      setActionError(e instanceof Error ? e.message : "Save failed");
    }
    return saved;
  }, [id, isChat, loadVersions]);

  const dirty =
    Object.keys(agentDraft).length > 0 ||
    Object.keys(llmDraft).length > 0 ||
    Object.keys(flowDraft).length > 0;

  useEffect(() => {
    if (!dirty) return;
    setSaveState("pending");
    const timer = setTimeout(() => void flush(), AUTOSAVE_MS);
    return () => clearTimeout(timer);
  }, [agentDraft, llmDraft, flowDraft, dirty, flush]);

  // A reload mid-debounce would drop the pending edit silently.
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (
        Object.keys(agentDraftRef.current).length ||
        Object.keys(llmDraftRef.current).length ||
        Object.keys(flowDraftRef.current).length
      ) {
        e.preventDefault();
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
      void flush(); // leaving the page: save what's pending
    };
  }, [flush]);

  const setAgentField = <K extends keyof RawAgent & string>(field: K, value: RawAgent[K]) =>
    setAgentDraft((prev) => ({ ...prev, [field]: value }));
  const setLlmField = <K extends keyof RawLlm & string>(field: K, value: RawLlm[K]) =>
    setLlmDraft((prev) => ({ ...prev, [field]: value }));

  // The flow the editor edits is the server value overlaid with unsaved
  // edits, exactly like `view` / `llmView` below.
  const flowView: RawConversationFlow | null = flow ? { ...flow, ...flowDraft } : null;

  /**
   * Bridges `FlowEditor`'s `dispatch(action)` onto the draft the autosave
   * effect above PATCHes. `flow` is a dependency (not just read once at
   * mount) so a save landing mid-edit is picked up rather than silently
   * overwritten by a stale closure's view of the pre-save flow.
   */
  const dispatchFlow = useCallback(
    (action: FlowAction) => {
      setFlowDraft((prev) => {
        const current = { ...flow, ...prev } as RawConversationFlow;
        const next = flowReducer(current, action);
        // Send only what actually changed: PATCHing all nineteen mutable
        // fields on every keystroke would make each edit look like a full
        // rewrite in the version history.
        const patch: Partial<RawConversationFlow> = {};
        for (const key of Object.keys(next)) {
          if (
            !Object.is(
              (next as Record<string, unknown>)[key],
              (current as Record<string, unknown>)[key],
            )
          ) {
            (patch as Record<string, unknown>)[key] = (next as Record<string, unknown>)[key];
          }
        }
        return { ...prev, ...patch };
      });
    },
    [flow],
  );

  // ----------------------------------------------------------- version moves

  /** Load a version into the editor: the latest is editable, older ones aren't. */
  const selectVersion = async (version: number) => {
    if (!agent || version === agent.version) return;
    setVersionBusy(true);
    setActionError(null);
    try {
      await flush();
      if (version === latestVersion) {
        const detail = await api.getAgentDetail(id);
        setAgent(detail.agent);
        setLlm(detail.llm);
        setFlow(detail.flow);
      } else {
        const pinned = await api.getAgentVersion(id, version);
        setAgent(pinned);
        setLlm(pinned.response_engine_config);
        // `conversation_flow` is the frozen graph this version actually ran
        // (Task 1) — without it, viewing an older version of a flow agent
        // would keep showing today's live graph under a "published versions
        // are immutable" banner, which is exactly the lie this feature
        // exists to prevent.
        setFlow(pinned.conversation_flow ?? null);
      }
      setAgentDraft({});
      setLlmDraft({});
      setFlowDraft({});
      setSaveState("idle");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Failed to load version");
    } finally {
      setVersionBusy(false);
    }
  };

  /** Reload the editor from the server after a version-level change. */
  const reload = async () => {
    const detail = await api.getAgentDetail(id);
    setAgent(detail.agent);
    setLlm(detail.llm);
    setFlow(detail.flow);
    setAgentDraft({});
    setLlmDraft({});
    setFlowDraft({});
    setSaveState("idle");
    await loadVersions();
  };

  /**
   * `version` names a specific history entry (the panel's "Make live"); pass
   * undefined to publish whatever the editor is working on. It cannot be
   * captured up front, because the flush below may fork a *new* draft — a
   * captured number would publish an older version and drop the on-screen edit.
   */
  const handlePublish = async (
    version: number | undefined,
    meta: { version_title?: string; version_description?: string } = {},
  ) => {
    if (!agent || publishing) return;
    setPublishing(true);
    setActionError(null);
    try {
      const saved = await flush(); // publish what's on screen, not the last debounce tick
      const target = version ?? saved?.version ?? agent.version;
      await api.publishAgentVersion(id, target, meta);
      await reload();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Publish failed");
    } finally {
      setPublishing(false);
    }
  };

  const handleRestore = async (version: number) => {
    if (versionBusy) return;
    setVersionBusy(true);
    setActionError(null);
    try {
      await flush();
      await api.createAgentVersion(id, version);
      await reload();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Failed to open a draft");
    } finally {
      setVersionBusy(false);
    }
  };

  const handleDiscard = async (version: number) => {
    if (versionBusy) return;
    setVersionBusy(true);
    setActionError(null);
    // Pending edits belong to the draft being thrown away.
    setAgentDraft({});
    setLlmDraft({});
    setFlowDraft({});
    try {
      await api.deleteAgentVersion(id, version);
      await reload();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Failed to discard draft");
    } finally {
      setVersionBusy(false);
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
        agent={agent}
        llm={llm}
        tab={tab}
        onTab={setTab}
        saveState={saveState}
        publishing={publishing}
        onPublish={(meta) => void handlePublish(undefined, meta)}
        viewingVersion={agent.version}
        readOnly={readOnly}
        onBranch={() => void handleRestore(agent.version)}
        panelOpen={panelOpen}
        onTogglePanel={() => setPanelOpen((v) => !v)}
        error={actionError}
        chat={isChat}
      />
      {readOnly && (
        <div className="flex shrink-0 items-center gap-2 border-b border-line bg-amber-50 px-4 py-1.5 text-[12px] text-amber-900">
          Viewing V{agent.version} — published versions are immutable. Use{" "}
          <span className="font-medium">Edit as new draft</span> to change it.
        </div>
      )}
      <div className="flex min-h-0 grow gap-2 overflow-x-auto p-2">
        {tab === "simulation" ? (
          isChat ? (
            // Nothing to simulate without calls — just the text test, in the
            // slot the settings column occupies on the Create tab.
            <div
              className={`ml-auto ${SIDE_PANEL_WIDTH} overflow-hidden rounded-xl border border-line bg-card`}
            >
              <TestPanel agentId={agent.agent_id} audio={false} />
            </div>
          ) : (
            <SimulationTab
              agentId={agent.agent_id}
              agentVersion={agent.version}
              llm={llm}
              dirty={dirty}
            />
          )
        ) : (
          <>
        {/* left: prompt/flow column — takes whatever the fixed panel leaves.
            `fieldset disabled` freezes a published version without threading a
            readOnly prop through every settings section (`FlowEditor` also
            takes its own explicit `readOnly`, since it is a canvas, not form
            controls — a disabled `<fieldset>` alone would not stop a drag).
            A flow agent still gets `overflow-y-auto`'s scroll only for
            `MetaRow`/`SelectorRow` above; the editor itself manages its own
            interior scrolling/panning, so it gets `overflow-hidden` instead
            of fighting the ancestor for scroll. */}
        <fieldset
          disabled={readOnly}
          className={`flex min-w-[420px] flex-1 flex-col rounded-xl border border-line bg-card p-4 ${
            flowView ? "overflow-hidden" : "overflow-y-auto"
          }`}
        >
          <MetaRow agentId={agent.agent_id} llm={llmView} chat={isChat} />
          <div className="mt-3">
            <SelectorRow
              model={llmView?.model ?? ""}
              onModel={llm ? (v) => setLlmField("model", v) : undefined}
              temperature={num(llmView?.model_temperature, 0)}
              onTemperature={llm ? (v) => setLlmField("model_temperature", v) : undefined}
              voiceId={isChat ? undefined : view.voice_id}
              onVoice={isChat ? undefined : (v) => setAgentField("voice_id", v)}
              language={view.language}
              onLanguage={(v) => setAgentField("language", v)}
              timezone={isChat ? undefined : (view.timezone ?? "")}
              onTimezone={isChat ? undefined : (v) => setAgentField("timezone", v || null)}
              voices={voices}
            />
          </div>
          {/* `llm` and `flow` are never both set for one agent (exactly one
              engine is populated server-side, see `api.ts`'s `getAgentDetail`),
              so `onModel`/`onTemperature` above are already `undefined` in the
              flow branch — the model/temperature controls disappear from
              `SelectorRow` on their own; `GlobalSettings` inside `FlowEditor`
              is the only place that edits them for a flow agent. */}
          {flowView ? (
            <div className="mt-3 flex min-h-0 grow flex-col">
              <FlowEditor flow={flowView} dispatch={dispatchFlow} readOnly={readOnly} />
            </div>
          ) : llmView ? (
            <>
              <div className="mt-3 flex min-h-0 grow flex-col">
                <PromptEditor
                  value={llmView.general_prompt ?? ""}
                  onChange={(v) => setLlmField("general_prompt", v)}
                  defaultDynamicVariables={llmView.default_dynamic_variables}
                />
              </div>
              <WelcomeMessage
                startSpeaker={llmView.start_speaker}
                onStartSpeaker={(v) => setLlmField("start_speaker", v)}
                message={llmView.begin_message ?? ""}
                onMessage={(v) => setLlmField("begin_message", v)}
                pause={isChat ? undefined : num(view.begin_message_delay_ms, 0) / 1000}
              />
            </>
          ) : (
            // Neither engine populated: a `custom-llm` response engine, which
            // this dashboard has no create path for and does not edit here.
            <p className="mt-4 text-[13px] text-sub">
              This agent&rsquo;s response engine is not editable from this page.
            </p>
          )}
        </fieldset>

        {/* right: settings accordions */}
        <fieldset
          disabled={readOnly}
          className={`${SIDE_PANEL_WIDTH} overflow-y-auto rounded-xl border border-line bg-card`}
        >
          <Accordion icon={LayoutGrid} title="Functions">
            {flowView ? (
              <FunctionsSection
                tools={(flowView.tools ?? []) as Tool[]}
                onChange={(tools) =>
                  dispatchFlow({ type: "patchFlow", patch: { tools: stampToolIds(tools) } })
                }
              />
            ) : llmView ? (
              <FunctionsSection
                tools={llmView.general_tools ?? []}
                onChange={(tools) => setLlmField("general_tools", tools)}
              />
            ) : (
              <p className="text-[13px] text-sub">Not available for this agent&rsquo;s response engine.</p>
            )}
          </Accordion>
          <Accordion icon={Library} title="Knowledge Base">
            {flowView ? (
              <KnowledgeBaseSection
                attachedIds={flowView.knowledge_base_ids ?? []}
                onChange={(ids) =>
                  dispatchFlow({ type: "patchFlow", patch: { knowledge_base_ids: ids } })
                }
                kbConfig={flowView.kb_config ?? null}
                onKbConfig={(v) => dispatchFlow({ type: "patchFlow", patch: { kb_config: v } })}
              />
            ) : llmView ? (
              <KnowledgeBaseSection
                attachedIds={llmView.knowledge_base_ids ?? []}
                onChange={(ids) => setLlmField("knowledge_base_ids", ids)}
              />
            ) : (
              <p className="text-[13px] text-sub">Not available for this agent&rsquo;s response engine.</p>
            )}
          </Accordion>
          {/* Everything below is voice/telephony-only: a chat agent has no
              audio, no call and no version to publish, and the chat-agent API
              rejects these fields. */}
          {!isChat && (
            <>
          <Accordion icon={AudioLines} title="Speech Settings">
            <SpeechSettingsSection
              ambientSound={str(view.ambient_sound, "none")}
              onAmbientSound={(v) => setAgentField("ambient_sound", v)}
              ambientSoundVolume={num(view.ambient_sound_volume, 1)}
              onAmbientSoundVolume={(v) => setAgentField("ambient_sound_volume", v)}
              responsiveness={view.responsiveness}
              onResponsiveness={(v) => setAgentField("responsiveness", v)}
              interruptionSensitivity={view.interruption_sensitivity}
              onInterruptionSensitivity={(v) => setAgentField("interruption_sensitivity", v)}
              reminderTriggerMs={view.reminder_trigger_ms}
              onReminderTriggerMs={(v) => setAgentField("reminder_trigger_ms", v)}
              reminderMaxCount={view.reminder_max_count}
              onReminderMaxCount={(v) => setAgentField("reminder_max_count", v)}
              pronunciation={view.pronunciation_dictionary ?? []}
              onPronunciation={(v) => setAgentField("pronunciation_dictionary", v)}
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
              callScreening={Boolean(view.call_screening_option)}
              onCallScreening={(v) =>
                setAgentField("call_screening_option", v ? { action: { type: "hangup" } } : null)
              }
              ivrHangup={Boolean(view.ivr_option)}
              onIvrHangup={(v) =>
                setAgentField("ivr_option", v ? { action: { type: "hangup" } } : null)
              }
              allowUserDtmf={view.allow_user_dtmf ?? true}
              onAllowUserDtmf={(v) => setAgentField("allow_user_dtmf", v)}
              userDtmfOptions={view.user_dtmf_options ?? null}
              onUserDtmfOptions={(v) => setAgentField("user_dtmf_options", v)}
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
              piiConfig={view.pii_config ?? null}
              onPiiConfig={(v) => setAgentField("pii_config", v)}
              fallbackVoiceIds={view.fallback_voice_ids ?? null}
              onFallbackVoiceIds={(v) => setAgentField("fallback_voice_ids", v)}
              optInSignedUrl={Boolean(view.opt_in_signed_url)}
              onOptInSignedUrl={(v) => setAgentField("opt_in_signed_url", v)}
              voices={voices}
              dynamicVariables={llmView?.default_dynamic_variables}
              onDynamicVariables={
                llm ? (v) => setLlmField("default_dynamic_variables", v) : undefined
              }
            />
          </Accordion>
            </>
          )}
          {isChat && llm && (
            <Accordion icon={Braces} title="Default Dynamic Variables">
              <DynamicVariablesRow
                value={llmView?.default_dynamic_variables ?? null}
                onChange={(v) => setLlmField("default_dynamic_variables", v)}
              />
            </Accordion>
          )}
          <Accordion icon={Webhook} title="Webhook Settings">
            {isChat ? (
              // The chat-agent API takes the URL only — no per-agent timeout,
              // event filter or test send (those ride on call webhooks).
              <Field label="Webhook URL">
                <TextInput
                  value={view.webhook_url ?? ""}
                  placeholder="https://example.com/webhook"
                  onChange={(e) => setAgentField("webhook_url", e.target.value || null)}
                />
              </Field>
            ) : (
              <WebhookSection
                agentId={agent.agent_id}
                url={view.webhook_url ?? ""}
                onUrl={(v) => setAgentField("webhook_url", v || null)}
                timeoutMs={num(view.webhook_timeout_ms, 5000)}
                onTimeoutMs={(v) => setAgentField("webhook_timeout_ms", v)}
                events={view.webhook_events ?? null}
                onEvents={(v) => setAgentField("webhook_events", v)}
              />
            )}
          </Accordion>
          <Accordion icon={Plug} title="MCPs">
            {flowView ? (
              <McpSection
                mcps={(flowView.mcps ?? []) as unknown as McpServer[]}
                onChange={(v) =>
                  dispatchFlow({
                    type: "patchFlow",
                    patch: { mcps: v as unknown as Record<string, unknown>[] | null },
                  })
                }
              />
            ) : llmView ? (
              <McpSection
                mcps={llmView.mcps ?? []}
                onChange={(v) => setLlmField("mcps", v)}
              />
            ) : (
              <p className="text-[13px] text-sub">Not available for this agent&rsquo;s response engine.</p>
            )}
          </Accordion>
        </fieldset>

        {panelOpen && (
          <VersionsPanel
            versions={versions}
            loading={versions === null && versionsError === null}
            error={versionsError}
            selected={agent.version}
            onSelect={(v) => void selectVersion(v)}
            onClose={() => setPanelOpen(false)}
            onRestore={(v) => void handleRestore(v)}
            onDiscard={(v) => void handleDiscard(v)}
            onPublish={(v) => void handlePublish(v === agent.version ? undefined : v)}
            busy={versionBusy || publishing}
          />
        )}
          </>
        )}
      </div>
    </div>
  );
}

/**
 * Drop the fields we just PATCHed, keeping any edit made while the request was
 * in flight — clearing the whole draft would lose those keystrokes.
 */
function omitSent<T extends object>(prev: T, sent: Partial<T>): T {
  const next = { ...prev };
  for (const key of Object.keys(sent) as (keyof T)[]) {
    if (Object.is(next[key], sent[key])) delete next[key];
  }
  return next;
}
