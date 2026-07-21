// Typed client for the Arhiteq backend (Retell-shaped REST).
//
// Requests NEVER silently fall back to fake data. A failed request throws
// ApiError and flips the backend-status store (rendered as a banner by
// components/shell/BackendBanner.tsx). The only way to see canned data is to
// explicitly run with NEXT_PUBLIC_DEMO_MODE=true, which is labelled in the UI.

import { getValidSession } from "./auth";
import { kbFromBytes } from "./utils";
import type {
  Agent,
  AgentFolder,
  Alert,
  AnalyticsData,
  ApiKey,
  Call,
  Contact,
  KnowledgeBase,
  KnowledgeDocument,
  ListCallsResponse,
  PhoneNumber,
  QaCohort,
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
  last_modification_timestamp: number;
  folder_id?: string | null;
  [key: string]: unknown;
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
  chat_status: string;
  message_with_tool_calls: ChatMessage[];
  transcript: string;
  [key: string]: unknown;
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
  last_modification_timestamp: number;
  [key: string]: unknown;
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
  transcript_object?: { role: string; content: string; words?: unknown[] }[];
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

export interface Workspace {
  workspace_id: string;
  name: string;
  webhook_url: string | null;
}

export interface WorkspaceMember {
  email: string;
  name: string | null;
  role: string; // owner | admin | member
  created_at_ms: number;
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

const SENTIMENTS = new Set(["Positive", "Negative", "Neutral", "Unknown"]);

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
    transcript: (c.transcript_object ?? []).map((t) => ({
      role: t.role === "agent" ? "agent" : t.role === "kb_retrieval" ? "kb_retrieval" : "user",
      content: t.content,
      time: "",
    })),
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
}

export const api = {
  // ------------------------------------------------------------ agents
  listAgents: async (): Promise<Agent[]> => {
    const [agents, phones] = await Promise.all([
      request<RawAgent[]>("/list-agents"),
      request<RawPhoneNumber[]>("/list-phone-numbers").catch(() => [] as RawPhoneNumber[]),
    ]);
    return agents
      .filter((a) => a.voice_id !== "chat") // chat agents live on their own page
      .map((a) => uiAgentFromRaw(a, phones));
  },

  /** Agent + its Retell LLM (prompt lives on the LLM, not the agent). */
  getAgentDetail: async (agentId: string): Promise<AgentDetail> => {
    const agent = await request<RawAgent>(`/get-agent/${encodeURIComponent(agentId)}`);
    const llmId = agent.response_engine?.llm_id;
    const llm = llmId
      ? await request<RawLlm>(`/get-retell-llm/${encodeURIComponent(llmId)}`)
      : null;
    return { agent, llm };
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

  // ------------------------------------------------------ Test LLM (text chat)
  createChat: (agentId: string) =>
    request<RawChat>("/create-chat", post({ agent_id: agentId })),

  createChatCompletion: (chatId: string, content: string) =>
    request<{ messages: ChatMessage[]; is_fallback?: boolean }>(
      "/create-chat-completion",
      post({ chat_id: chatId, content }),
    ),

  endChat: (chatId: string) =>
    request<void>(`/end-chat/${encodeURIComponent(chatId)}`, { method: "PATCH" }),

  // --------------------------------------------------- Test Audio (web call)
  createWebCall: (agentId: string) =>
    request<RawWebCall>("/v2/create-web-call", post({ agent_id: agentId })),

  updateLlm: (llmId: string, body: Partial<RawLlm>) =>
    request<RawLlm>(`/update-retell-llm/${encodeURIComponent(llmId)}`, patch(body)),

  createLlm: (body: Partial<RawLlm>) => request<RawLlm>("/create-retell-llm", post(body)),

  createAgent: (body: Record<string, unknown>) => request<RawAgent>("/create-agent", post(body)),

  createConversationFlow: (body: Record<string, unknown>) =>
    request<{ conversation_flow_id: string }>("/create-conversation-flow", post(body)),

  deleteAgent: (agentId: string) =>
    request<void>(`/delete-agent/${encodeURIComponent(agentId)}`, del),

  publishAgent: (agentId: string) =>
    request<RawAgent>(`/publish-agent/${encodeURIComponent(agentId)}`, post({})),

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
  }) => request<{ batch_call_id: string }>("/create-batch-call", post(body)),

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
  getAnalytics: (days = 30) => request<AnalyticsData>(`/analytics/calls?days=${days}`),

  // ---------------------------------------------------------------- QA
  listCohorts: () => request<QaCohort[]>("/list-qa-cohorts"),
  createCohort: (body: {
    name: string;
    agents?: string[];
    sampling_pct?: number;
    weekly_max?: number;
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
  updateWorkspace: (body: { name?: string; webhook_url?: string | null }) =>
    request<Workspace>("/workspace", patch(body)),

  listMembers: () => request<WorkspaceMember[]>("/list-members"),
  listInvites: () => request<WorkspaceInvite[]>("/list-invites"),
  createInvite: (body: { email: string; role?: string }) =>
    request<WorkspaceInvite>("/create-invite", post(body)),
  revokeInvite: (inviteId: string) =>
    request<void>(`/revoke-invite/${encodeURIComponent(inviteId)}`, post({})),
  removeMember: (email: string) => request<void>("/remove-member", post({ email })),
};
