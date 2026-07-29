// Typed client for the Arhiteq backend (Retell-shaped REST).
//
// Requests NEVER silently fall back to fake data. A failed request throws
// ApiError and flips the backend-status store (rendered as a banner by
// components/shell/BackendBanner.tsx). The only way to see canned data is to
// explicitly run with NEXT_PUBLIC_DEMO_MODE=true, which is labelled in the UI.

import { getValidSession } from "./auth";
import { formatDuration, kbFromBytes } from "./utils";
import type {
  Agent,
  AgentFolder,
  Alert,
  AnalyticsData,
  ApiKey,
  Call,
  ChatAnalyticsData,
  Contact,
  ContactFieldDefinition,
  KnowledgeBase,
  KnowledgeDocument,
  ListCallsResponse,
  PhoneNumber,
  QaCohort,
  TranscriptItem,
  Voice,
  WebhookDelivery,
} from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";
// Dashboard auth: the backend accepts `Authorization: Bearer <token>` where
// <token> is either the Google-sign-in session JWT (lib/auth.ts) or a
// workspace API key. The session wins; NEXT_PUBLIC_API_KEY is the dev fallback.
// The API-key fallback is dev-only: never trust a NEXT_PUBLIC_ key in a
// production build (it would ship to every browser).
const API_KEY =
  process.env.NODE_ENV !== "production" ? process.env.NEXT_PUBLIC_API_KEY : undefined;

export const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === "true";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status = 0) {
    super(message);
    this.status = status;
  }
}

// ---------------------------------------------------------- backend status
// Tiny store consumed via useSyncExternalStore so the shell can show a
// truthful "backend unreachable / unauthorized / demo data" banner.

export type BackendStatus = "unknown" | "ok" | "unreachable" | "unauthorized" | "demo";

let backendStatus: BackendStatus = DEMO_MODE ? "demo" : "unknown";
const statusListeners = new Set<() => void>();

function setBackendStatus(next: BackendStatus) {
  if (backendStatus === next) return;
  backendStatus = next;
  statusListeners.forEach((fn) => fn());
}

export function getBackendStatus(): BackendStatus {
  return backendStatus;
}

export function subscribeBackendStatus(onChange: () => void): () => void {
  statusListeners.add(onChange);
  return () => statusListeners.delete(onChange);
}

// ------------------------------------------------------------------ request

function bearerToken(): string | undefined {
  return getValidSession()?.token ?? API_KEY;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (DEMO_MODE) {
    const { demoResponse } = await import("./mock");
    return demoResponse<T>(path, init);
  }
  const token = bearerToken();
  const isForm = init?.body instanceof FormData;
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      // Multipart uploads (e.g. 20MB KB files) can take far longer than a
      // typical JSON round-trip on real uplinks; give them more room.
      signal: AbortSignal.timeout(isForm ? 120_000 : 10_000),
      ...init,
      headers: {
        ...(isForm ? {} : { "Content-Type": "application/json" }),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init?.headers,
      },
    });
  } catch {
    setBackendStatus("unreachable");
    throw new ApiError(`Backend unreachable at ${API_BASE}`, 0);
  }
  if (res.status === 401) {
    setBackendStatus("unauthorized");
    throw new ApiError("Not authorized — sign in or set NEXT_PUBLIC_API_KEY", res.status);
  }
  // 403 means authenticated but forbidden (e.g. a role gate): the backend is
  // fine and the credential works, so don't flip the global banner — surface
  // the backend's reason to the caller instead.
  setBackendStatus("ok");
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      // non-JSON error body; keep the status line
    }
    if (res.status === 403 && detail === `403 ${res.statusText}`) {
      detail = "You don't have permission to do this";
    }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });
const patch = (body: unknown): RequestInit => ({ method: "PATCH", body: JSON.stringify(body) });
const del: RequestInit = { method: "DELETE" };

/** Retell's multipart shape: repeated fields, texts as JSON strings. */
function kbFormData(
  fields: {
    knowledge_base_name?: string;
    knowledge_base_urls?: string[];
    knowledge_base_texts?: { title: string; text: string }[];
  },
  files: File[],
): FormData {
  const fd = new FormData();
  if (fields.knowledge_base_name) fd.append("knowledge_base_name", fields.knowledge_base_name);
  for (const url of fields.knowledge_base_urls ?? []) fd.append("knowledge_base_urls", url);
  for (const t of fields.knowledge_base_texts ?? [])
    fd.append("knowledge_base_texts", JSON.stringify(t));
  for (const f of files) fd.append("knowledge_base_files", f, f.name);
  return fd;
}

// ------------------------------------------------------- backend raw shapes
// The backend speaks Retell's wire format; the UI types in lib/types.ts are
// dashboard-oriented. Raw shapes + adapters live here so every page maps the
// same way.

export interface ResponseEngine {
  type: "retell-llm" | "conversation-flow" | "custom-llm";
  llm_id?: string;
  conversation_flow_id?: string;
  version?: number;
}

export interface PronunciationEntry {
  word: string;
  alphabet: "ipa" | "cmu";
  phoneme: string;
}

export interface PiiConfig {
  mode: "post_call";
  categories: string[];
}

export interface UserDtmfOptions {
  digit_limit?: number | null;
  termination_key?: string | null;
  timeout_ms?: number | null;
}

export interface RawAgent {
  agent_id: string;
  agent_name: string | null;
  response_engine: ResponseEngine;
  voice_id: string;
  language: string;
  version: number;
  is_published: boolean;
  webhook_url: string | null;
  webhook_timeout_ms?: number | null;
  webhook_events?: string[] | null;
  interruption_sensitivity: number;
  responsiveness: number;
  reminder_trigger_ms: number;
  reminder_max_count: number;
  boosted_keywords: string[] | null;
  enable_voicemail_detection: boolean;
  ambient_sound?: string | null;
  ambient_sound_volume?: number;
  pronunciation_dictionary?: PronunciationEntry[] | null;
  pii_config?: PiiConfig | null;
  fallback_voice_ids?: string[] | null;
  allow_user_dtmf?: boolean;
  allow_dtmf_interruption?: boolean;
  user_dtmf_options?: UserDtmfOptions | null;
  opt_in_signed_url?: boolean;
  ivr_option?: { action: { type: string; text?: string } } | null;
  call_screening_option?: { action: { type: string; text?: string } } | null;
  /** IANA zone for the agent's un-suffixed time variables; null = unset. */
  timezone?: string | null;
  last_modification_timestamp: number;
  folder_id?: string | null;
  [key: string]: unknown;
}

