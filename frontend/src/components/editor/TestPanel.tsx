"use client";

import { PillTabs } from "@/components/ui/Tabs";
import { api, type ChatMessage } from "@/lib/api";
import { Braces, Info, Loader2, Mic, Play, RotateCcw, Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export default function TestPanel({ agentId }: { agentId: string }) {
  const [tab, setTab] = useState("llm");
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-4 pt-3">
        <PillTabs
          tabs={[
            { key: "audio", label: "Test Audio" },
            { key: "llm", label: "Test LLM" },
          ]}
          active={tab}
          onChange={setTab}
        />
        <button
          disabled
          title="Not available yet"
          className="flex size-8 items-center justify-center rounded-lg border border-line bg-white text-sub opacity-40 cursor-not-allowed"
          aria-label="Dynamic variables"
        >
          <Braces className="size-4" />
        </button>
      </div>

      {/* Both tabs stay mounted (toggled with `hidden`) so the LLM chat keeps
          its conversation when the user peeks at the Audio tab. */}
      <div className={tab === "audio" ? "flex min-h-0 grow flex-col" : "hidden"}>
        <AudioTab />
      </div>
      <div className={tab === "llm" ? "flex min-h-0 grow flex-col" : "hidden"}>
        <LlmChat agentId={agentId} />
      </div>
    </div>
  );
}

/** Live web call — not wired up yet (needs the LiveKit browser client). */
function AudioTab() {
  return (
    <>
      <div className="flex grow flex-col items-center justify-center gap-6 px-6">
        <div className="relative flex size-24 items-center justify-center">
          <span className="absolute inset-0 rounded-full bg-app" />
          <span className="absolute inset-3 rounded-full border border-line bg-white shadow-sm" />
          <Mic className="relative size-8 text-line-strong" strokeWidth={1.5} />
        </div>
      </div>
      <div className="space-y-3 px-6 pb-6">
        <p className="flex items-center justify-center gap-1.5 text-center text-xs text-sub">
          <Info className="size-3.5 shrink-0" />
          Please note call transfer is not supported in Webcall.
        </p>
        <div className="flex justify-center">
          <button
            disabled
            title="Test calls not available yet"
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line bg-white px-5 text-[13px] font-medium shadow-sm opacity-50 cursor-not-allowed"
          >
            <Play className="size-3.5" />
            Run Test
          </button>
        </div>
      </div>
    </>
  );
}

/** Text chat against the agent's saved LLM prompt (Retell "Test LLM"). */
function LlmChat({ agentId }: { agentId: string }) {
  const [chatId, setChatId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Mirror chatId into a ref so the unmount cleanup ends the right chat without
  // re-subscribing the effect on every id change.
  const chatIdRef = useRef<string | null>(null);
  useEffect(() => {
    chatIdRef.current = chatId;
  }, [chatId]);

  // Keep the newest message (and the typing indicator) in view.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  // Auto-grow the textarea up to the max height so multi-line input is visible.
  useEffect(() => {
    const ta = inputRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 112)}px`;
  }, [input]);

  // End the chat when the panel goes away (page navigation) so test sessions
  // don't linger as `ongoing` rows.
  useEffect(
    () => () => {
      if (chatIdRef.current) api.endChat(chatIdRef.current).catch(() => {});
    },
    [],
  );

  const send = async () => {
    const content = input.trim();
    if (!content || sending) return;
    setError(null);
    setInput("");
    const userMsg: ChatMessage = {
      message_id: `local_${Date.now()}`,
      role: "user",
      content,
      created_timestamp: 0,
    };
    setMessages((m) => [...m, userMsg]);
    setSending(true);
    try {
      // Create the chat lazily on the first turn so an untouched tab makes no
      // server call; reuse the same chat_id for the rest of the session.
      let id = chatId;
      if (!id) {
        id = (await api.createChat(agentId)).chat_id;
        setChatId(id);
      }
      const res = await api.createChatCompletion(id, content);
      setMessages((m) => [...m, ...res.messages]);
      if (res.is_fallback) {
        setError(
          "Showing a fallback reply — the agent's LLM isn't configured or the call failed. Check the backend logs / Gemini credentials.",
        );
      }
    } catch (e) {
      // The optimistic user turn never reached the backend; drop it (so the
      // local view can't drift from the chat's server history) and restore the
      // text so the user can retry.
      setMessages((m) => m.filter((x) => x !== userMsg));
      setInput(content);
      setError(e instanceof Error ? e.message : "Failed to get a reply");
    } finally {
      setSending(false);
    }
  };

  const restart = () => {
    const prev = chatId;
    setChatId(null);
    setMessages([]);
    setError(null);
    setInput("");
    if (prev) api.endChat(prev).catch(() => {}); // best-effort cleanup
  };

  return (
    <div className="flex min-h-0 grow flex-col">
      <div ref={scrollRef} className="min-h-0 grow space-y-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 && !sending ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
            <Mic className="size-7 text-line-strong" strokeWidth={1.5} />
            <p className="text-[13px] font-medium text-ink">Test your agent&apos;s responses</p>
            <p className="max-w-[240px] text-xs text-sub">
              Chat with the agent&apos;s LLM using its saved prompt. Save the agent to test your
              latest changes.
            </p>
          </div>
        ) : (
          messages.map((m) => (
            <div
              key={m.message_id}
              className={
                m.role === "user"
                  ? "ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-3.5 py-2 text-[13px] text-white"
                  : "mr-auto max-w-[85%] rounded-2xl rounded-bl-sm border border-line bg-app px-3.5 py-2 text-[13px] text-ink whitespace-pre-wrap"
              }
            >
              {m.content}
            </div>
          ))
        )}
        {sending && (
          <div className="mr-auto flex items-center gap-1.5 rounded-2xl rounded-bl-sm border border-line bg-app px-3.5 py-2 text-[13px] text-sub">
            <Loader2 className="size-3.5 animate-spin" />
            Thinking…
          </div>
        )}
      </div>

      <div className="border-t border-line p-3">
        {error && <p className="mb-2 px-1 text-xs text-bad">{error}</p>}
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            rows={1}
            placeholder="Type a message…"
            className="max-h-28 min-h-9 flex-1 resize-none rounded-lg border border-line bg-white px-3 py-2 text-[13px] outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/15"
          />
          {messages.length > 0 && (
            <button
              onClick={restart}
              disabled={sending}
              title="Restart chat"
              aria-label="Restart chat"
              className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-line bg-white text-sub shadow-sm transition-colors hover:text-ink disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
            >
              <RotateCcw className="size-4" />
            </button>
          )}
          <button
            onClick={() => void send()}
            disabled={!input.trim() || sending}
            aria-label="Send message"
            className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-ink text-white shadow-sm transition-colors hover:bg-black/80 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
          >
            <Send className="size-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
