"use client";

import { seedFlow } from "@/components/flow/flowModel";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  CalendarCheck,
  FileText,
  Filter,
  Headset,
  PhoneOutgoing,
  Plus,
  Sparkles,
  Workflow,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

const CATEGORIES = [
  "All",
  "Receptionist",
  "Outbound Sales & Reactivation",
  "Appointment Booking",
  "Lead Qualification",
  "Customer S…",
];

const TEMPLATES = [
  {
    name: "Build from scratch",
    desc: "Start with a blank prompt.",
    icon: Plus,
    suggested: false,
    disabled: false,
    prompt: "",
    beginMessage: "",
  },
  {
    name: "Generate from prompt",
    desc: "Describe your agent, we draft it.",
    icon: Sparkles,
    suggested: true,
    disabled: true, // no generation backend yet
    prompt: "",
    beginMessage: "",
  },
  {
    name: "Healthcare Check-in",
    desc: "Daily wellness check-in calls.",
    icon: Headset,
    suggested: false,
    disabled: false,
    prompt:
      "You are a warm, patient care coordinator making a daily wellness check-in call. Ask how the person is feeling today, whether they have taken their medications, and whether they need anything. Keep questions short, one at a time, and speak clearly. If they report a medical emergency, tell them to hang up and dial 911 immediately.",
    beginMessage: "Hi, this is your daily wellness check-in call. How are you feeling today?",
  },
  {
    name: "Front Desk Receptionist",
    desc: "Answer, route and book callers.",
    icon: CalendarCheck,
    suggested: false,
    disabled: false,
    prompt:
      "You are a friendly front desk receptionist. Greet the caller, find out why they are calling, and either answer their question, take a message, or book an appointment. Collect the caller's name, phone number, and preferred time before confirming any booking. Be concise and professional.",
    beginMessage: "Thank you for calling! How can I help you today?",
  },
  {
    name: "Outbound Lead Reactivation",
    desc: "Re-engage cold leads at scale.",
    icon: PhoneOutgoing,
    suggested: false,
    disabled: false,
    prompt:
      "You are an upbeat outbound sales representative re-engaging a lead who previously showed interest. Remind them briefly why you are calling, ask if they are still interested, and offer to schedule a follow-up with a specialist. Respect a clear no and offer to remove them from the call list if asked.",
    beginMessage: "Hi! I'm following up on your earlier interest — do you have a quick minute?",
  },
  {
    name: "Lead Qualification",
    desc: "Qualify and score inbound leads.",
    icon: Filter,
    suggested: false,
    disabled: false,
    prompt:
      "You are a lead qualification specialist. Ask the caller about their needs, timeline, budget range, and decision-making role. Ask one question at a time and acknowledge each answer. Once you have the key details, thank them and let them know a specialist will follow up shortly.",
    beginMessage: "Hi, thanks for reaching out! I'd love to learn a bit about what you're looking for.",
  },
];