/**
 * Sentinel voice_id the backend flags chat agents with (api/chat_agents.py).
 * They are Agent rows served by the /…-chat-agent endpoints, not /…-agent.
 */
export const CHAT_VOICE_ID = "chat";

/** GET /get-chat-agent — the agent shape minus voice/telephony concerns. */
export interface RawChatAgent {
  agent_id: string;
  agent_name: string | null;
  agent_type: "chat-agent";
  response_engine: ResponseEngine;
  version: number;
  is_published: boolean;
  language: string | null;
  webhook_url: string | null;
  last_modification_timestamp: number;
  [key: string]: unknown;
}

/**
 * One entry of GET /get-agent-versions: the agent shape as of that version,
 * plus lineage and publish bookkeeping. Published versions are immutable
 * snapshots; at most one draft exists and it is always the highest version.
 */
export interface RawAgentVersion extends RawAgent {
  /** Version this one was branched from; null for an agent's first version. */
  base_version: number | null;
  version_title: string | null;
  version_description: string | null;
  /** Whether live calls resolve to this entry (publishing an older version
   *  re-points this without minting a new version). */
  is_live: boolean;
  created_timestamp: number;
  published_timestamp: number | null;
}

export interface McpServer {
  name: string;
  url: string;
  headers?: Record<string, string>;
  query_params?: Record<string, string>;
  timeout_ms?: number;
}

export interface ChatMessage {
  message_id: string;
  role: "agent" | "user";
  content: string;
  created_timestamp: number;
}

export interface RawChat {
  chat_id: string;
  agent_id: string;
  agent_version?: number;
  chat_status: string;
  message_with_tool_calls: ChatMessage[];
  transcript: string;
  start_timestamp?: number;
  end_timestamp?: number;
  metadata?: Record<string, unknown>;
  retell_llm_dynamic_variables?: Record<string, string>;
  [key: string]: unknown;
}

export interface ListChatsResponse {
  items: RawChat[];
  has_more: boolean;
  next_pagination_key: string | null;
}

export interface RawWebCall {
  call_id: string;
  access_token: string;
  /** Arhiteq extra: browser-reachable LiveKit signalling URL. */
  livekit_server_url: string;
  agent_id: string;
  call_status: string;
  [key: string]: unknown;
}

export interface RawLlm {
  llm_id: string;
  model: string;
  model_temperature: number;
  general_prompt: string | null;
  begin_message: string | null;
  start_speaker: "agent" | "user";
  general_tools:
    | {
        name: string;
        type?: string;
        description?: string;
        url?: string;
        method?: string;
        timeout_ms?: number;
        headers?: Record<string, string>;
        query_params?: Record<string, string>;
        parameters?: Record<string, unknown>;
        args_at_root?: boolean;
        speak_during_execution?: boolean;
        speak_after_execution?: boolean;
        execution_message_description?: string;
        transfer_destination?: { type?: string; number?: string; [key: string]: unknown };
        number?: string;
        delay_ms?: number;
        cal_api_key?: string;
        event_type_id?: number | string;
        timezone?: string;
        sms_content?: {
          type?: string;
          content?: string;
          prompt?: string;
          template?: string;
          [key: string]: unknown;
        };
        variables?: {
          name?: string;
          type?: string;
          description?: string;
          choices?: string[];
          required?: boolean;
          [key: string]: unknown;
        }[];
        agent_id?: string;
        post_call_analysis_setting?: string;
        [key: string]: unknown;
      }[]
    | null;
  knowledge_base_ids: string[] | null;
  default_dynamic_variables: Record<string, string> | null;
  mcps?: McpServer[] | null;
  last_modification_timestamp: number;
  [key: string]: unknown;
}

// ------------------------------------------------------------ simulation
/** A tool's canned reply during a simulation, so no real integration is hit. */
export interface ToolMock {
  tool_name: string;
  input_match_rule: { type: "any" } | { type: "partial_match"; args: Record<string, unknown> };
  output: string;
}

export interface RawTestCase {
  test_case_definition_id: string;
  type: string;
  name: string;
  user_prompt: string;
  metrics: string[];
  dynamic_variables?: Record<string, string>;
  tool_mocks?: ToolMock[];
  llm_model?: string | null;
  /** Arhiteq extra: `manual` when hand-written, `generated` when self-written. */
  source?: "manual" | "generated";
  creation_timestamp: number;
  user_modified_timestamp: number;
}

export interface PagedTestCases {
  items: RawTestCase[];
  has_more: boolean;
  pagination_key?: string | null;
}

/** The editable half of a test case (everything but ids and timestamps). */
export interface TestCaseDraft {
  name: string;
  user_prompt: string;
  metrics: string[];
  tool_mocks?: ToolMock[];
  dynamic_variables?: Record<string, string>;
  /** Model to simulate the agent on. Null means "the agent's own". */
  llm_model?: string | null;
}

function testCaseBody(draft: TestCaseDraft) {
  return {
    name: draft.name,
    user_prompt: draft.user_prompt,
    metrics: draft.metrics,
    tool_mocks: draft.tool_mocks ?? [],
    // Always sent, including as `{}`: the edit form owns these, so an empty
    // set means "this case sets no variables" and must overwrite, not be
    // skipped. Callers that don't edit them pass the current set straight back.
    dynamic_variables: draft.dynamic_variables ?? {},
    // Null, never omitted: clearing the picker back to "the agent's own model"
    // has to overwrite a previously pinned one.
    llm_model: draft.llm_model ?? null,
  };
}

export type TestRunStatus = "pending" | "in_progress" | "pass" | "fail" | "error";

