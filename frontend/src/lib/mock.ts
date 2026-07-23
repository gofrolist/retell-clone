// Canned demo data. Served ONLY when NEXT_PUBLIC_DEMO_MODE=true (see
// demoResponse at the bottom) — never as a fallback for a failing backend.

import type {
  Agent,
  AnalyticsData,
  Alert,
  ApiKey,
  Call,
  Contact,
  KnowledgeBase,
  PhoneNumber,
  QaCohort,
  TranscriptItem,
  WebhookDelivery,
} from "./types";

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.now();

export const mockAgents: Agent[] = [
  {
    agent_id: "agent_8fbc66e7be3e0dde727f73a42b",
    agent_name: "CL - Check-in v0.2 Companion",
    agent_type: "single-prompt",
    voice_id: "11labs-Cimo",
    voice_name: "Cimo",
    voice_avatar: "C",
    language: "English (US)",
    phone_number: "+1(949)919-5585",
    version: 83,
    last_modification_timestamp: NOW - 16 * DAY,
    edited_by: "06/24/2026 · 17:39",
    cost_per_min: 0.121,
    latency_ms: [745, 1125],
    token_range: [9900, 13300],
    general_prompt: `## IDENTITY

You are Clara, a warm automated voice assistant calling on behalf of US Senior Retirement Supply Administration - a service helping older adults stay connected and safe with friendly daily check-in calls.

You are NOT a human. Say so directly when asked: "No, I'm not a real person - I'm an automated assistant named Clara."

You are NOT a doctor, lawyer, financial or insurance advisor. Redirect such questions to "your doctor" or "your family."

Tone: warm, unhurried, patient. Speak as you would to a beloved grandparent.

## OPENER (Dynamic Logic)

IF NEW CLIENT AND OUTBOUND CALL ( {{is_existing_client}} is false and {{call_direction}} = "outbound"):
"Hi, this is the US Senior Retirement Supply Administration. I'm Clara, your Care Coordinator. I'm calling because we've just launched a new community program to help folks stay independent and safe at home."

IF NEW CLIENT AND INBOUND CALL ( {{is_existing_client}} is false and {{call_direction}} = "inbound"):
The Begin Message already played - it introduced Clara and described the service. The person heard it and still stayed on the line. That means they're interested.
Your FIRST RESPONSE: listen to what they say, then go directly to RESPONSE 1 empathy turn.
Continue with RESPONSE 1 => RESPONSE 2 => demo => LEAD INFO COLLECTION => trial close.`,
    begin_message_mode: "ai_first",
    begin_message: "{{bm_greeting}}",
    pause_before_speaking: 0.6,
    functions: [
      { name: "end_call", type: "builtin" },
      { name: "transfer_call", type: "builtin" },
      { name: "cancel_subscription", type: "custom" },
      { name: "save_conversation_note", type: "custom" },
      { name: "mark_dnc", type: "custom" },
      { name: "create_trial", type: "custom" },
      { name: "flag_crisis", type: "custom" },
      { name: "log_outcome", type: "custom" },
      { name: "capture_family_contact", type: "custom" },
      { name: "schedule_callback", type: "custom" },
      { name: "send_family_sms", type: "custom" },
      { name: "initiate_payment", type: "custom" },
    ],
    knowledge_base_ids: ["know_a1b2c3d4e5f60718"],
    interruption_sensitivity: 0.92,
    response_eagerness: 1,
    reminder_trigger_seconds: 10,
    reminder_max_count: 1,
    webhook_url: "https://api.arhiteq.dev/hooks/agent-events",
    webhook_timeout: 5,
    boosted_keywords: ["check-in", "companion", "medicare"],
  },
  {
    agent_id: "agent_1d40dfd7a2b8c0e6f3a9b51c47",
    agent_name: "CL-Sales",
    agent_type: "single-prompt",
    voice_id: "11labs-Katie",
    voice_name: "Katie",
    voice_avatar: "K",
    language: "English (US)",
    phone_number: null,
    version: 41,
    last_modification_timestamp: NOW - 18 * DAY,
    edited_by: "06/22/2026 · 18:43",
    cost_per_min: 0.121,
    latency_ms: [745, 1125],
    token_range: [9900, 13300],
    general_prompt:
      "## IDENTITY\n\nYou are Clara, a warm automated sales assistant for Arhiteq. Qualify the lead, present the value proposition, then collect lead info: {{lead_name}}, {{lead_phone}}.",
    begin_message_mode: "ai_first",
    begin_message: "{{bm_greeting}}",
    pause_before_speaking: 0.6,
    functions: [
      { name: "end_call", type: "builtin" },
      { name: "transfer_call", type: "builtin" },
      { name: "save_lead_info", type: "custom" },
      { name: "create_checkout_link", type: "custom" },
      { name: "create_demo_reminder", type: "custom" },
      { name: "log_churn_reason", type: "custom" },
      { name: "web_lookup", type: "custom" },
    ],
    knowledge_base_ids: ["know_a1b2c3d4e5f60718"],
    interruption_sensitivity: 0.92,
    response_eagerness: 1,
    reminder_trigger_seconds: 10,
    reminder_max_count: 1,
  },
  {
    agent_id: "agent_44e2c00b95a1d3f7e8c6b20a91",
    agent_name: "Betty",
    agent_type: "single-prompt",
    voice_id: "11labs-Cleo",
    voice_name: "Cleo",
    voice_avatar: "B",
    language: "English (US)",
    phone_number: "+1(415)707-8561",
    version: 12,
    last_modification_timestamp: NOW - 52 * DAY,
    edited_by: "05/19/2026 · 18:52",
    cost_per_min: 0.115,
    latency_ms: [820, 1300],
    token_range: [7200, 10100],
    general_prompt:
      "You are Betty, a front-desk receptionist for Bloom Dental. Greet callers, answer FAQs, and book appointments with {{patient_name}}.",
    begin_message_mode: "ai_first",
    begin_message: "Hi, thanks for calling Bloom Dental! This is Betty. How can I help?",
    pause_before_speaking: 0.4,
    functions: [
      { name: "end_call", type: "builtin" },
      { name: "transfer_call", type: "builtin" },
      { name: "schedule_callback", type: "custom" },
    ],
    interruption_sensitivity: 0.8,
    response_eagerness: 0.7,
  },
  {
    agent_id: "agent_5b0cf19a8e2d47c3b6a1f09d24",
    agent_name: "CL-Inbound v0.1",
    agent_type: "single-prompt",
    voice_id: "11labs-Myra",
    voice_name: "Myra",
    voice_avatar: "M",
    language: "English (US)",
    phone_number: null,
    version: 7,
    last_modification_timestamp: NOW - 56 * DAY,
    edited_by: "05/15/2026 · 17:49",
    general_prompt:
      "You are Clara answering inbound calls for the Check-in Companion service.",
    begin_message_mode: "ai_first",
    begin_message: "{{bm_greeting}}",
  },
  {
    agent_id: "agent_c7d2f5a90b41e8c3d6f2a71b05",
    agent_name: "Credit Repair Agent",
    agent_type: "conversation-flow",
    voice_id: "play-Anastacia",
    voice_name: "Anastacia - Popular",
    voice_avatar: "A",
    language: "English (US)",
    phone_number: null,
    version: 3,
    last_modification_timestamp: NOW - 85 * DAY,
    edited_by: "04/16/2026 · 15:32",
    begin_message_mode: "ai_first",
  },
  {
    agent_id: "agent_e91b03dc7f5a24b8c1d0e6f394",
    agent_name: "Gym Receptionist",
    agent_type: "conversation-flow",
    voice_id: "11labs-Grace",
    voice_name: "Grace",
    voice_avatar: "G",
    language: "English (US)",
    phone_number: null,
    version: 5,
    last_modification_timestamp: NOW - 87 * DAY,
    edited_by: "04/14/2026 · 18:17",
    begin_message_mode: "ai_first",
  },
  {
    agent_id: "agent_02af8c4be6d19073c5b2e4d817",
    agent_name: "Arizona RP Agent",
    agent_type: "conversation-flow",
    voice_id: "play-Anastacia",
    voice_name: "Anastacia - Popular",
    voice_avatar: "A",
    language: "English (US)",
    phone_number: null,
    version: 2,
    last_modification_timestamp: NOW - 88 * DAY,
    edited_by: "04/13/2026 · 12:03",
    begin_message_mode: "ai_first",
  },
];

