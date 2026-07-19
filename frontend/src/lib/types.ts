// Retell-shaped resource types used across the dashboard.

export type AgentType = "single-prompt" | "conversation-flow";

export interface Agent {
  agent_id: string;
  agent_name: string;
  agent_type: AgentType;
  voice_id: string;
  voice_name: string;
  voice_avatar?: string; // emoji/initial used for the round avatar
  language: string;
  phone_number?: string | null;
  version: number;
  last_modification_timestamp: number; // ms epoch
  edited_by?: string;
  // editor fields
  general_prompt?: string;
  begin_message_mode?: "ai_first" | "user_first" | "silent";
  begin_message?: string;
  pause_before_speaking?: number; // seconds
  cost_per_min?: number;
  latency_ms?: [number, number];
  token_range?: [number, number];
  functions?: AgentFunction[];
  knowledge_base_ids?: string[];
  interruption_sensitivity?: number;
  response_eagerness?: number;
  reminder_trigger_seconds?: number;
  reminder_max_count?: number;
  webhook_url?: string;
  webhook_timeout?: number;
  boosted_keywords?: string[];
  folder_id?: string | null;
}

/** Dashboard-only agent grouping (Arhiteq extra, mirrors Retell's sidebar). */
export interface AgentFolder {
  folder_id: string;
  folder_name: string;
  last_modification_timestamp: number;
}

export interface AgentFunction {
  name: string;
  type: "builtin" | "custom";
}

/** /list-voices catalog entry (Retell voice shape + Arhiteq extras). */
export interface Voice {
  voice_id: string;
  voice_name: string;
  provider: string;
  gender?: string;
  accent?: string;
  age?: string;
  // Free-form voice characteristic shown as the trait when accent/age are
  // absent (e.g. Gemini Live voices carry Google's one-word descriptor).
  description?: string;
  preview_audio_url?: string | null;
  recommended?: boolean;
}

export type EndReason =
  | "agent hangup"
  | "user hangup"
  | "voicemail reached"
  | "dial no answer";

export type SessionStatus = "ended" | "not_connected" | "ongoing" | "error";
export type Sentiment = "Neutral" | "Positive" | "Negative" | "Unknown";

export interface TranscriptTurn {
  role: "agent" | "user" | "kb_retrieval";
  content: string;
  time: string; // "0:03"
}

export interface Call {
  call_id: string;
  agent_id: string;
  agent_name: string;
  agent_version: number;
  channel_type: "phone_call" | "web_call";
  direction: "inbound" | "outbound";
  from_number: string;
  to_number: string;
  start_timestamp: number;
  end_timestamp: number;
  duration_ms: number;
  cost: number;
  disconnection_reason: EndReason;
  call_status: SessionStatus;
  user_sentiment: Sentiment;
  call_successful: boolean | null;
  end_to_end_latency_ms?: number;
  llm_token_usage?: number;
  call_summary?: string;
  recording_url?: string;
  transcript?: TranscriptTurn[];
  contact_id?: string;
  // Data tab: input vars merged with vars extracted mid-call.
  dynamic_variables?: Record<string, string>;
  // Detail Logs tab: synthesized timestamped lifecycle log lines.
  detail_logs?: DetailLog[];
}

export interface DetailLog {
  time_ms: number;
  level: string; // "info" | "error"
  message: string;
}

export interface ListCallsResponse {
  calls: Call[];
  pagination_key?: string;
}

export interface KnowledgeDocument {
  document_id: string;
  name: string;
  /** Badge label: file extension for documents ("pdf", "md", …), "url", or "txt". */
  type: string;
  size_kb: number;
  /** Present on uploaded files; download goes through api.downloadKnowledgeBaseFile. */
  file_url?: string;
}

export interface KnowledgeBase {
  knowledge_base_id: string;
  knowledge_base_name: string;
  uploaded_by: string; // date string
  status: "ready" | "processing";
  documents: KnowledgeDocument[];
}

export interface PhoneNumber {
  phone_number: string; // E.164
  nickname?: string;
  provider: "Custom telephony" | "Twilio" | "Telnyx";
  inbound_agent_id?: string | null;
  inbound_agent_version_tag?: string;
  outbound_agent_id?: string | null;
  outbound_agent_version_tag?: string;
  inbound_webhook_enabled?: boolean;
  inbound_webhook_url?: string;
  allowed_inbound_countries: string[];
  allowed_outbound_countries: string[];
  fallback_number?: string | null;
}

export interface Contact {
  contact_id: string;
  phone_number: string;
  first_name: string;
  last_name: string;
  timezone?: string | null;
  related_conversations: number;
  latest_conversation: number; // ms epoch
  do_not_call: boolean;
  external_id?: string;
}

export interface StatPoint {
  date: string;
  value: number;
}

export interface AnalyticsData {
  call_counts: number;
  avg_duration_s: number;
  avg_latency_ms: number;
  call_counts_series: StatPoint[];
  concurrency_series: StatPoint[];
  call_successful: { name: string; value: number }[];
  disconnection_reason: { name: string; value: number }[];
  user_sentiment: { name: string; value: number }[];
  phone_direction: { name: string; value: number }[];
}

export interface QaCohort {
  cohort_id: string;
  name: string;
  agents: string[];
  sampling_pct: number;
  weekly_max: number;
  transfer_success_rate: number;
  transfer_wait_time_s: number;
}

export interface Alert {
  alert_id: string;
  name: string;
  check_every_min: number;
  lookback_min: number;
  metric: string;
  condition: string;
  threshold: number;
  notify_emails: string[];
  webhook_url?: string;
  enabled: boolean;
}

export interface ApiKey {
  key_id: string;
  name: string;
  prefix: string; // "key_53a…"
  created_at: number;
  last_used_at?: number;
  revoked?: boolean;
  /** full secret — only present right after creation */
  secret?: string;
}

export interface WebhookDelivery {
  delivery_id: string;
  event: string;
  status: number;
  timestamp: number;
  duration_ms: number;
}