export interface RawBatchTest {
  test_case_batch_job_id: string;
  status: "in_progress" | "complete";
  pass_count: number;
  fail_count: number;
  error_count: number;
  total_count: number;
  agent_id?: string | null;
  creation_timestamp: number;
}

export interface MetricResult {
  metric: string;
  passed: boolean;
  explanation: string;
}

export interface RawTestRun {
  test_case_job_id: string;
  status: TestRunStatus;
  test_case_batch_job_id: string;
  test_case_definition_id: string;
  test_case_definition_snapshot: Partial<RawTestCase>;
  transcript_snapshot?: { messages?: RawTranscriptItem[] } | null;
  result_explanation?: string | null;
  metric_results?: MetricResult[];
  creation_timestamp: number;
}

/** Item of transcript_object / transcript_with_tool_calls as served by the
 *  API (worker-recorded), and of a simulation run's transcript_snapshot.
 *  time_ms / tool_call_id exist only on calls recorded after the worker
 *  started stamping them. */
export interface RawTranscriptItem {
  role: string;
  content?: string;
  name?: string;
  arguments?: string;
  tool_call_id?: string;
  time_ms?: number;
  words?: unknown[];
}

export interface RawCall {
  call_id: string;
  agent_id: string;
  agent_name?: string | null;
  agent_version?: number;
  call_type?: "phone_call" | "web_call";
  direction?: "inbound" | "outbound";
  from_number?: string;
  to_number?: string;
  start_timestamp?: number;
  end_timestamp?: number;
  duration_ms?: number;
  disconnection_reason?: string;
  call_status: string;
  transcript?: string;
  transcript_object?: RawTranscriptItem[];
  transcript_with_tool_calls?: RawTranscriptItem[];
  recording_url?: string;
  call_analysis?: {
    call_summary?: string;
    user_sentiment?: string;
    call_successful?: boolean | null;
    in_voicemail?: boolean;
  };
  call_cost?: { combined_cost?: number };
  latency?: { e2e?: { p50?: number } };
  retell_llm_dynamic_variables?: Record<string, unknown>;
  collected_dynamic_variables?: Record<string, unknown>;
  detail_logs?: { time_ms: number; level: string; message: string }[];
  [key: string]: unknown;
}

export interface RawPhoneNumber {
  phone_number: string;
  phone_number_pretty?: string | null;
  nickname: string | null;
  phone_number_type: string;
  inbound_agent_id: string | null;
  outbound_agent_id: string | null;
  inbound_webhook_url: string | null;
  fallback_number?: string | null;
  area_code?: number | null;
  last_modification_timestamp: number;
  [key: string]: unknown;
}

export interface RawKnowledgeBase {
  knowledge_base_id: string;
  knowledge_base_name: string;
  status: string;
  knowledge_base_sources: {
    source_id: string;
    type: string;
    title?: string;
    url?: string;
    content?: string;
    filename?: string;
    file_size?: number;
    file_url?: string;
  }[];
  last_refreshed_timestamp?: number;
  [key: string]: unknown;
}

/** Persona picked in the create-workspace modal; descriptive only. */
export type WorkspaceType = "business" | "agency" | "developer" | "other";

export interface WorkspaceSettings {
  billing_email: string | null;
  workspace_type: WorkspaceType | null;
  purchased_concurrency: number;
  reserved_inbound_concurrency: number;
  concurrency_burst_enabled: boolean;
  llm_token_limit: number;
  cps_limits: { telnyx: number; twilio: number; custom_telephony: number };
  llm_failover_enabled: boolean;
  auto_call_retry_enabled: boolean;
  conductor_messages_enabled: boolean;
  contact_field_definitions: ContactFieldDefinition[];
}

export interface Workspace {
  workspace_id: string;
  name: string;
  webhook_url: string | null;
  settings: WorkspaceSettings;
}

export interface SystemComponent {
  key: string;
  name: string;
  status: "operational" | "degraded" | "down" | "not_configured";
  detail: string;
}

export interface WorkspaceMember {
  email: string;
  name: string | null;
  role: string; // owner | admin | member
  created_at_ms: number;
}

/** One entry in the sidebar's workspace switcher. */
export interface WorkspaceSummary {
  workspace_id: string;
  name: string;
  /** The caller's role in *that* workspace. */
  role: string;
  created_at_ms: number;
  is_current: boolean;
}

/** create/switch-workspace additionally return a session scoped to it. */
export interface WorkspaceSession extends WorkspaceSummary {
  token?: string;
  expires_at?: number;
  /** Only for API-key callers, who get no session to switch with. */
  api_key?: string;
}

export interface WorkspaceRole {
  role: string;
  name: string;
  type: string;
  description: string;
}

export interface WorkspaceInvite {
  invite_id: string;
  email: string;
  role: string;
  status: string;
  token: string;
  invited_by: string | null;
  created_at_ms: number;
  expires_at_ms: number;
}

/** The link an invitee opens; login consumes the token via /auth/google. */
export function inviteLink(invite: WorkspaceInvite): string {
  return `${window.location.origin}/login?invite=${encodeURIComponent(invite.token)}`;
}

// ----------------------------------------------------------------- adapters

function titleCase(s: string): string {
  return s.replace(/[-_]+/g, " ").replace(/\b\w/g, (m) => m.toUpperCase()).trim();
}

/** "cartesia-sonic-english" → "Sonic English"; "11labs-Cimo" → "Cimo". */
export function voiceNameFromId(voiceId: string): string {
  const bare = voiceId.replace(
    /^(cartesia|11labs|elevenlabs|openai|play|deepgram|gemini)-/i,
    "",
  );
  return titleCase(bare) || voiceId;
}