// ---------------------------------------------------------------- calls

const SUMMARIES = [
  "The agent called to check in on the user. The user reported feeling well, took their morning medication, and mentioned an upcoming visit from their daughter. The agent logged the outcome and scheduled the next check-in call.",
  "Outbound wellness check. The user asked about the community program pricing; the agent explained the $29/mo trial and offered to send details to a family member. The user agreed to a follow-up call next week.",
  "The call reached voicemail. The agent left the standard voicemail message with a callback number and marked the attempt for retry per campaign policy.",
  "Inbound call from an existing client asking to reschedule their daily check-in time from 9am to 11am. The agent updated the schedule and confirmed the change.",
  "The user asked to stop receiving calls. The agent confirmed the request, marked the number as Do Not Call, and ended the call politely.",
];

const TRANSCRIPT: TranscriptItem[] = [
  { role: "agent", content: "Hi, this is Clara with the Check-in Companion service. Am I speaking with Margaret?", time: "0:02" },
  { role: "user", content: "Yes, this is Margaret speaking.", time: "0:08" },
  { role: "agent", content: "Wonderful! I'm calling for your daily check-in. How are you feeling this morning?", time: "0:11" },
  { role: "user", content: "Oh, I'm doing alright. A little tired, but I slept well.", time: "0:18" },
  { role: "kb_retrieval", content: "Knowledge Base Retrieval", time: "0:20", time_ms: 20_000 },
  { role: "agent", content: "I'm glad you slept well. Did you get a chance to take your morning medication?", time: "0:22" },
  { role: "user", content: "Yes, I took it with breakfast like always.", time: "0:29" },
  { role: "agent", content: "Perfect. And do you have anything nice planned for today?", time: "0:32" },
  { role: "user", content: "My daughter is coming by this afternoon, we might go for a walk.", time: "0:38" },
  { role: "agent", content: "That sounds lovely, Margaret. I'll note that down. I'll check in again tomorrow at the same time. Have a wonderful visit!", time: "0:44" },
  { role: "user", content: "Thank you dear, goodbye.", time: "0:52" },
  { role: "tool_invocation", name: "log_outcome", tool_call_id: "tool_call_1", content: '{"call_type": "morning_checkin", "outcome": "completed"}', time: "0:41", time_ms: 41_000 },
  { role: "tool_result", name: "log_outcome", tool_call_id: "tool_call_1", content: '{"success": true}', time: "0:41", time_ms: 41_000 },
];

