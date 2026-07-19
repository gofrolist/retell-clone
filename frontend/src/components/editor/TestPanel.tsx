"use client";

import { PillTabs } from "@/components/ui/Tabs";
import { api, type ChatMessage } from "@/lib/api";
import { Room, RoomEvent, Track } from "livekit-client";
import { Braces, Info, Loader2, Mic, Phone, Play, RotateCcw, Send } from "lucide-react";
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
        <AudioTab agentId={agentId} />
      </div>
      <div className={tab === "llm" ? "flex min-h-0 grow flex-col" : "hidden"}>
        <LlmChat agentId={agentId} />
      </div>
    </div>
  );
}

type CallPhase = "idle" | "preflight" | "connecting" | "active" | "ended";

interface TranscriptSegment {
  id: string;
  role: "agent" | "user";
  text: string;
}

/** Live web call against the agent (Retell "Test Audio"). */
function AudioTab({ agentId }: { agentId: string }) {
  const [phase, setPhase] = useState<CallPhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [segments, setSegments] = useState<TranscriptSegment[]>([]);
  const [elapsed, setElapsed] = useState(0);
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  const roomRef = useRef<Room | null>(null);
  // Synchronous re-entrancy guard: set at the top of start(), before any
  // await, so a rapid double-click on "Run Test" can't spin up two Rooms —
  // state alone can't close this window since both clicks share a render.
  const startingRef = useRef(false);
  // Flips true in the unmount cleanup so a start() continuation resumed
  // after the panel is gone knows to bail instead of connecting a call
  // nobody can hang up.
  const unmountedRef = useRef(false);
  const audioRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Keep the newest transcript line in view.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [segments]);

  // Call timer.
  useEffect(() => {
    if (phase !== "active") return;
    const t = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [phase]);

  // Hang up when the panel goes away (page navigation). Null the ref first so
  // any in-flight start() sees it change and knows the call was cancelled.
  // The flag must be re-armed on setup: StrictMode runs mount → cleanup →
  // remount on the same instance, and a stuck-true flag silently disables
  // every future start().
  useEffect(() => {
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
      const room = roomRef.current;
      roomRef.current = null;
      void room?.disconnect();
    };
  }, []);

  const hangUp = () => void roomRef.current?.disconnect();

  const start = async () => {
    // Bail out synchronously if a call is already starting or live.
    if (startingRef.current || roomRef.current) return;
    startingRef.current = true;
    setError(null);
    setSegments([]);
    setElapsed(0);
    setPhase("preflight");
    // Preflight mic permission: a denial must abort before any call exists.
    try {
      const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
      mic.getTracks().forEach((t) => t.stop());
    } catch {
      startingRef.current = false;
      setPhase("idle");
      setError("Microphone access is blocked — allow it in the browser and retry.");
      return;
    }
    // The panel unmounted while the preflight was pending: nothing to attach to.
    if (unmountedRef.current) {
      startingRef.current = false;
      return;
    }
    setPhase("connecting");
    const room = new Room();
    roomRef.current = room;
    try {
      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Audio) audioRef.current?.appendChild(track.attach());
      });
      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        setAgentSpeaking(speakers.some((p) => p.identity !== room.localParticipant.identity));
      });
      // The worker left (it ended the call server-side): leave too.
      room.on(RoomEvent.ParticipantDisconnected, () => void room.disconnect());
      room.on(RoomEvent.Disconnected, () => {
        // Only clear the ref if it still points at this room — an orphaned
        // loser from a double-start (or a room replaced after this one was
        // superseded) must not stomp on a surviving call's ref.
        if (roomRef.current === room) roomRef.current = null;
        startingRef.current = false;
        setAgentSpeaking(false);
        setPhase((p) => (p === "idle" ? p : "ended"));
        // Drop attached remote audio elements so "Run Again" starts clean.
        audioRef.current?.replaceChildren();
      });
      // Register before connect so the agent's greeting is never missed.
      room.registerTextStreamHandler("lk.transcription", (reader, participantInfo) => {
        void (async () => {
          const text = await reader.readAll();
          if (!text) return;
          const attrs = reader.info.attributes ?? {};
          const id = attrs["lk.segment_id"] ?? reader.info.id;
          const trackId = attrs["lk.transcribed_track_id"] ?? "";
          const isUser =
            room.localParticipant.audioTrackPublications.has(trackId) ||
            participantInfo.identity === room.localParticipant.identity;
          setSegments((prev) => {
            const seg: TranscriptSegment = { id, role: isUser ? "user" : "agent", text };
            const i = prev.findIndex((s) => s.id === id);
            if (i < 0) return [...prev, seg];
            const next = prev.slice();
            next[i] = seg;
            return next;
          });
        })();
      });
      const call = await api.createWebCall(agentId);
      // Cancelled (unmount, or superseded) while awaiting the call: leave quietly.
      if (roomRef.current !== room) {
        startingRef.current = false;
        void room.disconnect();
        return;
      }
      await room.connect(call.livekit_server_url, call.access_token);
      if (roomRef.current !== room) {
        startingRef.current = false;
        void room.disconnect();
        return;
      }
      await room.localParticipant.setMicrophoneEnabled(true);
      if (roomRef.current !== room) {
        startingRef.current = false;
        void room.disconnect();
        return;
      }
      startingRef.current = false;
      setPhase("active");
    } catch (e) {
      if (roomRef.current === room) roomRef.current = null;
      startingRef.current = false;
      void room.disconnect();
      setPhase("idle");
      setError(e instanceof Error ? e.message : "Failed to start the test call");
    }
  };

  const mins = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const secs = String(elapsed % 60).padStart(2, "0");
  const inCall = phase === "connecting" || phase === "active";

  return (
    <>
      {/* Hidden sink the agent's audio elements attach into. */}
      <div ref={audioRef} className="hidden" />
      {segments.length > 0 || phase === "active" ? (
        <div ref={scrollRef} className="min-h-0 grow space-y-3 overflow-y-auto px-4 py-4">
          {segments.map((m) => (
            <div
              key={m.id}
              className={
                m.role === "user"
                  ? "ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-accent px-3.5 py-2 text-[13px] text-white"
                  : "mr-auto max-w-[85%] rounded-2xl rounded-bl-sm border border-line bg-app px-3.5 py-2 text-[13px] text-ink whitespace-pre-wrap"
              }
            >
              {m.text}
            </div>
          ))}
        </div>
      ) : (
        <div className="flex grow flex-col items-center justify-center gap-6 px-6">
          <div className="relative flex size-24 items-center justify-center">
            <span
              className={`absolute inset-0 rounded-full bg-app ${agentSpeaking ? "animate-pulse" : ""}`}
            />
            <span className="absolute inset-3 rounded-full border border-line bg-white shadow-sm" />
            <Mic className="relative size-8 text-line-strong" strokeWidth={1.5} />
          </div>
          {phase === "ended" && <p className="text-xs text-sub">Call ended.</p>}
        </div>
      )}
      <div className="space-y-3 border-t border-line px-6 py-4">
        {error && <p className="px-1 text-center text-xs text-bad">{error}</p>}
        {!inCall && (
          <p className="flex items-center justify-center gap-1.5 text-center text-xs text-sub">
            <Info className="size-3.5 shrink-0" />
            Please note call transfer is not supported in Webcall.
          </p>
        )}
        <div className="flex items-center justify-center gap-3">
          {inCall ? (
            <>
              <span className="text-xs tabular-nums text-sub">
                {phase === "connecting" ? "Connecting…" : `${mins}:${secs}`}
              </span>
              <button
                onClick={hangUp}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-bad px-5 text-[13px] font-medium text-white shadow-sm transition-colors hover:bg-bad/85 cursor-pointer"
              >
                <Phone className="size-3.5" />
                End Call
              </button>
            </>
          ) : phase === "preflight" ? (
            <button
              disabled
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line bg-white px-5 text-[13px] font-medium opacity-50 shadow-sm cursor-not-allowed"
            >
              <Loader2 className="size-3.5 animate-spin" />
              Waiting for microphone…
            </button>
          ) : (
            <button
              onClick={() => void start()}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line bg-white px-5 text-[13px] font-medium shadow-sm transition-colors hover:bg-app cursor-pointer"
            >
              {phase === "ended" ? <RotateCcw className="size-3.5" /> : <Play className="size-3.5" />}
              {phase === "ended" ? "Run Again" : "Run Test"}
            </button>
          )}
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