export function uiAgentFromRaw(a: RawAgent, phones: RawPhoneNumber[] = []): Agent {
  const phone = phones.find(
    (p) => p.inbound_agent_id === a.agent_id || p.outbound_agent_id === a.agent_id,
  );
  const voiceName = voiceNameFromId(a.voice_id);
  return {
    agent_id: a.agent_id,
    agent_name: a.agent_name ?? "Untitled agent",
    agent_type:
      a.response_engine?.type === "conversation-flow" ? "conversation-flow" : "single-prompt",
    voice_id: a.voice_id,
    voice_name: voiceName,
    voice_avatar: voiceName.charAt(0).toUpperCase(),
    language: a.language,
    phone_number: phone?.phone_number ?? null,
    version: a.version,
    last_modification_timestamp: a.last_modification_timestamp,
    webhook_url: a.webhook_url ?? undefined,
    interruption_sensitivity: a.interruption_sensitivity,
    reminder_trigger_seconds: a.reminder_trigger_ms ? a.reminder_trigger_ms / 1000 : undefined,
    reminder_max_count: a.reminder_max_count,
    boosted_keywords: a.boosted_keywords ?? undefined,
    folder_id: a.folder_id ?? null,
  };
}

/** Chat agents share the agents table; the voice/phone columns render as "-". */
export function uiAgentFromRawChat(a: RawChatAgent): Agent {
  return {
    agent_id: a.agent_id,
    agent_name: a.agent_name ?? "Untitled agent",
    agent_type: "chat",
    voice_id: CHAT_VOICE_ID,
    voice_name: "-",
    language: a.language ?? "en-US",
    phone_number: null,
    version: a.version,
    last_modification_timestamp: a.last_modification_timestamp,
    webhook_url: a.webhook_url ?? undefined,
    folder_id: null, // chat agents aren't foldered (no folder_id on the chat API)
  };
}

/**
 * A chat agent seen through the RawAgent lens the editor works in. The
 * voice-only fields are absent, not defaulted: chat mode hides every section
 * that reads them, and inventing values here would save them back on the
 * first PATCH.
 */
export function rawAgentFromChatAgent(a: RawChatAgent): RawAgent {
  return { ...a, voice_id: CHAT_VOICE_ID } as unknown as RawAgent;
}

const SENTIMENTS = new Set(["Positive", "Negative", "Neutral", "Unknown"]);

// Wire roles → UI roles; anything unrecognized renders as a user turn,
// matching the pre-tool-call coercion.
const UI_TRANSCRIPT_ROLES: Record<string, TranscriptItem["role"]> = {
  agent: "agent",
  kb_retrieval: "kb_retrieval",
  tool_call_invocation: "tool_invocation",
  tool_call_result: "tool_result",
};

/** Wire transcript items → the shape the Transcript component renders.
 *  Shared by call detail and simulation runs, which record the same roles. */
export function uiTranscriptFromRaw(source: RawTranscriptItem[]): TranscriptItem[] {
  return source.map((t) => {
    const time_ms = typeof t.time_ms === "number" ? t.time_ms : undefined;
    return {
      role: UI_TRANSCRIPT_ROLES[t.role] ?? "user",
      name: t.name,
      tool_call_id: t.tool_call_id,
      // Invocations carry their payload in `arguments`; everything else
      // (utterances, results) uses `content`.
      content: t.content ?? t.arguments ?? "",
      time_ms,
      time: time_ms !== undefined ? formatDuration(time_ms) : "",
    };
  });
}

function transcriptFromRaw(c: RawCall): TranscriptItem[] {
  // Prefer the tool-bearing stream; old calls only have transcript_object.
  return uiTranscriptFromRaw(
    c.transcript_with_tool_calls?.length
      ? c.transcript_with_tool_calls
      : (c.transcript_object ?? []),
  );
}

export function uiCallFromRaw(c: RawCall): Call {
  const analysis = c.call_analysis ?? {};
  const sentiment = analysis.user_sentiment ?? "Unknown";
  // Data tab: input vars first, mid-call extracted vars override. Values cross
  // the wire as `unknown`; String() pins them to the Record<string,string> the
  // panel renders (they're already string-coerced server-side).
  const dynamic_variables: Record<string, string> = Object.fromEntries(
    Object.entries({
      ...(c.retell_llm_dynamic_variables ?? {}),
      ...(c.collected_dynamic_variables ?? {}),
    }).map(([k, v]) => [k, String(v)]),
  );
  return {
    call_id: c.call_id,
    agent_id: c.agent_id,
    agent_name: c.agent_name ?? c.agent_id,
    agent_version: c.agent_version ?? 0,
    channel_type: c.call_type ?? "phone_call",
    direction: c.direction ?? "outbound",
    from_number: c.from_number ?? "",
    to_number: c.to_number ?? "",
    // Never-connected calls have no start_timestamp; fall back to the end
    // (finalize) time so Call History shows when the attempt happened.
    start_timestamp: c.start_timestamp ?? c.end_timestamp ?? 0,
    end_timestamp: c.end_timestamp ?? 0,
    duration_ms: c.duration_ms ?? 0,
    cost: c.call_cost?.combined_cost ?? 0,
    disconnection_reason: (c.disconnection_reason ?? "") as Call["disconnection_reason"],
    call_status: (c.call_status ?? "ended") as Call["call_status"],
    user_sentiment: (SENTIMENTS.has(sentiment) ? sentiment : "Unknown") as Call["user_sentiment"],
    call_successful: analysis.call_successful ?? null,
    end_to_end_latency_ms: c.latency?.e2e?.p50,
    call_summary: analysis.call_summary,
    recording_url: c.recording_url,
    transcript: transcriptFromRaw(c),
    dynamic_variables,
    detail_logs: c.detail_logs,
  };
}

export function uiPhoneFromRaw(p: RawPhoneNumber): PhoneNumber {
  return {
    phone_number: p.phone_number,
    nickname: p.nickname ?? undefined,
    provider: p.phone_number_type === "telnyx" ? "Telnyx" : "Custom telephony",
    inbound_agent_id: p.inbound_agent_id,
    outbound_agent_id: p.outbound_agent_id,
    inbound_webhook_enabled: Boolean(p.inbound_webhook_url),
    inbound_webhook_url: p.inbound_webhook_url ?? undefined,
    fallback_number: p.fallback_number ?? null,
    allowed_inbound_countries: ["US"],
    allowed_outbound_countries: ["US"],
  };
}

function extFromFilename(name: string): string {
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
  // Real extensions are short (pdf, docx, html, csv…); a long tail after the
  // last dot is part of the name, not an extension.
  return ext && ext.length <= 4 ? ext : "txt";
}

