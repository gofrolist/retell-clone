"""CallState / finalize payload — the worker side of the finalize contract."""

from arhiteq_worker.state import CallState


def _answered_state() -> CallState:
    s = CallState(call_id="call_x")
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


class TestItemTimingAndToolIds:
    def test_time_ms_is_offset_from_answer(self, monkeypatch):
        s = CallState(call_id="c")
        s.answered_at_ms = 1_000_000
        monkeypatch.setattr("arhiteq_worker.state.now_ms", lambda: 1_012_345)
        s.add_message("agent", "Hi")
        s.add_tool_invocation("log_outcome", "{}")
        s.add_tool_result("log_outcome", "ok")
        assert [i["time_ms"] for i in s.items] == [12_345, 12_345, 12_345]

    def test_unanswered_items_have_no_time_ms(self):
        s = CallState()
        s.add_message("agent", "Hi")
        s.add_tool_invocation("t", "{}")
        assert all("time_ms" not in i for i in s.items)

    def test_clock_skew_clamps_to_zero(self, monkeypatch):
        s = CallState()
        s.answered_at_ms = 2_000_000
        monkeypatch.setattr("arhiteq_worker.state.now_ms", lambda: 1_999_000)
        s.add_message("user", "Hi")
        assert s.items[0]["time_ms"] == 0

    def test_tool_call_id_pairs_result_with_invocation(self):
        s = CallState()
        s.add_tool_invocation("a", "{}")
        s.add_tool_invocation("b", "{}")
        s.add_tool_result("b", "rb")
        s.add_tool_result("a", "ra")
        inv_a, inv_b, res_b, res_a = s.items
        assert inv_a["tool_call_id"] != inv_b["tool_call_id"]
        assert res_b["tool_call_id"] == inv_b["tool_call_id"]
        assert res_a["tool_call_id"] == inv_a["tool_call_id"]

    def test_repeated_same_tool_pairs_in_order(self):
        s = CallState()
        s.add_tool_invocation("t", "{}")
        s.add_tool_result("t", "r1")
        s.add_tool_invocation("t", "{}")
        s.add_tool_result("t", "r2")
        assert s.items[1]["tool_call_id"] == s.items[0]["tool_call_id"]
        assert s.items[3]["tool_call_id"] == s.items[2]["tool_call_id"]

    def test_result_without_invocation_has_no_tool_call_id(self):
        s = CallState()
        s.add_tool_result("orphan", "r")
        assert "tool_call_id" not in s.items[0]

    def test_finalize_payload_items_carry_new_fields(self, monkeypatch):
        s = CallState(call_id="c")
        s.answered_at_ms = 1_000_000
        s.ended_at_ms = 1_060_000
        monkeypatch.setattr("arhiteq_worker.state.now_ms", lambda: 1_030_000)
        s.add_tool_invocation("log_outcome", '{"k": 1}')
        s.add_tool_result("log_outcome", "ok")
        items = s.build_finalize_payload()["transcript_with_tool_calls"]
        assert items[0]["time_ms"] == 30_000
        assert items[0]["tool_call_id"] == items[1]["tool_call_id"]