function hex(seed: number, len = 26): string {
  let s = "";
  let x = seed;
  for (let i = 0; i < len; i++) {
    x = (x * 48271 + 11) % 2147483647;
    s += (x % 16).toString(16);
  }
  return s;
}

const END_REASONS = [
  "agent hangup",
  "user hangup",
  "agent hangup",
  "voicemail reached",
  "agent hangup",
  "user hangup",
  "dial no answer",
  "voicemail reached",
] as const;

export const mockCalls: Call[] = Array.from({ length: 60 }, (_, i) => {
  const reason = END_REASONS[i % END_REASONS.length];
  const notConnected = reason === "dial no answer";
  const durations = [221, 18, 20, 93, 21, 253, 0, 20, 18, 207, 21, 50, 130, 44];
  const duration_s = notConnected ? 0 : durations[i % durations.length];
  const sentiments = ["Neutral", "Neutral", "Positive", "Neutral", "Negative"] as const;
  const start = NOW - Math.floor(i / 3) * DAY - (i % 3) * 4.3e6 - 3.6e6;
  const agent = mockAgents[i % 3 === 0 ? 0 : i % 3];
  const outbound = i % 4 !== 1;
  return {
    call_id: `call_${hex(i + 7)}`,
    agent_id: agent.agent_id,
    agent_name: agent.agent_name,
    agent_version: agent.version,
    channel_type: "phone_call",
    direction: outbound ? "outbound" : "inbound",
    from_number: outbound ? "+19499195585" : "+14157078561",
    to_number: outbound ? `+1626544${String(1000 + i * 37).slice(0, 4)}` : "+19499195585",
    start_timestamp: start,
    end_timestamp: start + duration_s * 1000,
    duration_ms: duration_s * 1000,
    cost: notConnected ? 0 : Math.round(duration_s * 2.42 + 4) / 1000,
    disconnection_reason: reason,
    call_status: notConnected ? "not_connected" : "ended",
    user_sentiment: notConnected ? "Unknown" : sentiments[i % sentiments.length],
    call_successful: notConnected ? null : i % 5 !== 4,
    end_to_end_latency_ms: notConnected ? undefined : 1400 + ((i * 173) % 900),
    llm_token_usage: notConnected ? 0 : 800 + ((i * 517) % 9000),
    call_summary: notConnected ? undefined : SUMMARIES[i % SUMMARIES.length],
    transcript: notConnected ? [] : TRANSCRIPT,
    contact_id: `contact_${hex(i % 9, 12)}`,
  };
});