function kbDocFromSource(s: RawKnowledgeBase["knowledge_base_sources"][number]): KnowledgeDocument {
  return {
    document_id: s.source_id,
    name: s.title ?? s.url ?? s.filename ?? s.source_id,
    type:
      s.type === "url" ? "url" : s.type === "document" ? extFromFilename(s.filename ?? "") : "txt",
    size_kb:
      typeof s.file_size === "number"
        ? kbFromBytes(s.file_size)
        : s.content
          ? kbFromBytes(s.content.length)
          : 0,
    file_url: s.file_url,
  };
}

export function docsFromRawKb(raw: RawKnowledgeBase): KnowledgeDocument[] {
  return (raw.knowledge_base_sources ?? []).map(kbDocFromSource);
}

export function uiKbFromRaw(k: RawKnowledgeBase): KnowledgeBase {
  return {
    knowledge_base_id: k.knowledge_base_id,
    knowledge_base_name: k.knowledge_base_name,
    status: k.status === "complete" ? "ready" : "processing",
    uploaded_by: k.last_refreshed_timestamp
      ? new Date(k.last_refreshed_timestamp).toLocaleDateString()
      : "",
    documents: docsFromRawKb(k),
  };
}

// ---------------------------------------------------------------- api calls

export interface ListCallsFilter {
  agent_id?: string[];
  call_status?: string[];
  user_sentiment?: string[];
  direction?: string[];
  from_number?: string[];
  to_number?: string[];
  start_timestamp?: { lower_threshold?: number; upper_threshold?: number };
}

export interface ListCallsParams {
  filter_criteria?: ListCallsFilter;
  sort_order?: "ascending" | "descending";
  limit?: number;
  pagination_key?: string;
}

export interface AgentDetail {
  agent: RawAgent;
  llm: RawLlm | null;
  /** The agent is a chat agent: it has no voice, versions or telephony, and
   *  edits go to /update-chat-agent instead of /update-agent. */
  is_chat: boolean;
}

export interface CallTimeWindow {
  start: string; // "HH:MM"
  end: string; // "HH:MM"
  days: string[]; // ["mon", ...]
}

export interface BatchCallDraft {
  batch_call_id: string;
  name: string | null;
  from_number: string | null;
  tasks: { to_number: string; retell_llm_dynamic_variables?: Record<string, string> }[];
  trigger_timestamp: number | null;
  reserved_concurrency: number | null;
  call_time_window: CallTimeWindow | null;
  created_at_ms: number;
}

export interface AnalyticsParams {
  days?: number;
  start_ms?: number;
  end_ms?: number;
  agent_ids?: string[];
  group_by?: "agent" | "direction";
}

