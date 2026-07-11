"""CallState / finalize payload — the worker side of the finalize contract."""

from state import CallState


def _answered_state() -> CallState:
    s = CallState(call_id="call_x", direction="outbound")
    s.answered_at_ms = 1_750_000_000_000
    s.ended_at_ms = 1_750_000_134_000
    return s


class TestTranscript:
    def test_exact_line_format(self):
        s = CallState()
        s.add_message("agent", "Hi John.")
        s.add_message("user", "Hello.")
        s.add_message("agent", "How are you?")
        assert s.transcript_text() == "Agent: Hi John.\nUser: Hello.\nAgent: How are you?"

    def test_empty_content_skipped(self):
        s = CallState()
        s.add_message("agent", "")
        s.add_message("user", "Hi")
        assert s.transcript_text() == "User: Hi"

    def test_tool_calls_excluded_from_transcript_but_kept_in_items(self):
        s = CallState()
        s.add_message("agent", "One sec.")
        s.add_tool_invocation("schedule_callback", '{"phone": "+1"}')
        s.add_tool_result("schedule_callback", '{"ok": true}')
        assert s.transcript_object() == [{"role": "agent", "content": "One sec."}]
        payload = s.build_finalize_payload()
        assert len(payload["transcript_with_tool_calls"]) == 3


class TestDurationAndStatus:
    def test_duration_is_answer_to_hangup(self):
        payload = _answered_state().build_finalize_payload()
        assert payload["duration_ms"] == 134_000
        assert payload["call_status"] == "ended"

    def test_unanswered_call_has_zero_duration_and_not_connected(self):
        s = CallState()
        s.ended_at_ms = 1_750_000_010_000
        payload = s.build_finalize_payload()
        assert payload["duration_ms"] == 0
        assert payload["call_status"] == "not_connected"
        assert payload["disconnection_reason"] == "dial_no_answer"

    def test_error_reason_maps_to_error_status(self):
        s = _answered_state()
        s.set_reason_once("error_llm")
        assert s.build_finalize_payload()["call_status"] == "error"


class TestDisconnectionReason:
    def test_first_reason_wins(self):
        s = _answered_state()
        s.set_reason_once("machine_detected")
        s.set_reason_once("agent_hangup")  # follows when we hang up on voicemail
        assert s.build_finalize_payload()["disconnection_reason"] == "machine_detected"

    def test_answered_without_reason_is_user_hangup(self):
        assert _answered_state().build_finalize_payload()["disconnection_reason"] == "user_hangup"

    def test_voicemail_flag_passthrough(self):
        s = _answered_state()
        s.in_voicemail = True
        assert s.build_finalize_payload()["in_voicemail"] is True


class TestLatency:
    def test_no_samples_means_null(self):
        assert _answered_state().build_finalize_payload()["latency"] is None

    def test_percentiles(self):
        s = _answered_state()
        s.e2e_latency_ms = [100.0] * 95 + [2000.0] * 5
        latency = s.build_finalize_payload()["latency"]["e2e"]
        assert latency["p50"] == 100.0
        assert latency["p95"] == 100.0
        s.e2e_latency_ms = list(range(1, 101))
        latency = s.build_finalize_payload()["latency"]["e2e"]
        assert latency["p50"] == 50
        assert latency["p95"] == 95