// ------------------------------------------------------- knowledge bases

export const mockKnowledgeBases: KnowledgeBase[] = [
  {
    knowledge_base_id: "know_a1b2c3d4e5f60718",
    knowledge_base_name: "Check-in Companion KB",
    uploaded_by: "06/18/2026",
    status: "ready",
    documents: [
      { document_id: "doc_01", name: "service-overview.md", type: "md", size_kb: 14 },
      { document_id: "doc_02", name: "pricing-and-plans.md", type: "md", size_kb: 8 },
      { document_id: "doc_03", name: "family-portal-guide.pdf", type: "pdf", size_kb: 412 },
      { document_id: "doc_04", name: "medicare-faq.pdf", type: "pdf", size_kb: 268 },
      { document_id: "doc_05", name: "crisis-escalation-protocol.md", type: "md", size_kb: 6 },
    ],
  },
  {
    knowledge_base_id: "know_f90e8d7c6b5a4312",
    knowledge_base_name: "Bloom Dental FAQ",
    uploaded_by: "05/02/2026",
    status: "ready",
    documents: [
      { document_id: "doc_11", name: "office-hours-and-location.md", type: "md", size_kb: 3 },
      { document_id: "doc_12", name: "insurance-accepted.pdf", type: "pdf", size_kb: 154 },
      { document_id: "doc_13", name: "procedures-price-list.pdf", type: "pdf", size_kb: 201 },
    ],
  },
];

// ---------------------------------------------------------- phone numbers

export const mockPhoneNumbers: PhoneNumber[] = [
  {
    phone_number: "+19499195585",
    nickname: "Telnyx Main",
    provider: "Custom telephony",
    inbound_agent_id: "agent_8fbc66e7be3e0dde727f73a42b",
    inbound_agent_version_tag: "Latest Published",
    outbound_agent_id: "agent_8fbc66e7be3e0dde727f73a42b",
    outbound_agent_version_tag: "Latest Created",
    inbound_webhook_enabled: true,
    inbound_webhook_url:
      "https://mrnlotdwthdqcaicwyql.supabase.co/functions/v1/inbound-call-router",
    allowed_inbound_countries: ["Canada", "United States"],
    allowed_outbound_countries: ["Canada", "United States"],
    fallback_number: null,
  },
  {
    phone_number: "+14157078561",
    provider: "Twilio",
    inbound_agent_id: "agent_44e2c00b95a1d3f7e8c6b20a91",
    inbound_agent_version_tag: "Latest Published",
    outbound_agent_id: null,
    inbound_webhook_enabled: false,
    allowed_inbound_countries: ["United States"],
    allowed_outbound_countries: ["United States"],
    fallback_number: null,
  },
];

