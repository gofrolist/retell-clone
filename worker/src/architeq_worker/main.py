"""Architeq voice worker — livekit-agents entrypoint (agent_name
"architeq-agent"). One job per call; see docs/ARCHITECTURE.md and
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
from functools import lru_cache
from typing import Any

import httpx
from livekit import api, rtc
from livekit.agents import Agent, AgentSession, JobContext, cli

from architeq_worker import amd
from architeq_worker import metrics
from architeq_worker.config import CallConfig
from architeq_worker.internal_api import InternalAPI, InternalAPIError
from architeq_worker.state import CallState, now_ms
from architeq_worker.tools import DTMF_CODES, build_tools
from architeq_worker.variables import resolve_template
from architeq_worker.voices import resolve_cartesia_voice

logger = logging.getLogger("architeq-worker")

# Strong refs to fire-and-forget tasks: the event loop only keeps a weak
# reference, so an un-held task can be GC'd before it runs (e.g. the inactivity
# hangup would silently never fire).
_background_tasks: set[asyncio.Task[Any]] = set()


def _spawn(coro: Any) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


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


AGENT_NAME = "architeq-agent"
TRANSCRIPT_UPDATE_INTERVAL_S = 10.0
AMD_SPEECH_WINDOW_S = 5.0
DIAL_TIMEOUT_S = float(os.getenv("ARCHITEQ_DIAL_TIMEOUT_S", "60"))
DEFAULT_GEMINI_MODEL = os.getenv("ARCHITEQ_GEMINI_MODEL", "gemini-2.5-flash")
CARTESIA_TTS_MODEL = os.getenv("ARCHITEQ_CARTESIA_TTS_MODEL", "sonic-2")
CARTESIA_STT_MODEL = os.getenv("ARCHITEQ_CARTESIA_STT_MODEL", "ink-whisper")

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
    # the deployment default — Architeq runs conversation on Gemini only.
    if model and "gemini" in model.lower():
        return model
    return DEFAULT_GEMINI_MODEL


def _min_interruption_duration(sensitivity: float) -> float:
    s = _clamp(sensitivity, 0.0, 1.0)
    return 0.1 + (1.0 - s) * 1.4


def _endpointing_delays(responsiveness: float) -> tuple[float, float]:
    r = _clamp(responsiveness, 0.0, 1.0)
    return 0.2 + (1.0 - r) * 1.0, 3.0 + (1.0 - r) * 3.0


def build_session(cfg: CallConfig) -> tuple[AgentSession, Any]:
    """Build the Cartesia-STT → Gemini → Cartesia-TTS AgentSession.

    Returns (session, llm) — the LLM instance is reused by the AMD greeting
    classifier. Optional tuning kwargs are filtered against the installed
    AgentSession signature so the worker runs on livekit-agents 1.2 (flat
    kwargs) and ≥1.3 (TurnHandlingOptions) alike.
    """
    from livekit.plugins import cartesia, google

    llm = google.LLM(  # GOOGLE_API_KEY read from env by the plugin
        model=_gemini_model(cfg.llm.model),
        temperature=cfg.llm.model_temperature,
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

    kwargs: dict[str, Any] = {"stt": stt, "llm": llm, "tts": tts}
    params = inspect.signature(AgentSession.__init__).parameters
    min_delay, max_delay = _endpointing_delays(cfg.agent.responsiveness)
    optional: dict[str, Any] = {
        "allow_interruptions": cfg.agent.interruption_sensitivity > 0,
        "min_interruption_duration": _min_interruption_duration(cfg.agent.interruption_sensitivity),
        # Closest option to end_call_after_silence_ms: the user is marked
        # "away" after this much silence and the worker hangs up (reason
        # "inactivity") from the user_state_changed handler.
        "user_away_timeout": cfg.agent.end_call_after_silence_ms / 1000.0,
    }
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
    return AgentSession(**kwargs), llm


class ArchiteqAgent(Agent):
    def __init__(
        self,
        *,
        instructions: str,
        tools: list[Any],
        begin_message: str | None,
        start_speaker: str,
    ) -> None:
        super().__init__(instructions=instructions, tools=tools)
        self._begin_message = begin_message
        self._start_speaker = start_speaker

    async def on_enter(self) -> None:
        if self._start_speaker != "agent":
            return  # start_speaker == "user": wait for the callee to talk
        if self._begin_message:
            await self.session.say(self._begin_message)
        else:
            self.session.generate_reply()


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

    async def end_call(self, reason: str = "agent_hangup") -> None:
        self._state.set_reason_once(reason)
        self._state.ended_at_ms = self._state.ended_at_ms or now_ms()
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


def _is_sip_participant(p: rtc.RemoteParticipant) -> bool:
    if getattr(p, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        return True
    return any(key.startswith("sip.") for key in p.attributes)


async def _wait_for_sip_participant(ctx: JobContext, timeout: float) -> rtc.RemoteParticipant:
    for p in ctx.room.remote_participants.values():
        if _is_sip_participant(p):
            return p
    return await asyncio.wait_for(
        ctx.wait_for_participant(kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP),
        timeout,
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
        except asyncio.TimeoutError:
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
) -> None:
    last_llm_ttft: dict[str, float] = {}

    @session.on("conversation_item_added")
    def _on_item(ev: Any) -> None:
        item = ev.item
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None) or ""
        if role == "assistant":
            state.add_message("agent", text)
        elif role == "user":
            state.add_message("user", text)

    @session.on("user_input_transcribed")
    def _on_transcribed(ev: Any) -> None:
        if amd_window_open.get("open") and getattr(ev, "is_final", False):
            amd_speech.append(getattr(ev, "transcript", "") or "")

    @session.on("user_state_changed")
    def _on_user_state(ev: Any) -> None:
        # user_away_timeout fired (maps end_call_after_silence_ms).
        if getattr(ev, "new_state", None) == "away":
            state.set_reason_once("inactivity")
            _spawn(runtime.end_call("inactivity"))

    @session.on("close")
    def _on_close(ev: Any) -> None:
        state.ended_at_ms = state.ended_at_ms or now_ms()
        reason = str(getattr(ev, "reason", "") or "")
        if "participant_disconnected" in reason:
            state.set_reason_once("user_hangup")
        elif "error" in reason:
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
    await runtime.end_call("machine_detected")


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
            participant = await _wait_for_sip_participant(ctx, timeout=DIAL_TIMEOUT_S)
        except asyncio.TimeoutError:
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

    # Dynamic variables + call-scoped {{call.*}} system variables
    # (consumer tool specs pass {{call.call_id}} as retell_call_id).
    variables = cfg.resolution_variables()
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

    session, llm = build_session(cfg)
    amd_speech: list[str] = []
    amd_window_open = {"open": cfg.direction == "outbound" and cfg.agent.enable_voicemail_detection}
    _wire_session_events(session, state, runtime, amd_speech, amd_window_open)

    agent = ArchiteqAgent(
        instructions=instructions,
        tools=livekit_tools,
        begin_message=begin_message,
        start_speaker=cfg.llm.start_speaker,
    )

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
        new_instructions = resolve_template(swap_cfg.llm.general_prompt, variables)
        new_tools = build_tools(
            swap_cfg.llm.general_tools,
            http=tool_http,
            function_secret=cfg.function_secret,
            variables=variables,
            control=runtime,
            state=state,
            call_info=cfg.tool_call_object(),
        )
        await agent.update_instructions(new_instructions)
        await agent.update_tools(new_tools)
        if not entry.get("keep_current_voice"):
            tts = getattr(agent, "tts", None) or getattr(session, "tts", None)
            update = getattr(tts, "update_options", None)
            if update is not None:
                try:
                    update(
                        voice=resolve_cartesia_voice(swap_cfg.agent.voice_id),
                        speed=_cartesia_speed(swap_cfg.agent.voice_speed),
                    )
                except Exception:  # noqa: BLE001 - voice switch is best-effort
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
        session.start(agent=agent, room=ctx.room),
        _post_call_started(),
        _start_recording(lkapi, ctx.room.name, cfg.call_id, state),
    )

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