export default function CreateAgentModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [type, setType] = useState<"single" | "flow">("single");
  const [category, setCategory] = useState("All");
  const [template, setTemplate] = useState("Build from scratch");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  // This modal is mounted permanently by the agents page (`open` merely
  // toggles it), so every piece of state survives a Cancel and is still there
  // on the next open. That is silent data corruption for `type`: pick
  // "Conversational flow", cancel, reopen, pick a prompt template, Create —
  // and you get a FLOW agent carrying the template's name and none of its
  // prompt, with nothing on screen saying so. Reset the whole picker on each
  // open instead of just `type`: a stale template/category/error is the same
  // class of surprise, only less destructive.
  //
  // `creating` is deliberately NOT reset here, matching `CreateAlertModal` and
  // `TestCaseModal`, which both exclude their own in-flight flags from the
  // same pattern. `Modal`'s backdrop, Escape and X all close unconditionally —
  // only the footer buttons respect `creating` — so a user who closes a
  // seemingly-stuck modal mid-create and reopens it would get an enabled
  // Create button with the first request still in flight. Submitting again
  // makes two agents, and whichever request lands first `router.push`es away
  // from the other. Leaving `creating` alone keeps the button disabled until
  // the in-flight request actually settles.
  useEffect(() => {
    if (!open) return;
    setType("single");
    setCategory("All");
    setTemplate("Build from scratch");
    setError(null);
  }, [open]);

  const handleCreate = async () => {
    const tpl = TEMPLATES.find((t) => t.name === template);
    setCreating(true);
    setError(null);
    try {
      // Two steps either way: the engine is created first, then the agent that
      // points at it. A flow agent's seed graph already reaches an end node, so
      // it is callable the moment it exists.
      const response_engine =
        type === "flow"
          ? {
              type: "conversation-flow" as const,
              conversation_flow_id: (await api.createConversationFlow(seedFlow()))
                .conversation_flow_id,
            }
          : {
              type: "retell-llm" as const,
              llm_id: (
                await api.createLlm({
                  ...(tpl?.prompt ? { general_prompt: tpl.prompt } : {}),
                  ...(tpl?.beginMessage ? { begin_message: tpl.beginMessage } : {}),
                })
              ).llm_id,
            };
      const agent = await api.createAgent({
        agent_name: template === "Build from scratch" ? "New Agent" : template,
        response_engine,
        voice_id: "cartesia-sonic-english",
      });
      onClose();
      router.push(`/agents/${agent.agent_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create agent");
      setCreating(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Create an Agent"
      width="max-w-3xl"
      footer={
        <>
          {error && <span className="mr-auto text-[12.5px] text-bad">{error}</span>}
          <Button variant="ghost" onClick={onClose} disabled={creating}>
            Cancel
          </Button>
          <Button variant="primary" onClick={handleCreate} disabled={creating}>
            {creating ? "Creating…" : "Create"}
          </Button>
        </>
      }
    >
      <div className="grid grid-cols-2 gap-3">
        {(
          [
            {
              key: "single",
              title: "Single prompt",
              desc: "Easy to start. Works with one system prompt for simple use cases.",
              icon: FileText,
            },
            {
              key: "flow",
              title: "Conversational flow",
              desc: "Production-ready. Design multi-state flows with full control.",
              icon: Workflow,
            },
          ] as const
        ).map((t) => (
          <button
            key={t.key}
            onClick={() => {
              setType(t.key);
              // The templates below are prompts, and a flow agent has no
              // prompt to put one in. Clearing the pick is what actually
              // stops a discarded template from naming the agent it was
              // discarded from (`handleCreate` names by `template`); hiding
              // the grid, below, is what stops one being picked at all.
              setTemplate("Build from scratch");
            }}
            className={cn(
              "cursor-pointer rounded-xl border p-4 text-left transition-colors",
              type === t.key
                ? "border-ink ring-1 ring-ink"
                : "border-line hover:border-line-strong",
            )}
          >
            <t.icon className="mb-2 size-5 text-sub" strokeWidth={1.8} />
            <div className="text-[14px] font-semibold">{t.title}</div>
            <div className="mt-0.5 text-[12.5px] text-sub">{t.desc}</div>
          </button>
        ))}
      </div>

      {type === "flow" ? (
        <p className="mt-5 text-[13px] text-sub">
          Flow agents start from a two-node graph — a greeting and an end node — which you edit on
          the canvas. The prompt templates below apply to single-prompt agents only.
        </p>
      ) : (
        <>
          <div className="mt-5 flex items-center gap-1.5 overflow-x-auto pb-1">
            {CATEGORIES.map((c) => (
              <button
                key={c}
                onClick={() => setCategory(c)}
                className={cn(
                  "whitespace-nowrap rounded-full border px-3 py-1 text-[12.5px] font-medium transition-colors cursor-pointer",
                  category === c
                    ? "border-ink bg-ink text-white"
                    : "border-line bg-white text-sub hover:text-ink",
                )}
              >
                {c}
              </button>
            ))}
          </div>

          <div className="mt-3 grid grid-cols-3 gap-3">
            {TEMPLATES.map((t) => (
              <button
                key={t.name}
                onClick={() => !t.disabled && setTemplate(t.name)}
                disabled={t.disabled}
                title={t.disabled ? "Not available yet" : undefined}
                className={cn(
                  "relative rounded-xl border p-3.5 text-left transition-colors",
                  t.disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer",
                  template === t.name
                    ? "border-ink ring-1 ring-ink"
                    : "border-line hover:border-line-strong",
                )}
              >
                {t.suggested && (
                  <Badge tone="blue" className="absolute right-2 top-2">
                    Suggested
                  </Badge>
                )}
                <t.icon className="mb-2 size-4.5 text-sub" strokeWidth={1.8} />
                <div className="text-[13px] font-semibold">{t.name}</div>
                <div className="mt-0.5 text-xs text-sub">{t.desc}</div>
              </button>
            ))}
          </div>
        </>
      )}
    </Modal>
  );
}