// --------------------------------------------------------------- contacts

const FIRST = ["Margaret", "Harold", "Dorothy", "Frank", "Evelyn", "Walter", "Ruth", "Gene", "Alma", "Ernest"];
const LAST = ["Whitfield", "Okafor", "Lindqvist", "Barrera", "Kowalski", "Meyers", "Delgado", "Hastings", "Novak", "Pruitt"];

export const mockContacts: Contact[] = Array.from({ length: 10 }, (_, i) => ({
  contact_id: `contact_${hex(i, 12)}`,
  phone_number: `+1626544${String(1000 + i * 37).slice(0, 4)}`,
  first_name: FIRST[i],
  last_name: LAST[i],
  related_conversations: 1 + ((i * 7) % 14),
  latest_conversation: NOW - i * 2 * DAY,
  do_not_call: i === 4,
  external_id: i % 3 === 0 ? `crm_${1000 + i}` : undefined,
}));

// -------------------------------------------------------------- analytics

const series = (n: number, gen: (i: number) => number): { date: string; value: number }[] =>
  Array.from({ length: n }, (_, i) => {
    const d = new Date(NOW - (n - 1 - i) * DAY);
    return {
      date: d.toLocaleDateString("en-US", { month: "short", day: "2-digit" }),
      value: gen(i),
    };
  });

export const mockAnalytics: AnalyticsData = {
  call_counts: 222,
  avg_duration_s: 52,
  avg_latency_ms: 1861,
  call_counts_series: series(28, (i) =>
    Math.max(2, Math.round(70 * Math.sin((Math.PI * (i + 3)) / 34) + ((i * 13) % 7) - 3)),
  ),
  concurrency_series: series(28, (i) => (i % 6 === 2 ? 3 : i % 4 === 1 ? 2 : 1)),
  call_successful: [
    { name: "Successful", value: 178 },
    { name: "Unsuccessful", value: 31 },
    { name: "Unknown", value: 13 },
  ],
  disconnection_reason: [
    { name: "agent hangup", value: 102 },
    { name: "user hangup", value: 61 },
    { name: "voicemail reached", value: 34 },
    { name: "dial no answer", value: 19 },
    { name: "other", value: 6 },
  ],
  user_sentiment: [
    { name: "Neutral", value: 132 },
    { name: "Positive", value: 58 },
    { name: "Unknown", value: 24 },
    { name: "Negative", value: 8 },
  ],
  phone_direction: [
    { name: "outbound", value: 171 },
    { name: "inbound", value: 51 },
  ],
};

// ------------------------------------------------------------------- QA

export const mockCohorts: QaCohort[] = [
  {
    cohort_id: "cohort_3f9a1c",
    name: "Transfer quality — Check-in agents",
    agents: ["CL - Check-in v0.2 Companion", "CL-Inbound v0.1"],
    sampling_pct: 100,
    weekly_max: 100,
    scoring_metric: "transfer",
    sample_size: 42,
    success_rate: 76,
    transfer_success_rate: 70,
    transfer_wait_time_s: 4.0,
    score: 70,
  },
];

// --------------------------------------------------------------- alerting

export const mockAlerts: Alert[] = [
  {
    alert_id: "alert_91bd02",
    name: "Call volume spike",
    check_every_min: 5,
    lookback_min: 30,
    metric: "Number of Calls",
    condition: "is above",
    threshold: 2,
    compare_to: "value" as const,
    notify_emails: ["ops@arhiteq.dev"],
    enabled: true,
  },
  {
    alert_id: "alert_5c7e44",
    name: "High failure rate — Betty",
    check_every_min: 15,
    lookback_min: 60,
    metric: "Failed Calls",
    condition: "is above",
    threshold: 5,
    compare_to: "value" as const,
    notify_emails: ["gmrnsk@gmail.com"],
    webhook_url: "https://hooks.arhiteq.dev/alerts",
    enabled: true,
  },
];

