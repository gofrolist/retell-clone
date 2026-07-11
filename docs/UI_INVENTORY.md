# Architeq dashboard — UI inventory

Derived from the 22 reference screenshots in `screenshots/` (Retell dashboard,
light theme). Rebrand everything "Retell" → **Architeq**.

## Visual style

Light UI. App chrome background very light gray (#F7F8FA-ish); white content
cards, 8–12px radius, hairline borders; near-black primary buttons; blue accent
for links/focus/charts; status dots (green/red/blue/gray); pill badges for
types and phone numbers; Inter-style sans, 13–14px body, semibold section
titles; thin-line icons (Lucide); accordions with left icon + right chevron;
toggles dark when on; thin sliders with round thumb + right-aligned value.

## Navigation tree

```
[Architeq logo]
[Workspace switcher: avatar + name + chevrons]
BUILD:    Agents (/agents), Knowledge Base (/knowledgeBase)
DEPLOY:   Phone Numbers (/phoneNumbers), Batch Call (/batchCall)
DATA:     Call History (/call-history), Chat History (stub), Contacts (/contacts)
MONITOR:  Analytics (/analytics), Live Monitoring (stub),
          AI Quality Assurance (/quality-assurance), Alerting (/alerting)
SYSTEM:   Integrations (stub), Settings (/settings/* — sidebar becomes sub-nav:
          ‹ GO BACK, Limits, Reliability, API Keys, Webhooks, Workspace)
FOOTER:   plan pill ("Pay As You Go"), account email row, Help | Updates
```

## Pages

1. **Agents list** — 3-pane: sidebar / secondary panel ("All Agents" +
   FOLDERS with "+") / content. Header: title, search, "Import",
   "Create an Agent" (dark, dropdown caret). Table: Agent Name | Agent Type
   (Single Prompt | Conversation Flow) | Voice | Phone (pill) | Edited by.
   Row kebab menu; pagination.
2. **Create Agent modal** — type radio-cards ("Single prompt" — "Easy to
   start…", "Conversational flow" — "Production-ready…"), template category
   tabs (All | Receptionist | Outbound Sales & Reactivation | Appointment
   Booking | Lead Qualification | Customer S…), template cards incl. "Build
   from scratch" and "Generate from prompt" (Suggested badge).
3. **Agent detail (Single Prompt editor)** — full-width, sidebar hidden.
   Header: back, name, "Environment" chip, tabs Create|Simulation, "…",
   share, version "V83" + history, "Create new version", "Conductor".
   Left: meta row (Cost $/min · Latency ms · Tokens), ID copy; selector row:
   LLM model ("Gemini 3.1 Flash Lite" + gear), voice ("Katie"), language
   ("English (US)"), "Agent Handbook"; big prompt textarea with `{{var}}`
   chips; Welcome Message (AI speaks first / Custom message /
   `{{bm_greeting}}`, "Pause Before Speaking: 0.6s").
   Middle: accordions — Functions, Knowledge Base, Speech Settings, Realtime
   Transcription Settings, Call Settings, Post-Call Data Extraction,
   Security & Fallback Settings, Webhook Settings, MCPs.
   Right: test panel, tabs Test Audio | Test LLM, `{}` dynamic-vars button,
   mic illustration, "Run Test".
   Accordion contents:
   - **Functions**: chips with edit/trash (end_call, transfer_call built-ins;
     custom: cancel_subscription, save_conversation_note, mark_dnc,
     create_trial, flag_crisis, log_outcome, capture_family_contact,
     schedule_callback, send_family_sms, initiate_payment,
     create_demo_reminder, save_lead_info, create_checkout_link,
     log_churn_reason, web_lookup); "+ Add".
   - **Knowledge Base**: attached KB items; Advanced: "Adjust KB Retrieval
     Chunks and Similarity", "Configure Knowledge Base Instruction".
   - **Speech Settings**: Background Sound (None + gear); Response Eagerness
     slider (patient↔eager, value 1) + "Dynamically adjust" checkbox;
     Interruption Sensitivity slider (0.92); Reminder Message Frequency
     (10 s / 1 times); Pronunciation "+ Add".
   - **Realtime Transcription**: Denoising Mode (Remove noise ✓ / Remove
     noise + background speech / No denoising); Transcription Mode (speed ✓ /
     accuracy / custom); Vocabulary Specialization (General ✓ / Medical);
     Boosted Keywords (comma list input).
   - **Call Settings**: Voicemail Detection toggle; iOS/Android Call Screen
     Handling; IVR Hangup; User Keypad Input Detection (ON) → Timeout slider
     2.5s, Termination Key, Digit Limit; End Call on Silence 30s; Max Call
     Duration.
   - **Post-Call Data Extraction**: rows Call Summary, Call Successful, User
     Sentiment; "+ Add"; extraction model dropdown ("GPT-4.1" → ours:
     Gemini).
   - **Security & Fallback**: Data Storage (Everything ✓ + "Retention: Keep
     forever" / Everything except PII / Basic Attributes Only); PII Redaction
     "Set Up"; Safety Guardrails "Set Up"; Opt In Secure URLs toggle;
     Fallback Voice (Automatic ✓ / Select); Default Dynamic Variables
     "Set Up".
   - **Webhook Settings**: Agent Level Webhook URL (+ Test); Webhook Timeout
     slider 5s; Webhook Events "Set Up".
4. **Knowledge Base** — list panel (KB names, "+") + detail: title, "ID:
   know…" copy, "Uploaded by: <date>" green check, Edit/trash. Document rows:
   type icon (MD/PDF), name, size, download.
5. **Phone Numbers** — list panel (nickname or number, "+", search) + detail:
   title (pencil), "ID: +1… · Provider: Custom telephony", "Make an outbound
   call", "…". Cards: Inbound Call Agent (agent dropdown + "Latest Published"
   badge, A/B Testing toggle, "Add an inbound webhook" checkbox + URL input,
   Allowed Inbound Countries tags, Fallback Number), Outbound Call Agent
   (agent dropdown + "Latest Created" badge, Allowed Outbound Countries),
   Advanced Add-Ons (SMS, Verified Phone Number $10/mo, Branded Call
   $0.1/call).
6. **Buy Phone Number modal** — tabs Twilio | Telnyx; search; "$2/month"
   info banner; number+city rows; "Outbound Transport: TCP"; Cancel/Purchase.
7. **Batch Call** — full-page form: name, From number dropdown, CSV dropzone
   (+ "Download the template"), Send Now | Schedule radio-cards, "When Calls
   Can Run: 00:00-23:59, Mon-Sun", Reserved Concurrency stepper (5),
   "Concurrency allocated to batch calling: 15" banner, ToS line, "Save as
   draft" / "Send". Right: Recipients panel ("Please upload recipients
   first").
8. **Call History** — toolbar: Date Range, Filter; column settings, Actions.
   Columns: Time | Duration | Channel Type (phone_call) | Cost | Session ID
   (call_…, copy) | End Reason (agent hangup, user hangup, voicemail reached,
   dial no answer — colored dots) | Session Status (ended, not_connected) |
   User Sentiment (Neutral/Positive/Unknown) | From.
   Footer: "Page 1 of N • Total Session: 705", pager, "50 / page".
9. **Call detail drawer** — over Call History, "↑ ↓ to navigate". Meta:
   Agent (+id), Version, Call ID, "+1… → +1… (Outbound)", Duration range +
   (3m 40s), Cost, LLM Token. Audio player + download. Conversation Analysis
   (Rerun): Call Successful, Call Status, User Sentiment, Disconnection
   Reason, End to End Latency. Summary paragraph. Tabs: Transcription | Data
   | Detail Logs | Packet Capture; "View In Test Playground". Transcript:
   User/Agent turns + timestamps, "Knowledge Base Retrieval" inline marker.
   Right contact panel: contact info fields, "View Contact", "Contact
   conversations (N)" list (direction, date, duration).
10. **Contacts** — "Connect your CRM" banner; table: Phone Number | First
    Name | Last Name | Contact ID | Related Conversations | Latest
    Conversation | Do Not Call | External ID.
11. **Analytics** — tabs "Call Dashboard" | "Chat Dashboard" | "+"; date
    range, Filter, Breakdown, "+ Add Chart". Stat tiles: Call Counts 222,
    Call Duration 52s, Call Latency 1861ms. Charts: Call Counts (area),
    Concurrency Used (bars), donuts: Call Successful, Disconnection Reason,
    User Sentiment, Phone inbound/outbound.
12. **AI Quality Assurance** — metric cards (Transfer Success Rate 70%,
    Transfer Wait Time 4.0s, trend chart), "+ Create QA"; Create Cohort modal
    (2 steps: Define QA Cohort → success criteria; name, agents multiselect,
    date range, filter builder [Duration > 30 s], sampling % 100, weekly max
    100).
13. **Alerting** — alert list rows with Edit/…; Create Alert modal: name,
    "Check every [5 min] for the last [30 min]", "Compare to certain value" |
    "Compare to last cycle", metric "Number of Calls", "when sum [is above]
    [2]", agent filter, notify via Email "+ Add" / Webhook URL + Test.
14. **Settings › Limits** — Concurrent Calls Limit 20 (+ Adjust, Reserve
    Inbound Capacity), Concurrency Burst toggle, Conductor messages toggle,
    LLM Token Limit 32768, Telnyx CPS 1 / Twilio CPS 1 / Custom Telephony
    CPS 1 (Adjust Limit).
    Sub-pages to build simply: Reliability, API Keys, Webhooks, Workspace.
15. Stubs (nav item + empty state): Chat History, Live Monitoring,
    Integrations.
