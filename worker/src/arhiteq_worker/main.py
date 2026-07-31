"""Arhiteq voice worker — livekit-agents entrypoint (agent_name
"arhiteq-agent"). One job per call; see docs/ARCHITECTURE.md and
docs/INTERNAL_API.md for the binding contracts.

Retell agent-config → livekit-agents mappings (documented per spec):
- interruption_sensitivity (0..1, 1 = easiest to interrupt)
    → allow_interruptions (False when 0) and min_interruption_duration
      (0.1s at sensitivity 1 … 1.5s at sensitivity 0).
- responsiveness (0..1, 1 = fastest replies)
    → endpointing delays: min_delay 0.2s..1.2s, max_delay 3s..6s.
- enable_backchannel → no livekit-agents equivalent; approximated with a
  system-prompt instruction to use brief acknowledgments.
- max_call_duration_ms → worker-side timer → hangup with
  disconnection_reason "max_duration_reached".
- end_call_after_silence_ms → user_away_timeout; on user state "away" the
  call ends with disconnection_reason "inactivity".
- voice_speed → Cartesia TTS speed (Retell 0.5..2.0, 1.0 neutral →
  Cartesia -1.0..1.0, 0.0 neutral).
- voice_temperature → no Cartesia Sonic equivalent; ignored (TODO if
  Cartesia exposes a generation-temperature knob).
- boosted_keywords → no Cartesia ink-whisper equivalent; ignored.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from functools import lru_cache
from typing import Any

import httpx
from livekit import api, rtc
from livekit.agents import Agent, AgentSession, CloseReason, JobContext, RoomInputOptions, cli

from arhiteq_worker import amd, metrics
from arhiteq_worker.config import CallConfig, ConversationFlowConfig, gemini_live_temperature
from arhiteq_worker.flow import (
    TOOL_EXTRACT,
    TOOL_FUNCTION,
    TOOL_KB_LOOKUP,
    TOOL_PRESS_DIGIT,
    TOOL_TRANSITION,
    FlowError,
    FlowGraph,
    classifier_prompt,
    flow_model_id,
    make_extract_node_tool,
    make_flow_kb_lookup_tool,
    make_function_node_tool,
    make_press_digit_node_tool,
    make_transition_tool,
    match_edge_id,
    node_instructions,
    node_tool_kinds,
    prompt_edges,
    start_speaker_for,
    transition_tool_schema,
)
from arhiteq_worker.flow_runtime import FlowRuntime
from arhiteq_worker.goodbye import looks_like_goodbye
from arhiteq_worker.internal_api import InternalAPI, InternalAPIError
from arhiteq_worker.state import CallState, now_ms
from arhiteq_worker.tools import DTMF_CODES, build_tools
from arhiteq_worker.variables import resolve_template
from arhiteq_worker.voices import resolve_cartesia_voice, resolve_gemini_voice

logger = logging.getLogger("arhiteq-worker")

# Strong refs to fire-and-forget tasks: the event loop only keeps a weak
# reference, so an un-held task can be GC'd before it runs (e.g. the inactivity
# hangup would silently never fire).
_background_tasks: set[asyncio.Task[Any]] = set()


def _spawn(coro: Any) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    task.add_done_callback(_log_task_exception)
    return task


def _cancel_background_tasks() -> None:
    """Cancel every task `_spawn` is still tracking.

    `entrypoint`'s `_finalize` only ever cancelled its own `tasks` list
    (the long-lived watchdogs/pumps registered via `_track`); a `_spawn`ed
    flow transition — `_FlowWiring._transition`'s node walk, or the initial
    `flow_wiring.start()` — was not in it, so an in-flight one could keep
    running after teardown and `session.say`/`generate_reply` into a session
    that no longer exists. `_background_tasks` already exists for exactly
    this purpose (`_spawn` populates it and a livekit-agents job runs one
    call per process, so there is nothing else in it to disturb); reusing it
    here is the smallest change that closes the gap, rather than adding a
    second tracking list alongside `tasks`.
    """
    for task in list(_background_tasks):
        task.cancel()


def _log_task_exception(task: asyncio.Task[Any]) -> None:
    """Surface a crashed background task instead of letting it die silently."""
    if not task.cancelled() and task.exception() is not None:
        logger.error("background task %s failed", task.get_name(), exc_info=task.exception())


@lru_cache(maxsize=1)
def _gcp_credentials() -> str:
    """Read the GCS service-account JSON once per process (it never changes)."""
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path and os.path.exists(creds_path):
        with open(creds_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


AGENT_NAME = "arhiteq-agent"
TRANSCRIPT_UPDATE_INTERVAL_S = 10.0
AMD_SPEECH_WINDOW_S = 5.0
DIAL_TIMEOUT_S = float(os.getenv("ARHITEQ_DIAL_TIMEOUT_S", "60"))
DEFAULT_GEMINI_MODEL = os.getenv("ARHITEQ_GEMINI_MODEL", "gemini-2.5-flash")
CARTESIA_TTS_MODEL = os.getenv("ARHITEQ_CARTESIA_TTS_MODEL", "sonic-2")
CARTESIA_STT_MODEL = os.getenv("ARHITEQ_CARTESIA_STT_MODEL", "ink-whisper")
# How much of the transcript a conversation flow's `branch` classifier sees.
# It routes on what was just said, so the tail is what matters; capping it
# keeps that one call cheap on a long call.
CLASSIFIER_TRANSCRIPT_CHARS = 4000

# Grace before tearing down the SIP room on a hangup that follows a spoken
# utterance (a goodbye or voicemail message). delete_room stops SIP egress
# immediately, so without this the tail still in flight to the callee — the
# room→SIP→phone jitter-buffered audio — gets cut mid-word. Parsed safely so a
# bad env value can't crash worker startup.
try:
    HANGUP_FLUSH_GRACE_S = max(0.0, float(os.getenv("ARHITEQ_HANGUP_FLUSH_GRACE_S", "1.0")))
except ValueError:
    HANGUP_FLUSH_GRACE_S = 1.0

# Live-only safety net: native-audio Gemini Live voices its goodbye but defers
# the end_call tool call to its next turn (which only fires on fresh user
# input), leaving seconds of dead air. When the agent voices a closing line and
# the user then stays silent this long, we hang up proactively instead of
# waiting for the model. Cancelled the instant the user speaks, so it never
# fires mid-conversation. <= 0 disables. Parsed safely (see above).
try:
    LIVE_GOODBYE_HANGUP_GRACE_S = max(
        0.0, float(os.getenv("ARHITEQ_LIVE_GOODBYE_HANGUP_GRACE_S", "1.5"))
    )
except ValueError:
    LIVE_GOODBYE_HANGUP_GRACE_S = 1.5


def _positive_int_env(name: str) -> int | None:
    """Parse a positive-int env var; blank / 0 / non-numeric → None (defaults).

    Tolerates a misconfigured value (e.g. a blank Helm key) instead of raising
    at import time and taking the whole worker down.
    """
    raw = (os.getenv(name) or "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


# Gemini Live (speech-to-speech) — a single realtime model that replaces the
# whole Cartesia-STT → Gemini → Cartesia-TTS pipeline. The default is the
# Vertex-served Live model; the plugin inherits the worker's Vertex env
# (GOOGLE_GENAI_USE_VERTEXAI / GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION).
DEFAULT_GEMINI_LIVE_MODEL = os.getenv(
    "ARHITEQ_GEMINI_LIVE_MODEL", "gemini-live-2.5-flash-native-audio"
)
# Live native-audio has limited regional availability and may not be served on
# the "global" endpoint the pipeline LLM uses. Set this (e.g. "us-central1") to
# pin the realtime model to a region without moving the rest of the worker.
GEMINI_LIVE_LOCATION = os.getenv("ARHITEQ_GEMINI_LIVE_LOCATION") or None
# Context-window compression thresholds (native-audio ctx is 128k tokens ≈ 15
# min of audio). Unset / 0 = leave the model/plugin defaults in place.
GEMINI_LIVE_TRIGGER_TOKENS = _positive_int_env("ARHITEQ_GEMINI_LIVE_TRIGGER_TOKENS")
GEMINI_LIVE_TARGET_TOKENS = _positive_int_env("ARHITEQ_GEMINI_LIVE_TARGET_TOKENS")
# Native audio runs at the model's default sampling temperature — the agent's
# model_temperature is a text-LLM knob and low values (Retell default 0)
# degenerate Live speech into droning/repeated syllables. Set 0..2 to pin one.
GEMINI_LIVE_TEMPERATURE = gemini_live_temperature(os.getenv("ARHITEQ_GEMINI_LIVE_TEMPERATURE"))
# Markers that identify a Live API (realtime) model id. Matched loosely so new
# Live model names Just Work: "gemini-live-2.5-flash-native-audio",
# "gemini-2.5-flash-native-audio-preview-…", "gemini-3.1-flash-live-preview".
# Note "flash-lite" does NOT match "flash-live", so the lite pipeline models
# stay on the Cartesia path.
_LIVE_MODEL_MARKERS = ("native-audio", "-live-", "flash-live", "live-2.5")

_SIP_ANSWERED_STATUSES = {"active", "automation"}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _cartesia_speed(voice_speed: float) -> float:
    # Retell 0.5..2.0 (1.0 neutral) → Cartesia -1.0..1.0 (0.0 neutral).
    return _clamp(voice_speed - 1.0, -1.0, 1.0)


def _stt_language(language: str) -> str:
    # Retell "en-US" → Cartesia STT "en".
    return (language or "en").split("-")[0].lower()


def _gemini_model(model: str) -> str:
    # Non-Gemini engine names (Retell allows gpt-*, claude-*) fall back to
    # the deployment default — Arhiteq runs conversation on Gemini only.
    if model and "gemini" in model.lower():
        return model
    return DEFAULT_GEMINI_MODEL


def _is_live_model(model: str) -> bool:
    """True when *model* is a Gemini Live (speech-to-speech) model id."""
    m = (model or "").lower()
    return any(marker in m for marker in _LIVE_MODEL_MARKERS)


def _amd_enabled(cfg: CallConfig) -> bool:
    """Whether the answering-machine greeting classifier runs for this call."""
    return cfg.direction == "outbound" and cfg.agent.enable_voicemail_detection


def _min_interruption_duration(sensitivity: float) -> float:
    s = _clamp(sensitivity, 0.0, 1.0)
    return 0.1 + (1.0 - s) * 1.4


def _endpointing_delays(responsiveness: float) -> tuple[float, float]:
    r = _clamp(responsiveness, 0.0, 1.0)
    return 0.2 + (1.0 - r) * 1.0, 3.0 + (1.0 - r) * 3.0


def _assemble_session(
    cfg: CallConfig, base_kwargs: dict[str, Any], *, realtime: bool = False
) -> AgentSession:
    """Attach interruption/endpointing tuning and build the AgentSession.

    Optional kwargs are filtered against the installed AgentSession signature
    so the worker runs on livekit-agents 1.2 (flat kwargs) and ≥1.3
    (TurnHandlingOptions) alike. When *realtime* is set, endpointing/interruption
    timing is skipped: the Gemini Live model does its own server-side VAD and
    turn detection, so those Cartesia-STT-tuned values don't apply.
    """
    kwargs: dict[str, Any] = dict(base_kwargs)
    params = inspect.signature(AgentSession.__init__).parameters
    optional: dict[str, Any] = {
        "allow_interruptions": cfg.agent.interruption_sensitivity > 0,
        # Closest option to end_call_after_silence_ms: the user is marked
        # "away" after this much silence and the worker hangs up (reason
        # "inactivity") from the user_state_changed handler. Session-level
        # timer, so it applies to both pipeline and realtime sessions.
        "user_away_timeout": cfg.agent.end_call_after_silence_ms / 1000.0,
    }
    if not realtime:
        min_delay, max_delay = _endpointing_delays(cfg.agent.responsiveness)
        optional["min_interruption_duration"] = _min_interruption_duration(
            cfg.agent.interruption_sensitivity
        )
        if "turn_handling" in params:
            from livekit.agents import TurnHandlingOptions  # livekit-agents >= 1.3

            kwargs["turn_handling"] = TurnHandlingOptions(
                endpointing={"min_delay": min_delay, "max_delay": max_delay},
            )
        else:
            optional["min_endpointing_delay"] = min_delay
            optional["max_endpointing_delay"] = max_delay
    for name, value in optional.items():
        if name in params:
            kwargs[name] = value
    return AgentSession(**kwargs)


def _build_realtime_model(google_plugin: Any, cfg: CallConfig) -> Any:
    """Build a Gemini Live realtime model for a speech-to-speech session.

    Enables input+output transcription (the Retell contract's post-call
    analysis and webhooks read the transcript), session resumption (seamless
    reconnect on the ~10-min GoAway), and context-window compression (the 128k
    native-audio window ≈ 15 min of audio would otherwise overflow and drop
    the call). Auth and region are inherited from the worker's Vertex env
    unless ARHITEQ_GEMINI_LIVE_LOCATION pins a region.
    """
    from google.genai import types as genai_types

    sliding = (
        genai_types.SlidingWindow(target_tokens=GEMINI_LIVE_TARGET_TOKENS)
        if GEMINI_LIVE_TARGET_TOKENS
        else genai_types.SlidingWindow()
    )
    compression_kwargs: dict[str, Any] = {"sliding_window": sliding}
    if GEMINI_LIVE_TRIGGER_TOKENS:
        compression_kwargs["trigger_tokens"] = GEMINI_LIVE_TRIGGER_TOKENS

    opts: dict[str, Any] = {
        # The wire model id just selects "Gemini Live"; the exact model sent to
        # Vertex is deployment config (ARHITEQ_GEMINI_LIVE_MODEL), mirroring how
        # _gemini_model maps the pipeline model. This keeps a Gemini-API-only
        # live id (e.g. gemini-3.1-flash-live-preview) from reaching Vertex,
        # where the plugin would reject it.
        "model": DEFAULT_GEMINI_LIVE_MODEL,
        "voice": resolve_gemini_voice(cfg.agent.voice_id),
        "input_audio_transcription": genai_types.AudioTranscriptionConfig(),
        "output_audio_transcription": genai_types.AudioTranscriptionConfig(),
        "session_resumption": genai_types.SessionResumptionConfig(),
        "context_window_compression": genai_types.ContextWindowCompressionConfig(
            **compression_kwargs
        ),
    }
    if GEMINI_LIVE_TEMPERATURE is not None:
        # cfg.llm.model_temperature intentionally does not reach Live sessions
        # (see gemini_live_temperature) — only the explicit env override does.
        opts["temperature"] = GEMINI_LIVE_TEMPERATURE
    if GEMINI_LIVE_LOCATION:
        # A region override implies Vertex — the Live models we ship are Vertex.
        opts["vertexai"] = True
        opts["location"] = GEMINI_LIVE_LOCATION
    return google_plugin.realtime.RealtimeModel(**opts)


def build_session(
    cfg: CallConfig, *, model: str | None = None, temperature: float | None = None
) -> tuple[AgentSession, Any]:
    """Build the call's AgentSession; return (session, amd_llm).

    *model* / *temperature* override the agent's own LLM settings. A
    flow-backed agent has no Retell LLM at all (``llm`` comes back null from
    the control plane) — its engine choice lives on the flow's ``model_choice``
    / ``model_temperature`` — so the flow branch passes them here. Both default
    to ``None``, i.e. the agent's own values, leaving the single-prompt path
    exactly as it was.

    Two shapes, chosen by the configured model:
    - Gemini Live model → a single google.realtime.RealtimeModel
      (speech-to-speech); the Cartesia STT/TTS pipeline is bypassed and the
      Gemini native-audio voice comes from the agent's voice_id.
    - anything else → the Cartesia-STT → Gemini → Cartesia-TTS pipeline.

    The second return value is the LLM the AMD greeting classifier uses (it
    needs a *text* google.LLM — RealtimeModel has no .chat()). In Live mode a
    small dedicated text LLM is built only when AMD will actually run; otherwise
    the realtime model is returned as an unused placeholder.
    """
    from livekit.plugins import cartesia, google

    engine = model or cfg.llm.model
    if _is_live_model(engine):
        realtime = _build_realtime_model(google, cfg)
        session = _assemble_session(cfg, {"llm": realtime}, realtime=True)
        amd_llm = (
            google.LLM(model=DEFAULT_GEMINI_MODEL, temperature=0.0)
            if _amd_enabled(cfg)
            else realtime  # never consumed: _run_amd only runs when _amd_enabled
        )
        return session, amd_llm

    llm = google.LLM(  # GOOGLE_API_KEY read from env by the plugin
        model=_gemini_model(engine),
        temperature=cfg.llm.model_temperature if temperature is None else temperature,
    )
    stt = cartesia.STT(  # CARTESIA_API_KEY read from env by the plugin
        model=CARTESIA_STT_MODEL,
        language=_stt_language(cfg.agent.language),
    )
    tts = cartesia.TTS(
        model=CARTESIA_TTS_MODEL,
        voice=resolve_cartesia_voice(cfg.agent.voice_id),
        speed=_cartesia_speed(cfg.agent.voice_speed),
    )
    session = _assemble_session(cfg, {"stt": stt, "llm": llm, "tts": tts})
    return session, llm


class ArhiteqAgent(Agent):
    def __init__(
        self,
        *,
        instructions: str,
        tools: list[Any],
        begin_message: str | None,
        start_speaker: str,
        on_user_turn: Callable[[str | None], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(instructions=instructions, tools=tools)
        self._begin_message = begin_message
        self._start_speaker = start_speaker
        self._on_user_turn = on_user_turn

    async def on_user_turn_completed(self, turn_ctx: Any, new_message: Any) -> None:
        """Flow-backed calls: walk the graph BEFORE the reply to this turn is built.

        This hook is the only seam livekit awaits between committing the
        caller's turn and creating the speech handle that answers it
        (``AgentActivity._user_turn_completed_task``). The graph walk has to
        land here, not on ``conversation_item_added``: that event fires from
        inside the reply task, by which point the reply has already been built
        from the OLD node's instructions and tool set, so an ``always_edge`` /
        equation transition took effect a full turn late and raced the reply
        it was supposed to precede.

        `_wire_session_events` still walks on ``conversation_item_added`` as
        well, because livekit skips this hook entirely for a realtime model
        with server-side turn detection (Gemini Live) — see the id-based
        de-duplication in `_FlowWiring`, which is what stops the two seams
        from walking the same turn twice on the pipeline path.

        Never raises: livekit treats an exception from this hook as "skip the
        reply to this turn", which would cost the caller an answer over a bug
        in the graph walk.
        """
        if self._on_user_turn is None:
            return
        try:
            await self._on_user_turn(getattr(new_message, "id", None))
        except Exception:
            logger.exception("flow: on_user_turn_completed walk failed")

    async def on_enter(self) -> None:
        if self._start_speaker != "agent":
            return  # start_speaker == "user": wait for the callee to talk
        if not self._begin_message:
            self.session.generate_reply()
        elif self.session.tts is None:
            # A speech-to-speech session (Gemini Live) has no TTS, so say()
            # raises ("...without a TTS model or a RealtimeSession that supports
            # say()"). Have the realtime model open the call by voicing the
            # greeting verbatim instead.
            self.session.generate_reply(
                instructions=(
                    "Start the call by saying this greeting to the user, word "
                    f'for word and nothing else first: "{self._begin_message}"'
                )
            )
        else:
            await self.session.say(self._begin_message)


class CallRuntime:
    """Call-control surface handed to built-in tools (tools.CallControl)."""

    def __init__(self, ctx: JobContext, lkapi: api.LiveKitAPI, state: CallState) -> None:
        self._ctx = ctx
        self._lkapi = lkapi
        self._state = state
        self.sip_participant_identity: str | None = None
        # Installed after the session starts (needs the live agent/session).
        self._agent_swap: Callable[[str, Mapping[str, Any]], Awaitable[str]] | None = None

    def set_agent_swap(self, handler: Callable[[str, Mapping[str, Any]], Awaitable[str]]) -> None:
        self._agent_swap = handler

    async def press_digit(self, digits: str) -> None:
        # DTMF rides the SIP leg; 0.3s between events mirrors telephony pacing.
        lp = self._ctx.room.local_participant
        for i, digit in enumerate(digits):
            if i:
                await asyncio.sleep(0.3)
            await lp.publish_dtmf(code=DTMF_CODES[digit.upper()], digit=digit)

    async def agent_swap(self, agent_id: str, entry: Mapping[str, Any]) -> str:
        if self._agent_swap is None:
            return json.dumps({"error": "agent swap not available on this call"})
        return await self._agent_swap(agent_id, entry)

    async def end_call(self, reason: str = "agent_hangup", *, flush_grace: bool = False) -> None:
        self._state.set_reason_once(reason)
        self._state.ended_at_ms = self._state.ended_at_ms or now_ms()
        if flush_grace and HANGUP_FLUSH_GRACE_S > 0:
            # The agent just spoke (goodbye / voicemail); let that tail reach the
            # callee before delete_room cuts SIP egress. wait_for_playout only
            # covers worker→room, not the room→SIP→phone tail still in flight.
            await asyncio.sleep(HANGUP_FLUSH_GRACE_S)
        try:
            await self._lkapi.room.delete_room(api.DeleteRoomRequest(room=self._ctx.room.name))
        except Exception as exc:  # noqa: BLE001 - room may already be gone
            logger.warning("delete_room failed: %s", exc)

    async def transfer_call(self, number: str) -> str:
        # Cold transfer via SIP REFER (LiveKit SIP TransferSIPParticipant).
        if not self.sip_participant_identity:
            logger.warning("transfer requested but no SIP participant present")
            return json.dumps({"error": "transfer not supported on this call"})
        await self._lkapi.sip.transfer_sip_participant(
            api.TransferSIPParticipantRequest(
                room_name=self._ctx.room.name,
                participant_identity=self.sip_participant_identity,
                transfer_to=f"tel:{number}",
                play_dialtone=False,
            )
        )
        self._state.set_reason_once("call_transfer")
        self._state.ended_at_ms = self._state.ended_at_ms or now_ms()
        return json.dumps({"result": f"call transferred to {number}"})


async def _one_shot_completion(llm: Any, system: str, user: str) -> str:
    """One completion against *llm*, collected in full. Same shape as `amd`'s.

    livekit-agents' LLM surface is a stream; consuming it to exhaustion once is
    the non-streaming completion a ``branch`` node's classifier needs. Never
    raises: a classifier that cannot answer must fall through to the node's
    fallback edge, not take the call down.
    """
    try:
        from livekit.agents.llm import ChatContext

        chat_ctx = ChatContext()
        chat_ctx.add_message(role="system", content=system)
        chat_ctx.add_message(role="user", content=user or "(no conversation yet)")
        answer = ""
        async with llm.chat(chat_ctx=chat_ctx) as stream:
            async for chunk in stream:
                delta = getattr(chunk, "delta", None)
                if delta is not None and getattr(delta, "content", None):
                    answer += delta.content
        return answer
    except Exception as exc:  # noqa: BLE001 - routing must survive a bad call
        logger.warning("flow classification call failed: %s", exc)
        return ""


class _FlowWiring:
    """The livekit-side seams one flow-backed call's `FlowRuntime` needs.

    `FlowRuntime` and `flow`'s decision layer import no livekit on purpose;
    every side effect reaches them as an injected callable, and this class is
    where those callables are actually made of a live `AgentSession`, `Agent`
    and `CallRuntime`.

    Built *before* the session exists, because its `start_instructions` /
    `start_tools` are what `ArhiteqAgent` is constructed with — a flow-backed
    agent opens on its start node, not on ``llm.general_prompt``. `attach` then
    binds the live session and agent once `session.start` has produced them,
    and `start` enters the start node.

    Two wiring decisions are load-bearing and easy to get wrong:

    1. **Every tool-driven entry into the runtime is spawned** (`_transition`),
       never awaited inside the tool handler. livekit runs a function tool
       inside the speech handle that called it, and a *new* speech handle
       cannot start until that one finishes — so awaiting speech (which
       `say`/`generate_reply` below do) from inside a handler would deadlock
       until the interruption timeout. Spawning moves the whole node walk out
       of the handler, where awaiting is safe. `tools._make_end_call_tool`
       solves the same problem the other way, with `context.wait_for_playout`,
       which only ever waits on the handler's *own* speech.
    2. **`say`/`generate_reply` wait for playout.** That is what lets an
       ``end`` or ``transfer_call`` node finish its closing line before the
       room is deleted or the leg is handed off; without it the last words are
       cut. It is only safe because of (1).

    A `flow_lock` serializes the three entry points (`start`, `on_user_turn`,
    an advance) so a caller speaking mid-transition cannot interleave two node
    walks over the runtime's single "where the call is now" cursor.
    """

    def __init__(
        self,
        *,
        cfg: CallConfig,
        flow: ConversationFlowConfig,
        control: CallRuntime,
        state: CallState,
        variables: dict[str, Any],
        http: httpx.AsyncClient,
        knowledge: InternalAPI,
    ) -> None:
        # Raises FlowError on an unsupported node type or an edge to a missing
        # node, naming the node — the caller aborts the call on it, before the
        # greeting, rather than letting it become a dead end mid-call.
        self._graph = FlowGraph.from_config(flow)
        self._cfg = cfg
        self._flow = flow
        self._control = control
        self._state = state
        self._variables = variables
        self._http = http
        self._knowledge = knowledge
        self._session: AgentSession | None = None
        self._runtime: FlowRuntime | None = None
        self._classifier_llm: Any = None
        self._lock = asyncio.Lock()
        # Last user ChatMessage id `on_user_turn` walked, so `on_user_item`
        # can tell "livekit already gave us the pre-reply seam for this turn"
        # from "this is a realtime call that never gets one".
        self._walked_message_id: str | None = None

    # -- what the agent starts with ------------------------------------------

    @property
    def model(self) -> str:
        """The flow's raw ``model_choice`` id, for `build_session` to map."""
        return flow_model_id(self._flow.model_choice)

    @property
    def temperature(self) -> float | None:
        return self._flow.model_temperature

    @property
    def start_instructions(self) -> str:
        return node_instructions(self._graph.start, self._graph, self._variables)

    def start_tools(self) -> list[Any]:
        start = self._graph.start
        return self.build_node_tools(start, prompt_edges(start, self._graph.global_nodes))

    def attach(self, session: AgentSession, agent: Agent) -> FlowRuntime:
        """Bind the live session/agent and build the runtime that drives them."""
        self._session = session
        self._runtime = FlowRuntime(
            self._graph,
            # The start node may override who opens the call; FlowRuntime reads
            # that decision off the config it is handed.
            replace(
                self._flow,
                start_speaker=start_speaker_for(self._graph.start, self._flow.start_speaker),
            ),
            self._variables,
            set_instructions=agent.update_instructions,
            set_tools=agent.update_tools,
            say=self.say,
            classify=self.classify,
            build_node_tools=self.build_node_tools,
            end_call=self.end_call,
            transfer_call=self._control.transfer_call,
            generate_reply=self.generate_reply,
            call_id=self._cfg.call_id,
        )
        return self._runtime

    # -- entry points --------------------------------------------------------

    async def start(self) -> None:
        if self._runtime is None:
            return
        async with self._lock:
            await self._runtime.start()

    async def on_user_turn(self, message_id: str | None = None) -> None:
        """A user turn was committed: re-evaluate the node it was committed on.

        Called from `ArhiteqAgent.on_user_turn_completed`, the seam livekit
        awaits *before* building the reply — so the destination node's
        instructions and tools are what that reply is generated from. See
        that hook for why the ordering matters.

        The cursor is captured HERE, before the lock — the same guard, and for
        the same reason, as `_transition` captures it before spawning an
        advance. A caller who produces two turns while the agent is mid-`say`
        (holding the lock) queues two walks: unguarded, the first takes N's
        ``always_edge`` to M and the second takes M's straight away, and M is
        entered and left without ever speaking or listening. Both capture N
        here, so the second is recognised as stale and dropped.
        """
        self._walked_message_id = message_id
        if self._runtime is None:
            return
        from_node_id = self._runtime.current_node_id
        async with self._lock:
            await self._runtime.on_user_turn(from_node_id=from_node_id)

    async def on_user_item(self, message_id: str | None = None) -> None:
        """`conversation_item_added`'s fallback walk, for turns `on_user_turn` never sees.

        ``AgentActivity._user_turn_completed_task`` returns early — before
        calling `ArhiteqAgent.on_user_turn_completed` at all — when the LLM is
        a realtime model doing its own server-side turn detection, which is
        Gemini Live. Those calls get no pre-reply seam, so the committed-item
        event stays wired up as the fallback: one turn late, but present
        rather than absent.

        De-duplicated by message id, not by a "did the other seam run" flag:
        the id livekit hands this event is the very ChatMessage
        `on_user_turn_completed` was given, so matching it is exact and
        order-independent. A turn whose reply is skipped (paused scheduling)
        never reaches this event at all, and a stale id simply fails to match
        the next one rather than silently swallowing a later walk.
        """
        if message_id is not None and message_id == self._walked_message_id:
            return
        await self.on_user_turn(message_id)

    async def _advance(self, edge: dict[str, Any], *, from_node_id: str | None = None) -> None:
        if self._runtime is None:
            return
        async with self._lock:
            await self._runtime.advance(edge, from_node_id=from_node_id)

    async def _transition(self, edge: dict[str, Any]) -> None:
        """A node tool chose *edge*: walk it, but not on the handler's stack.

        See the class docstring, decision (1) — this is the seam that keeps the
        node walk (and its awaited speech) out of the function-tool handler.

        The node the runtime is on *right now* is captured before spawning,
        not inside the spawned coroutine: two parallel `transition_to` calls
        from the same node each capture the same id here, and only the one
        that reaches `FlowRuntime.advance` while the cursor still matches it
        is honoured — the other is a stale edge and is dropped there instead
        of walking from wherever the first one already moved the call to.
        """
        from_node_id = self._runtime.current_node_id if self._runtime is not None else None
        _spawn(self._advance(edge, from_node_id=from_node_id))

    def _transition_by_id(self, edges: list[dict[str, Any]]) -> Callable[[str], Awaitable[None]]:
        """`make_transition_tool`'s callback: resolve the model's edge id, then walk it.

        The model can only name an id (that is all the tool's enum offers), so
        resolving it back to the edge dict `FlowRuntime.advance` takes is the
        caller's job — done against the very list the tool was built from, so a
        synthetic ``global::`` edge resolves like an authored one.
        """

        async def on_transition(edge_id: str) -> None:
            edge = next((e for e in edges if e.get("id") == edge_id), None)
            if edge is None:
                # Raise, don't swallow: make_transition_tool turns this into
                # "failed to transition" so the model can pick a real id.
                raise ValueError(f"unknown transition {edge_id!r}")
            await self._transition(edge)

        return on_transition

    # -- injected callables --------------------------------------------------

    def _build_builtin_tool(self, entry: dict[str, Any]) -> Any | None:
        """Build one built-in tool from its ENTRY, for a node that references it.

        Delegates to the same `build_tools` dispatch a single-prompt agent
        uses, one entry at a time, so a flow's built-in behaves identically to
        the agent-level one — including its `CallControl` side effects (DTMF,
        hang-up, transfer) and its `state` bookkeeping. `flow.py` then wraps
        the result with the node's routing (`make_routed_builtin_tool`);
        composing this way is what keeps eight built-ins from needing a
        flow-aware second implementation.
        """
        built = build_tools(
            [entry],
            http=self._http,
            function_secret=self._cfg.function_secret,
            variables=self._variables,
            control=self._control,
            state=self._state,
            call_info=self._cfg.tool_call_object(),
            knowledge=self._knowledge,
            call_id=self._cfg.call_id,
            knowledge_base_ids=self._flow.knowledge_base_ids,
        )
        # `build_tools` skips an unsupported/misconfigured entry with a warning
        # rather than raising, so an empty list is the expected "no tool" case.
        return built[0] if built else None

    def build_node_tools(self, node: dict[str, Any], edges: list[dict[str, Any]]) -> list[Any]:
        """Assemble *node*'s livekit tools. Which ones is `node_tool_kinds`' call."""
        tools: list[Any] = []
        for kind in node_tool_kinds(node, edges, knowledge_base_ids=self._flow.knowledge_base_ids):
            if kind == TOOL_TRANSITION:
                schema = transition_tool_schema(node, edges)
                if schema is not None:  # node_tool_kinds already checked
                    tools.append(
                        make_transition_tool(
                            schema, self._transition_by_id(edges), state=self._state
                        )
                    )
            elif kind == TOOL_FUNCTION:
                tool = make_function_node_tool(
                    node,
                    self._flow.tools,
                    http=self._http,
                    function_secret=self._cfg.function_secret,
                    variables=self._variables,
                    call_info=self._cfg.tool_call_object(),
                    state=self._state,
                    on_transition=self._transition,
                    build_builtin=self._build_builtin_tool,
                )
                if tool is not None:  # unresolved tool_id: skipped with a warning
                    tools.append(tool)
            elif kind == TOOL_PRESS_DIGIT:
                tool = make_press_digit_node_tool(
                    node,
                    build_builtin=self._build_builtin_tool,
                    variables=self._variables,
                    on_transition=self._transition,
                )
                if tool is not None:
                    tools.append(tool)
            elif kind == TOOL_EXTRACT:
                tools.append(
                    make_extract_node_tool(
                        node,
                        variables=self._variables,
                        state=self._state,
                        on_transition=self._transition,
                    )
                )
            elif kind == TOOL_KB_LOOKUP:
                tools.append(
                    make_flow_kb_lookup_tool(
                        self._flow.kb_config,
                        knowledge=self._knowledge,
                        call_id=self._cfg.call_id,
                        knowledge_base_ids=self._flow.knowledge_base_ids,
                        variables=self._variables,
                        state=self._state,
                    )
                )
        return tools

    async def say(self, text: str) -> None:
        session = self._session
        if session is None:
            return
        if getattr(session, "tts", None) is None:
            # Gemini Live: no TTS, so say() raises. Have the realtime model
            # voice the line verbatim instead (same fallback ArhiteqAgent's
            # begin_message takes).
            await session.generate_reply(
                instructions=f'Say this to the user, word for word and nothing else: "{text}"'
            )
            return
        await session.say(text)

    async def generate_reply(self, instructions: str | None) -> None:
        session = self._session
        if session is None:
            return
        if instructions:
            await session.generate_reply(instructions=instructions)
        else:
            await session.generate_reply()

    async def classify(self, instructions: str, edges: list[dict[str, Any]]) -> str | None:
        """The one extra LLM call in the design: route a ``branch`` node.

        Runs against the flow's own model, mapped onto the Gemini catalogue
        exactly like the session's — a flow naming ``gpt-5.1`` never reaches a
        provider. Temperature 0: this is a classification, not a phrasing.
        """
        if self._classifier_llm is None:
            from livekit.plugins import google

            self._classifier_llm = google.LLM(model=_gemini_model(self.model), temperature=0.0)
        answer = await _one_shot_completion(
            self._classifier_llm,
            classifier_prompt(instructions, edges),
            self._state.transcript_text()[-CLASSIFIER_TRANSCRIPT_CHARS:],
        )
        return match_edge_id(answer, edges)

    async def end_call(self, reason: str) -> None:
        # The closing line has already played (`say` waits for playout);
        # flush_grace then covers the room->SIP->phone tail, the same pairing
        # `tools._make_end_call_tool` uses.
        await self._control.end_call(reason, flush_grace=True)