// --------------------------------------------------------------- api keys

export const mockApiKeys: ApiKey[] = [
  {
    key_id: "key_01",
    name: "Production",
    prefix: "key_53a0…9f2c",
    created_at: NOW - 120 * DAY,
    last_used_at: NOW - 0.2 * DAY,
  },
  {
    key_id: "key_02",
    name: "Staging",
    prefix: "key_b8d1…44ae",
    created_at: NOW - 40 * DAY,
    last_used_at: NOW - 6 * DAY,
  },
];

export const mockDeliveries: WebhookDelivery[] = Array.from({ length: 8 }, (_, i) => ({
  delivery_id: `evt_${hex(i + 40, 16)}`,
  event: ["call_started", "call_ended", "call_analyzed"][i % 3],
  status: i === 5 ? 500 : 200,
  timestamp: NOW - i * 0.4 * DAY,
  duration_ms: 120 + ((i * 97) % 800),
}));

// ------------------------------------------------------------- demo router
// lib/api.ts delegates here when NEXT_PUBLIC_DEMO_MODE=true. Paths that the
// api client consumes in the backend's raw Retell shape are converted from
// the UI-shaped mocks above; writes are rejected so demo mode can't pretend
// to persist anything.

function demoLlmId(agent: Agent): string {
  return `llm_demo_${agent.agent_id.slice(-12)}`;
}

function rawAgent(a: Agent): Record<string, unknown> {
  return {
    agent_id: a.agent_id,
    agent_name: a.agent_name,
    response_engine:
      a.agent_type === "conversation-flow"
        ? { type: "conversation-flow", conversation_flow_id: `flow_demo_${a.agent_id.slice(-8)}` }
        : { type: "retell-llm", llm_id: demoLlmId(a) },
    voice_id: a.voice_id,
    language: "en-US",
    version: a.version,
    is_published: true,
    webhook_url: a.webhook_url ?? null,
    interruption_sensitivity: a.interruption_sensitivity ?? 1,
    responsiveness: a.response_eagerness ?? 1,
    reminder_trigger_ms: (a.reminder_trigger_seconds ?? 10) * 1000,
    reminder_max_count: a.reminder_max_count ?? 1,
    boosted_keywords: a.boosted_keywords ?? null,
    enable_voicemail_detection: true,
    last_modification_timestamp: a.last_modification_timestamp,
  };
}

function rawLlm(a: Agent): Record<string, unknown> {
  return {
    llm_id: demoLlmId(a),
    model: "gemini-2.5-flash",
    model_temperature: 0,
    general_prompt: a.general_prompt ?? null,
    begin_message: a.begin_message ?? null,
    start_speaker: a.begin_message_mode === "user_first" ? "user" : "agent",
    general_tools: a.functions ?? null,
    knowledge_base_ids: a.knowledge_base_ids ?? null,
    last_modification_timestamp: a.last_modification_timestamp,
  };
}

function rawCall(c: Call): Record<string, unknown> {
  return {
    ...c,
    call_type: c.channel_type,
    call_analysis: {
      call_summary: c.call_summary,
      user_sentiment: c.user_sentiment,
      call_successful: c.call_successful,
    },
    call_cost: { combined_cost: c.cost },
    latency: c.end_to_end_latency_ms ? { e2e: { p50: c.end_to_end_latency_ms } } : null,
    transcript_object: (c.transcript ?? []).map((t) => ({ role: t.role, content: t.content })),
  };
}

function rawPhone(p: PhoneNumber): Record<string, unknown> {
  return {
    ...p,
    phone_number_type: p.provider === "Telnyx" ? "telnyx" : "custom",
    inbound_webhook_url: p.inbound_webhook_enabled ? (p.inbound_webhook_url ?? null) : null,
    last_modification_timestamp: NOW,
  };
}