function analyticsQuery(params: AnalyticsParams): string {
  const q = new URLSearchParams();
  if (params.days) q.set("days", String(params.days));
  if (params.start_ms !== undefined) q.set("start_ms", String(params.start_ms));
  if (params.end_ms !== undefined) q.set("end_ms", String(params.end_ms));
  if (params.group_by) q.set("group_by", params.group_by);
  for (const id of params.agent_ids ?? []) q.append("agent_id", id);
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const api = {
  // ------------------------------------------------------------ agents
  /**
   * Voice agents only — what every caller that puts an agent on a phone
   * number, a call filter or a QA cohort means by "agent". Chat agents have
   * no voice and would break those; the agents list uses listAllAgents.
   */
  listAgents: async (): Promise<Agent[]> => {
    const [agents, phones] = await Promise.all([
      request<RawAgent[]>("/list-agents"),
      request<RawPhoneNumber[]>("/list-phone-numbers").catch(() => [] as RawPhoneNumber[]),
    ]);
    return agents
      .filter((a) => a.voice_id !== CHAT_VOICE_ID) // /list-agents excludes them; belt and braces
      .map((a) => uiAgentFromRaw(a, phones));
  },

  /** Voice + chat agents, for the agents list page (the only page showing both). */
  listAllAgents: async (): Promise<Agent[]> => {
    const [agents, chatAgents] = await Promise.all([
      api.listAgents(),
      request<RawChatAgent[]>("/list-chat-agents"),
    ]);
    return [...agents, ...chatAgents.map(uiAgentFromRawChat)];
  },

  /** One agent. `version` accepts a number, "latest" or "latest_published". */
  getAgent: (agentId: string, version?: number | string) =>
    request<RawAgent>(
      `/get-agent/${encodeURIComponent(agentId)}${version === undefined ? "" : `?version=${version}`}`,
    ),

  /**
   * Agent + its Retell LLM (prompt lives on the LLM, not the agent).
   *
   * Chat agents are 404 on the voice-agent endpoints, so a miss retries on the
   * chat-agent family — the editor takes one id and can't know which it is.
   */
  getAgentDetail: async (agentId: string): Promise<AgentDetail> => {
    let agent: RawAgent;
    let isChat = false;
    try {
      agent = await request<RawAgent>(`/get-agent/${encodeURIComponent(agentId)}`);
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) throw e;
      agent = rawAgentFromChatAgent(
        await request<RawChatAgent>(`/get-chat-agent/${encodeURIComponent(agentId)}`),
      );
      isChat = true;
    }
    const llmId = agent.response_engine?.llm_id;
    const llm = llmId
      ? await request<RawLlm>(`/get-retell-llm/${encodeURIComponent(llmId)}`)
      : null;
    return { agent, llm, is_chat: isChat };
  },

  updateAgent: (agentId: string, body: Partial<RawAgent>) =>
    request<RawAgent>(`/update-agent/${encodeURIComponent(agentId)}`, patch(body)),

  testAgentWebhook: (
    agentId: string,
    body: { webhook_url?: string | null; webhook_timeout_ms?: number | null; event?: string },
  ) =>
    request<{ ok: boolean; status_code: number | null; error: string | null }>(
      `/test-agent-webhook/${encodeURIComponent(agentId)}`,
      post(body),
    ),

  /** Full version history, newest first. */
  getAgentVersions: (agentId: string) =>
    request<RawAgentVersion[]>(`/get-agent-versions/${encodeURIComponent(agentId)}`),

  /** One version with its prompt/tools attached, for read-only viewing. */
  getAgentVersion: (agentId: string, version: number) =>
    request<RawAgentVersion & { response_engine_config: RawLlm | null }>(
      `/get-agent-version/${encodeURIComponent(agentId)}/${version}`,
    ),

  /** Open a draft carrying `baseVersion`'s config — the restore/rollback path. */
  createAgentVersion: (agentId: string, baseVersion: number) =>
    request<RawAgentVersion>(
      `/create-agent-version/${encodeURIComponent(agentId)}`,
      post({ base_version: baseVersion }),
    ),

  /** Publish a draft, or an older version to roll production back to it. */
  publishAgentVersion: (
    agentId: string,
    version: number,
    meta: { version_title?: string | null; version_description?: string | null } = {},
  ) =>
    request<RawAgent>(
      `/publish-agent-version/${encodeURIComponent(agentId)}`,
      post({ version, ...meta }),
    ),

  /** Discard a draft; the editor reverts to the version it branched from. */
  deleteAgentVersion: (agentId: string, version: number) =>
    request<void>(`/delete-agent-version/${encodeURIComponent(agentId)}/${version}`, del),

  // ------------------------------------------------------ Test LLM (text chat)
  createChat: (agentId: string, dynamicVariables?: Record<string, string>) =>
    request<RawChat>(
      "/create-chat",
      post({
        agent_id: agentId,
        ...(dynamicVariables && Object.keys(dynamicVariables).length
          ? { retell_llm_dynamic_variables: dynamicVariables }
          : {}),
      }),
    ),

  createChatCompletion: (chatId: string, content: string) =>
    request<{ messages: ChatMessage[]; is_fallback?: boolean }>(
      "/create-chat-completion",
      post({ chat_id: chatId, content }),
    ),

  endChat: (chatId: string) =>
    request<void>(`/end-chat/${encodeURIComponent(chatId)}`, { method: "PATCH" }),

  // ------------------------------------------------------------ chat history
  listChats: (params: {
    filter_criteria?: { agent_id?: string[]; chat_status?: string[] };
    sort_order?: "ascending" | "descending";
    limit?: number;
    pagination_key?: string;
  } = {}) => request<ListChatsResponse>("/v3/list-chats", post(params)),

  getChat: (chatId: string) => request<RawChat>(`/get-chat/${encodeURIComponent(chatId)}`),

  // --------------------------------------------------- Test Audio (web call)
  /**
   * `agentVersion` dials a specific version. The editor passes the one being
   * edited so a draft can be voice-tested; omitting it resolves the published
   * version, which is what an API caller wants.
   */
  createWebCall: (
    agentId: string,
    dynamicVariables?: Record<string, string>,
    agentVersion?: number,
  ) =>
    request<RawWebCall>(
      "/v2/create-web-call",
      post({
        agent_id: agentId,
        ...(agentVersion === undefined ? {} : { agent_version: agentVersion }),
        ...(dynamicVariables && Object.keys(dynamicVariables).length
          ? { retell_llm_dynamic_variables: dynamicVariables }
          : {}),
      }),
    ),

  updateLlm: (llmId: string, body: Partial<RawLlm>) =>
    request<RawLlm>(`/update-retell-llm/${encodeURIComponent(llmId)}`, patch(body)),

  createLlm: (body: Partial<RawLlm>) => request<RawLlm>("/create-retell-llm", post(body)),

  createAgent: (body: Record<string, unknown>) => request<RawAgent>("/create-agent", post(body)),

  createConversationFlow: (body: Record<string, unknown>) =>
    request<{ conversation_flow_id: string }>("/create-conversation-flow", post(body)),

  deleteAgent: (agentId: string) =>
    request<void>(`/delete-agent/${encodeURIComponent(agentId)}`, del),

  // ------------------------------------------------------------ chat agents
  // Text-only agents. They share the Agent table server-side but have no
  // voice, telephony or version endpoints, so they get their own CRUD.
  listChatAgents: () => request<RawChatAgent[]>("/list-chat-agents"),

  createChatAgent: (body: Record<string, unknown>) =>
    request<RawChatAgent>("/create-chat-agent", post(body)),

  updateChatAgent: (agentId: string, body: Record<string, unknown>) =>
    request<RawChatAgent>(`/update-chat-agent/${encodeURIComponent(agentId)}`, patch(body)),

  deleteChatAgent: (agentId: string) =>
    request<void>(`/delete-chat-agent/${encodeURIComponent(agentId)}`, del),

  publishAgent: (
    agentId: string,
    meta: { version_title?: string | null; version_description?: string | null } = {},
  ) => request<RawAgent>(`/publish-agent/${encodeURIComponent(agentId)}`, post(meta)),

  // ------------------------------------------------------ agent folders
  listAgentFolders: () => request<AgentFolder[]>("/list-agent-folders"),

  createAgentFolder: (folderName: string) =>
    request<AgentFolder>("/create-agent-folder", post({ folder_name: folderName })),

  renameAgentFolder: (folderId: string, folderName: string) =>
    request<AgentFolder>(
      `/update-agent-folder/${encodeURIComponent(folderId)}`,
      patch({ folder_name: folderName }),
    ),

  deleteAgentFolder: (folderId: string) =>
    request<void>(`/delete-agent-folder/${encodeURIComponent(folderId)}`, del),

  /** Move an agent into a folder (or out, with folderId = null). */
  moveAgentToFolder: (agentId: string, folderId: string | null) =>
    request<RawAgent>(`/update-agent/${encodeURIComponent(agentId)}`, patch({ folder_id: folderId })),

  // ------------------------------------------------------------- voices
  listVoices: () => request<Voice[]>("/list-voices"),

  // ------------------------------------------------------------- calls
  // Retell shape: POST /v2/list-calls returns a bare array; the last item's
  // call_id is the pagination key.
  listCalls: async (params: ListCallsParams = {}): Promise<ListCallsResponse> => {
    const raw = await request<RawCall[]>("/v2/list-calls", post(params));
    const limit = params.limit ?? 50;
    return {
      calls: raw.map(uiCallFromRaw),
      pagination_key: raw.length === limit ? raw[raw.length - 1].call_id : undefined,
    };
  },

  getCall: async (callId: string): Promise<Call> =>
    uiCallFromRaw(await request<RawCall>(`/v2/get-call/${encodeURIComponent(callId)}`)),

  rerunCallAnalysis: (callId: string) =>
    request<RawCall>(`/rerun-call-analysis/${encodeURIComponent(callId)}`, { method: "PUT" }),

  createPhoneCall: (body: { from_number: string; to_number: string; override_agent_id?: string }) =>
    request<RawCall>("/v2/create-phone-call", post(body)),

  createBatchCall: (body: {
    from_number: string;
    name?: string;
    tasks: { to_number: string; retell_llm_dynamic_variables?: Record<string, string> }[];
    trigger_timestamp?: number;
    reserved_concurrency?: number;
    call_time_window?: CallTimeWindow;
  }) => request<{ batch_call_id: string }>("/create-batch-call", post(body)),

  saveBatchCallDraft: (body: Partial<BatchCallDraft>) =>
    request<BatchCallDraft>("/save-batch-call-draft", post(body)),

  listBatchCallDrafts: () => request<BatchCallDraft[]>("/list-batch-call-drafts"),

  deleteBatchCallDraft: (id: string) =>
    request<void>(`/delete-batch-call-draft/${encodeURIComponent(id)}`, del),

  // ----------------------------------------------------- phone numbers
  listPhoneNumbers: async (): Promise<PhoneNumber[]> =>
    (await request<RawPhoneNumber[]>("/list-phone-numbers")).map(uiPhoneFromRaw),

  createPhoneNumber: (body: {
    phone_number: string;
    nickname?: string;
    inbound_agent_id?: string;
    outbound_agent_id?: string;
  }) => request<RawPhoneNumber>("/create-phone-number", post(body)),

  updatePhoneNumber: (num: string, body: Record<string, unknown>) =>
    request<RawPhoneNumber>(`/update-phone-number/${encodeURIComponent(num)}`, patch(body)),

  deletePhoneNumber: (num: string) =>
    request<void>(`/delete-phone-number/${encodeURIComponent(num)}`, del),

  // --------------------------------------------------- knowledge bases
  listKnowledgeBases: async (): Promise<KnowledgeBase[]> =>
    (await request<RawKnowledgeBase[]>("/list-knowledge-bases")).map(uiKbFromRaw),

  createKnowledgeBase: (
    body: {
      knowledge_base_name: string;
      knowledge_base_texts?: { title: string; text: string }[];
      knowledge_base_urls?: string[];
    },
    files: File[] = [],
  ) =>
    files.length
      ? request<RawKnowledgeBase>("/create-knowledge-base", {
          method: "POST",
          body: kbFormData(body, files),
        })
      : request<RawKnowledgeBase>("/create-knowledge-base", post(body)),

  deleteKnowledgeBase: (id: string) =>
    request<void>(`/delete-knowledge-base/${encodeURIComponent(id)}`, del),

  addKnowledgeBaseSources: (
    id: string,
    body: {
      knowledge_base_texts?: { title: string; text: string }[];
      knowledge_base_urls?: string[];
    },
    files: File[] = [],
  ) =>
    files.length
      ? request<RawKnowledgeBase>(`/add-knowledge-base-sources/${encodeURIComponent(id)}`, {
          method: "POST",
          body: kbFormData(body, files),
        })
      : request<RawKnowledgeBase>(`/add-knowledge-base-sources/${encodeURIComponent(id)}`, post(body)),

  downloadKnowledgeBaseFile: async (id: string, sourceId: string): Promise<Blob> => {
    if (DEMO_MODE) throw new ApiError("Downloads are not available in demo mode", 0);
    const token = bearerToken();
    let res: Response;
    try {
      res = await fetch(
        `${API_BASE}/get-knowledge-base-file/${encodeURIComponent(id)}/source/${encodeURIComponent(sourceId)}`,
        {
          cache: "no-store",
          signal: AbortSignal.timeout(30_000),
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        },
      );
    } catch {
      setBackendStatus("unreachable");
      throw new ApiError(`Backend unreachable at ${API_BASE}`, 0);
    }
    if (res.status === 401) {
      setBackendStatus("unauthorized");
      throw new ApiError("Not authorized — sign in or set NEXT_PUBLIC_API_KEY", res.status);
    }
    setBackendStatus("ok");
    if (!res.ok) throw new ApiError(`Download failed (${res.status})`, res.status);
    return res.blob();
  },

  deleteKnowledgeBaseSource: (id: string, sourceId: string) =>
    request<RawKnowledgeBase>(
      `/delete-knowledge-base-source/${encodeURIComponent(id)}/source/${encodeURIComponent(sourceId)}`,
      del,
    ),

  // ---------------------------------------------------------- contacts
  listContacts: () => request<Contact[]>("/list-contacts"),
  createContact: (body: Partial<Contact> & { phone_number: string }) =>
    request<Contact>("/create-contact", post(body)),
  updateContact: (id: string, body: Partial<Contact>) =>
    request<Contact>(`/update-contact/${encodeURIComponent(id)}`, patch(body)),
  deleteContact: (id: string) =>
    request<void>(`/delete-contact/${encodeURIComponent(id)}`, del),

  // --------------------------------------------------------- analytics
  getAnalytics: (params: AnalyticsParams = {}) =>
    request<AnalyticsData>(`/analytics/calls${analyticsQuery(params)}`),

  getChatAnalytics: (params: AnalyticsParams = {}) =>
    request<ChatAnalyticsData>(`/analytics/chats${analyticsQuery(params)}`),

  getCallInsights: (body: { days?: number; agent_id?: string[]; limit?: number }) =>
    request<{ insights: string; calls_analyzed: number; window_days: number }>(
      "/analytics/call-insights",
      post(body),
    ),

  // ------------------------------------------------------- concurrency
  getConcurrency: () =>
    request<{
      current_concurrency: number;
      concurrency_limit: number;
      base_concurrency: number;
      purchased_concurrency: number;
      concurrency_purchase_limit: number;
      remaining_purchase_limit: number;
      reserved_inbound_concurrency: number;
      concurrency_burst_enabled: boolean;
      concurrency_burst_limit: number;
    }>("/get-concurrency"),

  // ---------------------------------------------------------------- QA
  listCohorts: () => request<QaCohort[]>("/list-qa-cohorts"),
  createCohort: (body: {
    name: string;
    agents?: string[];
    sampling_pct?: number;
    weekly_max?: number;
    min_duration_s?: number | null;
    success_criteria?: string | null;
    scoring_metric?: "call_successful" | "transfer";
  }) => request<QaCohort>("/create-qa-cohort", post(body)),
  deleteCohort: (id: string) =>
    request<void>(`/delete-qa-cohort/${encodeURIComponent(id)}`, del),

  // ---------------------------------------------------------- alerting
  listAlerts: () => request<Alert[]>("/list-alerts"),
  createAlert: (body: Partial<Alert> & { name: string; metric: string }) =>
    request<Alert>("/create-alert", post(body)),
  updateAlert: (id: string, body: Partial<Alert>) =>
    request<Alert>(`/update-alert/${encodeURIComponent(id)}`, patch(body)),
  deleteAlert: (id: string) =>
    request<void>(`/delete-alert/${encodeURIComponent(id)}`, del),

  // ---------------------------------------------------------- settings
  listApiKeys: () => request<ApiKey[]>("/list-api-keys"),
  createApiKey: (name: string) => request<ApiKey>("/create-api-key", post({ name })),
  revokeApiKey: (keyId: string) =>
    request<ApiKey>(`/revoke-api-key/${encodeURIComponent(keyId)}`, post({})),

  listWebhookDeliveries: () => request<WebhookDelivery[]>("/list-webhook-deliveries"),

  getWorkspace: () => request<Workspace>("/workspace"),
  updateWorkspace: (body: {
    name?: string;
    webhook_url?: string | null;
    settings?: Partial<WorkspaceSettings>;
  }) => request<Workspace>("/workspace", patch(body)),

  getSystemStatus: () =>
    request<{ checked_at_ms: number; components: SystemComponent[] }>("/system-status"),

  testWorkspaceWebhook: (body: { webhook_url?: string; event?: string }) =>
    request<{ ok: boolean; status_code: number | null; error: string | null }>(
      "/test-workspace-webhook",
      post(body),
    ),

  deleteWorkspace: () => request<void>("/workspace", del),

  // -------------------------------------------------------- simulation
  listTestCases: (llmId: string) =>
    request<PagedTestCases>(
      `/v2/list-test-case-definitions?type=retell-llm&llm_id=${encodeURIComponent(llmId)}&limit=200`,
    ),

  createTestCase: (body: TestCaseDraft & { llm_id: string }) =>
    request<RawTestCase>(
      "/create-test-case-definition",
      post({ ...testCaseBody(body), response_engine: { type: "retell-llm", llm_id: body.llm_id } }),
    ),

  updateTestCase: (id: string, body: TestCaseDraft) =>
    request<RawTestCase>(`/update-test-case-definition/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(testCaseBody(body)),
    }),

  deleteTestCase: (id: string) =>
    request<void>(`/delete-test-case-definition/${encodeURIComponent(id)}`, del),

  /** Draft cases from the agent's own prompt + tools ("the agent tests itself").
   *  The backend writes every case in one synchronous model call, so this runs
   *  far past the default 10s budget — and it commits the rows regardless, so
   *  aborting early would strand a saved suite behind a "backend unreachable"
   *  banner and tempt a second click that duplicates it. */
  generateTestCases: (body: { agent_id: string; count?: number }) =>
    request<{ items: RawTestCase[]; saved: boolean }>("/generate-test-case-definitions", {
      ...post({ ...body, save: true }),
      signal: AbortSignal.timeout(180_000),
    }),

  createBatchTest: (body: { llm_id: string; agent_id?: string; test_case_definition_ids: string[] }) =>
    request<RawBatchTest>(
      "/create-batch-test",
      post({
        test_case_definition_ids: body.test_case_definition_ids,
        response_engine: { type: "retell-llm", llm_id: body.llm_id },
        agent_id: body.agent_id,
      }),
    ),

  getBatchTest: (id: string) =>
    request<RawBatchTest>(`/get-batch-test/${encodeURIComponent(id)}`),

  /** Batches for this agent only — several agents can share one LLM. */
  listBatchTests: (llmId: string, agentId: string) =>
    request<{ items: RawBatchTest[]; has_more: boolean }>(
      `/v2/list-batch-tests?type=retell-llm&llm_id=${encodeURIComponent(llmId)}` +
        `&agent_id=${encodeURIComponent(agentId)}&limit=20`,
    ),

  listTestRuns: (batchId: string) =>
    request<{ items: RawTestRun[]; has_more: boolean }>(
      `/v2/list-test-runs/${encodeURIComponent(batchId)}?limit=200`,
    ),

  listMembers: () => request<WorkspaceMember[]>("/list-members"),
  listInvites: () => request<WorkspaceInvite[]>("/list-invites"),
  createInvite: (body: { email: string; role?: string }) =>
    request<WorkspaceInvite>("/create-invite", post(body)),
  revokeInvite: (inviteId: string) =>
    request<void>(`/revoke-invite/${encodeURIComponent(inviteId)}`, post({})),
  removeMember: (email: string) => request<void>("/remove-member", post({ email })),
  updateMemberRole: (email: string, role: string) =>
    request<WorkspaceMember>("/update-member-role", post({ email, role })),
  listRoles: () => request<WorkspaceRole[]>("/list-roles"),

  // ------------------------------------------------------- workspaces
  // Switching is "re-issue the session": the active workspace is a claim in
  // the JWT, so these return a token the caller stores before reloading.
  listWorkspaces: () => request<WorkspaceSummary[]>("/list-workspaces"),
  createWorkspace: (body: { name: string; workspace_type?: WorkspaceType | null }) =>
    request<WorkspaceSession>("/create-workspace", post(body)),
  switchWorkspace: (workspaceId: string) =>
    request<WorkspaceSession>("/switch-workspace", post({ workspace_id: workspaceId })),
};