def _is_sip_participant(p: rtc.RemoteParticipant) -> bool:
    if getattr(p, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        return True
    return any(key.startswith("sip.") for key in p.attributes)


def _is_web_participant(p: rtc.RemoteParticipant) -> bool:
    """Browser callers join as STANDARD participants (agents/egress excluded)."""
    return getattr(p, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD


async def _wait_for_participant(
    ctx: JobContext,
    predicate: Callable[[rtc.RemoteParticipant], bool],
    kind: rtc.ParticipantKind.ValueType,
    timeout: float,
) -> rtc.RemoteParticipant:
    for p in ctx.room.remote_participants.values():
        if predicate(p):
            return p
    return await asyncio.wait_for(ctx.wait_for_participant(kind=kind), timeout)


async def _wait_for_sip_participant(ctx: JobContext, timeout: float) -> rtc.RemoteParticipant:
    return await _wait_for_participant(
        ctx, _is_sip_participant, rtc.ParticipantKind.PARTICIPANT_KIND_SIP, timeout
    )


async def _wait_for_web_participant(ctx: JobContext, timeout: float) -> rtc.RemoteParticipant:
    return await _wait_for_participant(
        ctx, _is_web_participant, rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD, timeout
    )


async def _wait_for_answer(
    ctx: JobContext, participant: rtc.RemoteParticipant, timeout: float
) -> bool:
    """Wait until the SIP call is answered (sip.callStatus in active/automation).

    Non-SIP participants (e.g. web test calls) count as answered immediately.
    Returns False on no-answer/hangup/timeout.
    """
    status = participant.attributes.get("sip.callStatus")
    if status is None or status in _SIP_ANSWERED_STATUSES:
        return True

    done = asyncio.Event()
    answered = False

    def _on_attrs(changed: dict[str, str], p: rtc.Participant) -> None:
        nonlocal answered
        if p.identity != participant.identity:
            return
        st = p.attributes.get("sip.callStatus")
        if st in _SIP_ANSWERED_STATUSES:
            answered = True
            done.set()
        elif st == "hangup":
            done.set()

    def _on_disconnected(p: rtc.RemoteParticipant, *_: Any) -> None:
        if p.identity == participant.identity:
            done.set()

    ctx.room.on("participant_attributes_changed", _on_attrs)
    ctx.room.on("participant_disconnected", _on_disconnected)
    try:
        if participant.attributes.get("sip.callStatus") in _SIP_ANSWERED_STATUSES:
            return True
        try:
            await asyncio.wait_for(done.wait(), timeout)
        except TimeoutError:
            return False
        return answered
    finally:
        ctx.room.off("participant_attributes_changed", _on_attrs)
        ctx.room.off("participant_disconnected", _on_disconnected)


async def _load_call_config(
    ctx: JobContext, api_client: InternalAPI, state: CallState
) -> tuple[dict[str, Any], rtc.RemoteParticipant | None]:
    """Outbound: call_id in job/room metadata → GET config.
    Inbound: from/to numbers from SIP participant attributes → POST resolve.
    """
    meta: dict[str, Any] = {}
    for raw in (ctx.job.metadata, getattr(ctx.room, "metadata", None)):
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    meta.update(parsed)
            except TypeError, ValueError:
                pass
    call_id = meta.get("call_id")
    if call_id:
        # Set call_id before the GET so a failed config fetch still finalizes
        # the call (posts an error status) instead of leaving it non-terminal.
        state.call_id = call_id
        return await api_client.get_call_config(call_id), None

    participant = await _wait_for_sip_participant(ctx, timeout=30.0)
    from_number = participant.attributes.get("sip.phoneNumber", "")
    to_number = participant.attributes.get("sip.trunkPhoneNumber", "")
    cfg = await api_client.resolve_inbound(from_number, to_number, ctx.room.name)
    return cfg, participant


async def _start_recording(
    lkapi: api.LiveKitAPI, room_name: str, call_id: str, state: CallState
) -> None:
    bucket = os.getenv("RECORDINGS_GCS_BUCKET")
    if not bucket:
        return
    credentials = _gcp_credentials()
    filepath = f"calls/{call_id}.ogg"
    try:
        await lkapi.egress.start_room_composite_egress(
            api.RoomCompositeEgressRequest(
                room_name=room_name,
                audio_only=True,
                file_outputs=[
                    api.EncodedFileOutput(
                        file_type=api.EncodedFileType.OGG,
                        filepath=filepath,
                        gcp=api.GCPUpload(credentials=credentials, bucket=bucket),
                    )
                ],
            )
        )
        # The control plane converts this to a signed URL before exposing it
        # (ARCHITECTURE.md: recording_url is a signed URL).
        state.recording_url = f"https://storage.googleapis.com/{bucket}/{filepath}"
    except Exception as exc:  # noqa: BLE001 - recording is best-effort
        logger.warning("failed to start egress for %s: %s", call_id, exc)
        state.recording_url = None


def _wire_session_events(
    session: AgentSession,
    state: CallState,
    runtime: CallRuntime,
    amd_speech: list[str],
    amd_window_open: dict[str, bool],
    on_user_turn: Callable[[str | None], Awaitable[None]] | None = None,
) -> None:
    last_llm_ttft: dict[str, float] = {}

    # Live-only goodbye safety net (see LIVE_GOODBYE_HANGUP_GRACE_S). Gemini Live
    # bakes the voice into the realtime model and has no separate TTS; the
    # pipeline always sets one. A missing TTS therefore marks the native-audio
    # session whose deferred end_call this guards against.
    _live_goodbye = getattr(session, "tts", None) is None and LIVE_GOODBYE_HANGUP_GRACE_S > 0
    _last_agent_text: dict[str, str] = {"text": ""}
    _goodbye_timer: dict[str, asyncio.Task[Any] | None] = {"task": None}

    def _cancel_goodbye_hangup() -> None:
        # Also consume the last agent line: a stale goodbye must not re-arm on a
        # later, non-speaking return to "listening" (e.g. after a silent tool
        # call) and hang up on a caller who has since re-engaged.
        _last_agent_text["text"] = ""
        task = _goodbye_timer["task"]
        _goodbye_timer["task"] = None
        if task is not None and not task.done():
            task.cancel()

    async def _goodbye_hangup() -> None:
        # Armed after the agent voiced a closing line; the user has now stayed
        # silent for the whole grace, so end the call the model was slow to end
        # itself. flush_grace keeps the goodbye's SIP tail from clipping.
        await asyncio.sleep(LIVE_GOODBYE_HANGUP_GRACE_S)
        _goodbye_timer["task"] = None
        logger.info("live goodbye safety net: hanging up %s", state.call_id)
        await runtime.end_call("agent_hangup", flush_grace=True)

    def _arm_goodbye_hangup() -> None:
        if not _live_goodbye or not looks_like_goodbye(_last_agent_text["text"]):
            return
        _cancel_goodbye_hangup()
        _goodbye_timer["task"] = _spawn(_goodbye_hangup())

    @session.on("conversation_item_added")
    def _on_item(ev: Any) -> None:
        item = ev.item
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None) or ""
        if role == "assistant":
            state.add_message("agent", text)
            _last_agent_text["text"] = text
        elif role == "user":
            state.add_message("user", text)
            if on_user_turn is not None:
                # Flow-backed calls only, and only as the FALLBACK seam: this
                # event fires from inside the reply task, so the walk it drives
                # lands a turn late. `ArhiteqAgent.on_user_turn_completed` is
                # the pre-reply seam and normally walks first; the id passed
                # here is what lets `_FlowWiring.on_user_item` recognise that
                # and skip. It only does the walk itself on a realtime call
                # with server-side turn detection, which livekit never gives
                # that hook. None on the single-prompt path — no graph to walk.
                _spawn(on_user_turn(getattr(item, "id", None)))

    @session.on("user_input_transcribed")
    def _on_transcribed(ev: Any) -> None:
        # The user is talking — never hang up on them.
        _cancel_goodbye_hangup()
        if amd_window_open.get("open") and getattr(ev, "is_final", False):
            amd_speech.append(getattr(ev, "transcript", "") or "")

    @session.on("agent_state_changed")
    def _on_agent_state(ev: Any) -> None:
        new_state = getattr(ev, "new_state", None)
        if new_state in ("speaking", "thinking"):
            # A fresh agent turn — the previous line wasn't the last word.
            _cancel_goodbye_hangup()
        elif new_state == "listening":
            # Agent finished a turn; if it was a sign-off, start the countdown.
            _arm_goodbye_hangup()

    @session.on("user_state_changed")
    def _on_user_state(ev: Any) -> None:
        new_state = getattr(ev, "new_state", None)
        if new_state == "speaking":
            _cancel_goodbye_hangup()  # user re-engaged
        elif new_state == "away":
            # user_away_timeout fired (maps end_call_after_silence_ms).
            state.set_reason_once("inactivity")
            _spawn(runtime.end_call("inactivity"))

    @session.on("close")
    def _on_close(ev: Any) -> None:
        # Reason attribution only — teardown is the framework's job:
        # delete_room_on_close (session.start) deletes the room on ANY close,
        # which ends the job and drives the finalize shutdown callback.
        # CloseReason is a str enum, so compare members, never str(ev.reason)
        # (that yields "CloseReason.PARTICIPANT_DISCONNECTED" and matches
        # nothing).
        _cancel_goodbye_hangup()
        state.ended_at_ms = state.ended_at_ms or now_ms()
        reason = getattr(ev, "reason", None)
        if reason == CloseReason.PARTICIPANT_DISCONNECTED:
            state.set_reason_once("user_hangup")
        elif reason == CloseReason.ERROR:
            state.set_reason_once("error_unknown")

    # TODO: metrics_collected is deprecated in livekit-agents ≥1.3 in favor
    # of session_usage_updated / ChatMessage.metrics; it still fires today
    # and carries the ttft/ttfb we need.
    @session.on("metrics_collected")
    def _on_metrics(ev: Any) -> None:
        m = getattr(ev, "metrics", ev)
        ttft = getattr(m, "ttft", None)
        ttfb = getattr(m, "ttfb", None)
        if ttft is not None and ttft >= 0:
            metrics.LLM_TTFB_SECONDS.observe(ttft)
            last_llm_ttft["value"] = ttft
        if ttfb is not None and ttfb >= 0:
            metrics.TTS_TTFB_SECONDS.observe(ttfb)
            e2e_ms = (last_llm_ttft.get("value", 0.0) + ttfb) * 1000.0
            state.e2e_latency_ms.append(e2e_ms)


async def _run_amd(
    cfg: CallConfig,
    state: CallState,
    runtime: CallRuntime,
    session: AgentSession,
    llm: Any,
    participant: rtc.RemoteParticipant | None,
    amd_speech: list[str],
    amd_window_open: dict[str, bool],
    variables: dict[str, Any],
) -> None:
    result: str | None = None
    if participant is not None:
        result = amd.read_sip_amd_result(participant.attributes)
    if result is None:
        # Heuristic: classify the first ~5s of callee speech with Gemini.
        await asyncio.sleep(AMD_SPEECH_WINDOW_S)
        amd_window_open["open"] = False
        greeting = " ".join(s for s in amd_speech if s).strip()
        if not greeting:
            return
        is_vm = await amd.classify_greeting_is_voicemail(llm, greeting)
        result = "machine" if is_vm else "human"
    else:
        amd_window_open["open"] = False
    metrics.AMD_DETECTIONS_TOTAL.labels(result=result).inc()
    if result != "machine":
        return

    state.in_voicemail = True
    state.set_reason_once("machine_detected")
    message = amd.voicemail_message(cfg.agent.voicemail_option)
    if message:
        try:
            await session.say(resolve_template(message, variables), allow_interruptions=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("voicemail message playback failed: %s", exc)
    await runtime.end_call("machine_detected", flush_grace=bool(message))


async def entrypoint(ctx: JobContext) -> None:
    metrics.ensure_server()
    await ctx.connect()

    api_client = InternalAPI()
    lkapi = api.LiveKitAPI()  # LIVEKIT_URL / _API_KEY / _API_SECRET from env
    state = CallState()
    tasks: list[asyncio.Task[Any]] = []

    async def _finalize() -> None:
        if state.finalized:
            return
        state.finalized = True
        for task in tasks:
            task.cancel()
        # Also cancel anything still in flight via `_spawn` (flow transitions,
        # the goodbye timer, ...): none of those are in `tasks` above, so
        # without this an in-flight one could outlive the call. See
        # `_cancel_background_tasks`.
        _cancel_background_tasks()
        state.ended_at_ms = state.ended_at_ms or now_ms()
        try:
            if state.call_id:
                await api_client.finalize(state.call_id, state.build_finalize_payload())
        except InternalAPIError as exc:
            logger.error("finalize failed: %s", exc)
        finally:
            await api_client.aclose()
            await lkapi.aclose()

    ctx.add_shutdown_callback(_finalize)

    try:
        cfg_raw, participant = await _load_call_config(ctx, api_client, state)
    except Exception:
        state.set_reason_once("error_unknown")
        raise
    cfg = CallConfig.from_dict(cfg_raw)
    state.call_id = cfg.call_id
    metrics.JOBS_TOTAL.labels(direction=cfg.direction).inc()
    logger.info("job started: call_id=%s direction=%s", cfg.call_id, cfg.direction)

    if participant is None:
        try:
            if cfg.call_type == "web_call":
                # Web test call: the caller is a browser, not a SIP leg.
                participant = await _wait_for_web_participant(ctx, timeout=DIAL_TIMEOUT_S)
            else:
                participant = await _wait_for_sip_participant(ctx, timeout=DIAL_TIMEOUT_S)
        except TimeoutError:
            participant = None

    # Inbound: the caller is already on the line, and livekit-sip only
    # answers (callStatus ringing -> active) once the agent session
    # subscribes to the caller's track — waiting for "active" here would
    # deadlock both sides until the dial timeout. Only outbound dials wait.
    if cfg.direction == "inbound":
        answered = participant is not None
    else:
        answered = participant is not None and await _wait_for_answer(
            ctx, participant, timeout=DIAL_TIMEOUT_S
        )
    if not answered:
        # TODO: distinguish dial_busy / dial_failed once livekit-sip exposes a
        # disconnect-cause attribute; until then unanswered dials are
        # dial_no_answer (call_status not_connected).
        state.set_reason_once("dial_no_answer")
        ctx.shutdown(reason="dial_no_answer")
        return

    state.answered_at_ms = now_ms()

    # Dynamic variables + Retell system variables: call-scoped {{call.*}}
    # (consumer tool specs pass {{call.call_id}} as retell_call_id) and
    # defaults like {{current_time}} / {{session_duration}}, computed lazily
    # at each template resolution.
    variables = cfg.resolution_variables(answered_at_ms=state.answered_at_ms)
    runtime = CallRuntime(ctx, lkapi, state)
    runtime.sip_participant_identity = (
        participant.identity if _is_sip_participant(participant) else None
    )

    tool_http = httpx.AsyncClient()

    async def _close_tool_http() -> None:
        await tool_http.aclose()

    # Register the close callback immediately: an exception between here and
    # session start must not leak the client.
    ctx.add_shutdown_callback(_close_tool_http)

    livekit_tools = build_tools(
        cfg.llm.general_tools,
        http=tool_http,
        function_secret=cfg.function_secret,
        variables=variables,
        control=runtime,
        state=state,
        call_info=cfg.tool_call_object(),
        knowledge=api_client,
        call_id=cfg.call_id,
        knowledge_base_ids=cfg.llm.knowledge_base_ids,
    )

    instructions = resolve_template(cfg.llm.general_prompt, variables)
    if cfg.agent.enable_backchannel:
        # enable_backchannel has no livekit-agents knob; closest available
        # behavior is prompting for short verbal acknowledgments.
        instructions += (
            "\n\nWhile the user speaks, occasionally respond with brief verbal "
            'acknowledgments ("mm-hmm", "I see") but never talk over them.'
        )
    begin_message = (
        resolve_template(cfg.llm.begin_message, variables) if cfg.llm.begin_message else None
    )

    # A flow-backed agent runs its graph instead of a single prompt: it opens on
    # the start node's instructions and tools, and the start node's own line is
    # its opening line, so the three values computed above are replaced rather
    # than extended. Everything below this block is shared with, and unchanged
    # for, the single-prompt path.
    flow_wiring: _FlowWiring | None = None
    if cfg.conversation_flow is not None:
        try:
            flow_wiring = _FlowWiring(
                cfg=cfg,
                flow=cfg.conversation_flow,
                control=runtime,
                state=state,
                variables=variables,
                http=tool_http,
                knowledge=api_client,
            )
        except FlowError as exc:
            # Abort at start, before a word is spoken: a graph with an
            # unsupported node type or an edge into nowhere must never become a
            # dead end ninety seconds into a live call. The message names the
            # offending node.
            logger.error("call %s: unusable conversation flow: %s", cfg.call_id, exc)
            state.set_reason_once("error_unknown")
            ctx.shutdown(reason="invalid_conversation_flow")
            return
        instructions = flow_wiring.start_instructions
        livekit_tools = flow_wiring.start_tools()
        begin_message = None

    session, llm = build_session(
        cfg,
        model=flow_wiring.model if flow_wiring is not None else None,
        temperature=flow_wiring.temperature if flow_wiring is not None else None,
    )
    amd_speech: list[str] = []
    amd_window_open = {"open": _amd_enabled(cfg)}
    _wire_session_events(
        session,
        state,
        runtime,
        amd_speech,
        amd_window_open,
        on_user_turn=flow_wiring.on_user_item if flow_wiring is not None else None,
    )

    agent = ArhiteqAgent(
        instructions=instructions,
        tools=livekit_tools,
        begin_message=begin_message,
        # A flow's start node owns the opening line (FlowRuntime.start speaks
        # it, and honours a "user" start_speaker by deferring it), so the agent
        # itself must not also open — "user" is what keeps on_enter quiet.
        start_speaker="user" if flow_wiring is not None else cfg.llm.start_speaker,
        # The pre-reply walk seam; `_wire_session_events` above holds the
        # realtime fallback. Both go through `_FlowWiring`, which de-duplicates.
        on_user_turn=flow_wiring.on_user_turn if flow_wiring is not None else None,
    )
    if flow_wiring is not None:
        flow_wiring.attach(session, agent)

    async def _do_agent_swap(agent_id: str, entry: Mapping[str, Any]) -> str:
        """agent_swap tool: re-point the live session at another agent's config.

        The call record stays the same (Retell keeps one call across swaps);
        prompt, tools and — unless keep_current_voice — the TTS voice switch
        to the destination agent. keep_current_language is implicit: the STT
        pipeline is fixed for the session.
        """
        swap_raw = await api_client.get_agent_config(agent_id, call_id=cfg.call_id)
        if not isinstance(swap_raw.get("llm"), dict):
            # A destination without an LLM would wipe the live prompt/tools.
            logger.warning("agent swap rejected: agent %s has no LLM config", agent_id)
            return json.dumps({"error": "destination agent has no LLM configuration"})
        swap_cfg = CallConfig.from_dict({**cfg.raw, **swap_raw})
        # The destination agent's own "Current Time Awareness" takes over for
        # the rest of the call (the prompt below is resolved against it).
        variables.set_default_timezone(swap_cfg.agent.timezone)
        new_instructions = resolve_template(swap_cfg.llm.general_prompt, variables)
        new_tools = build_tools(
            swap_cfg.llm.general_tools,
            http=tool_http,
            function_secret=cfg.function_secret,
            variables=variables,
            control=runtime,
            state=state,
            call_info=cfg.tool_call_object(),
            knowledge=api_client,
            # The call id never changes across a swap (Retell keeps one call),
            # but the destination agent brings its own knowledge bases.
            call_id=cfg.call_id,
            knowledge_base_ids=swap_cfg.llm.knowledge_base_ids,
        )
        await agent.update_instructions(new_instructions)
        await agent.update_tools(new_tools)
        if not entry.get("keep_current_voice"):
            # Gemini Live sessions have no separate TTS (voice is baked into the
            # realtime model), so getattr(...) is None and the voice stays put —
            # a live voice hot-swap would need a full session rebuild.
            tts = getattr(agent, "tts", None) or getattr(session, "tts", None)
            update = getattr(tts, "update_options", None)
            if update is not None:
                try:
                    update(
                        voice=resolve_cartesia_voice(swap_cfg.agent.voice_id),
                        speed=_cartesia_speed(swap_cfg.agent.voice_speed),
                    )
                except Exception:  # voice switch is best-effort
                    logger.warning("agent_swap voice switch failed", exc_info=True)
        logger.info("agent swap: call %s now running agent %s", cfg.call_id, agent_id)
        return json.dumps({"result": f"now acting as agent {agent_id}"})

    runtime.set_agent_swap(_do_agent_swap)

    async def _post_call_started() -> None:
        # Best-effort: a slow/failed call_started webhook must never delay the
        # greeting or abort the call.
        try:
            await api_client.post_event(
                cfg.call_id,
                {"event": "call_started", "start_timestamp": state.answered_at_ms},
            )
        except Exception:  # noqa: BLE001
            logger.warning("call_started webhook failed for %s", cfg.call_id)

    # Start the agent immediately; run the call_started webhook and egress start
    # concurrently so their latency isn't dead air on the caller's line. Only
    # session.start can raise here (the other two swallow their own failures),
    # so a genuine session failure still aborts the call.
    await asyncio.gather(
        session.start(
            agent=agent,
            room=ctx.room,
            # Delete the room on ANY session close (caller hangup, model error,
            # shutdown). Without this the agent participant keeps the room —
            # and its egress recording — alive forever after a caller hangup,
            # and the job never reaches finalize. Redundant deletes after an
            # agent-initiated end_call are no-ops (NOT_FOUND is swallowed).
            room_input_options=RoomInputOptions(delete_room_on_close=True),
        ),
        _post_call_started(),
        _start_recording(lkapi, ctx.room.name, cfg.call_id, state),
    )

    if flow_wiring is not None:
        # Only now can the start node install instructions/tools and speak —
        # the agent has to be live first. Spawned, not awaited, so the opening
        # line plays concurrently with the watchdogs below rather than delaying
        # them (the single-prompt greeting runs from on_enter for the same
        # reason).
        _spawn(flow_wiring.start())

    async def _max_duration_watchdog() -> None:
        await asyncio.sleep(cfg.agent.max_call_duration_ms / 1000.0)
        state.set_reason_once("max_duration_reached")
        await runtime.end_call("max_duration_reached")

    async def _transcript_pump() -> None:
        sent = 0
        while True:
            await asyncio.sleep(TRANSCRIPT_UPDATE_INTERVAL_S)
            if len(state.items) == sent:
                continue
            sent = len(state.items)
            await api_client.post_event(
                cfg.call_id,
                {
                    "event": "transcript_update",
                    "transcript": state.transcript_text(),
                    "transcript_object": state.transcript_object(),
                },
            )

    def _track(coro: Any, name: str) -> None:
        # Long-lived call tasks are cancelled at finalize; a done-callback logs
        # any *unexpected* exception so a crashed watchdog/pump isn't silent.
        task = asyncio.create_task(coro, name=name)
        task.add_done_callback(_log_task_exception)
        tasks.append(task)

    _track(_max_duration_watchdog(), "max_duration_watchdog")
    _track(_transcript_pump(), "transcript_pump")
    if amd_window_open["open"]:
        _track(
            _run_amd(
                cfg,
                state,
                runtime,
                session,
                llm,
                participant,
                amd_speech,
                amd_window_open,
                variables,
            ),
            "amd",
        )


def _run() -> None:
    # Deploy configs pass lowercase levels ("info"); logging wants uppercase.
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    metrics.ensure_server()
    try:
        # livekit-agents ≥1.3: AgentServer with explicit-dispatch agent name.
        from livekit.agents import AgentServer
    except ImportError:
        AgentServer = None
    if AgentServer is not None:
        server = AgentServer()
        server.rtc_session(agent_name=AGENT_NAME)(entrypoint)
        cli.run_app(server)
    else:
        # livekit-agents 1.2 fallback.
        from livekit.agents import WorkerOptions

        cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name=AGENT_NAME))


if __name__ == "__main__":
    _run()