function rawKb(k: KnowledgeBase): Record<string, unknown> {
  return {
    knowledge_base_id: k.knowledge_base_id,
    knowledge_base_name: k.knowledge_base_name,
    status: k.status === "ready" ? "complete" : "in_progress",
    knowledge_base_sources: k.documents.map((d) =>
      d.type === "url"
        ? { source_id: d.document_id, type: "url", title: d.name, content: "x".repeat(d.size_kb * 1024) }
        : {
            source_id: d.document_id,
            type: "document",
            title: d.name,
            filename: d.name,
            file_size: d.size_kb * 1024,
            file_url: `/get-knowledge-base-file/${k.knowledge_base_id}/source/${d.document_id}`,
          },
    ),
    last_refreshed_timestamp: NOW,
  };
}

export function demoResponse<T>(path: string, init?: RequestInit): T {
  const method = init?.method ?? "GET";
  if (method !== "GET" && path !== "/v2/list-calls") {
    throw new Error("Demo mode: writes are disabled (unset NEXT_PUBLIC_DEMO_MODE to use the real backend)");
  }
  const route = path.split("?")[0];

  if (route === "/list-agents") return mockAgents.map(rawAgent) as T;
  if (route === "/list-agent-folders")
    return [
      {
        folder_id: "folder_demo000000000000000001",
        folder_name: "Template Agents",
        last_modification_timestamp: NOW,
      },
    ] as T;
  if (route.startsWith("/get-agent/")) {
    const a = mockAgents.find((x) => x.agent_id === route.split("/").pop());
    if (!a) throw new Error("Agent not found");
    return rawAgent(a) as T;
  }
  if (route.startsWith("/get-retell-llm/")) {
    const id = route.split("/").pop();
    const a = mockAgents.find((x) => demoLlmId(x) === id);
    if (!a) throw new Error("LLM not found");
    return rawLlm(a) as T;
  }
  if (route === "/v2/list-calls") {
    // Honor the from_number/to_number filters the contact drawer relies on —
    // returning everything would show every demo call under every contact.
    let calls = mockCalls;
    try {
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      const fc = body.filter_criteria ?? {};
      const from: string[] | undefined = fc.from_number;
      const to: string[] | undefined = fc.to_number;
      if (from?.length) calls = calls.filter((c) => from.includes(c.from_number));
      if (to?.length) calls = calls.filter((c) => to.includes(c.to_number));
      if (typeof body.limit === "number") calls = calls.slice(0, body.limit);
    } catch {
      // Malformed body → unfiltered list, same as before.
    }
    return calls.map(rawCall) as T;
  }
  if (route.startsWith("/v2/get-call/")) {
    const c = mockCalls.find((x) => x.call_id === route.split("/").pop());
    if (!c) throw new Error("Call not found");
    return rawCall(c) as T;
  }
  if (route === "/list-phone-numbers") return mockPhoneNumbers.map(rawPhone) as T;
  if (route === "/list-knowledge-bases") return mockKnowledgeBases.map(rawKb) as T;
  if (route === "/list-contacts") return mockContacts as T;
  if (route === "/analytics/calls") return mockAnalytics as T;
  if (route === "/list-qa-cohorts") return mockCohorts as T;
  if (route === "/list-alerts") return mockAlerts as T;
  if (route === "/list-api-keys") return mockApiKeys as T;
  if (route === "/list-webhook-deliveries") return mockDeliveries as T;
  if (route === "/workspace")
    return {
      workspace_id: "ws_demo",
      name: "Demo Workspace",
      webhook_url: null,
      // Pages hard-deref ws.settings.* — keep this in step with WorkspaceSettings.
      settings: {
        billing_email: null,
        purchased_concurrency: 0,
        reserved_inbound_concurrency: 0,
        concurrency_burst_enabled: false,
        llm_token_limit: 4096,
        cps_limits: { telnyx: 1, twilio: 1, custom_telephony: 1 },
        llm_failover_enabled: false,
        auto_call_retry_enabled: false,
        conductor_messages_enabled: false,
        contact_field_definitions: [],
      },
    } as T;

  throw new Error(`Demo mode: no canned data for ${path}`);
}
