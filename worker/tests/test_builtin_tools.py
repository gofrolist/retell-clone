"""Pure-logic tests for the Retell built-in tool helpers (no livekit stack)."""

from arhiteq_worker.state import CallState
from arhiteq_worker.tools import (
    DTMF_CODES,
    cal_default_end_date,
    cal_event_type_id,
    cal_timezone,
    extract_variable_parameters,
    press_digit_delay_s,
    sms_numbers,
    sms_static_content,
    variable_to_string,
)


def test_dtmf_codes_cover_keypad() -> None:
    for digit in "0123456789":
        assert DTMF_CODES[digit] == int(digit)
    assert DTMF_CODES["*"] == 10
    assert DTMF_CODES["#"] == 11


def test_press_digit_delay_default_and_clamp() -> None:
    assert press_digit_delay_s({}) == 1.0
    assert press_digit_delay_s({"delay_ms": 0}) == 0.0
    assert press_digit_delay_s({"delay_ms": 2500}) == 2.5
    assert press_digit_delay_s({"delay_ms": 60_000}) == 5.0
    assert press_digit_delay_s({"delay_ms": -5}) == 1.0
    assert press_digit_delay_s({"delay_ms": "fast"}) == 1.0
    assert press_digit_delay_s({"delay_ms": True}) == 1.0


def test_extract_variable_parameters_types() -> None:
    schema = extract_variable_parameters(
        {
            "variables": [
                {"name": "customer_name", "type": "string", "description": "Full name"},
                {
                    "name": "plan",
                    "type": "enum",
                    "description": "Chosen plan",
                    "choices": ["basic", "pro"],
                    "required": True,
                },
                {"name": "is_existing", "type": "boolean", "description": "Existing customer"},
                {"name": "age", "type": "number", "description": "Age"},
                {"type": "string", "description": "nameless — skipped"},
                "not-a-dict",
            ]
        }
    )
    props = schema["properties"]
    assert schema["type"] == "object"
    assert props["customer_name"]["type"] == "string"
    assert props["plan"] == {
        "description": "Chosen plan",
        "type": "string",
        "enum": ["basic", "pro"],
    }
    assert props["is_existing"]["type"] == "boolean"
    assert props["age"]["type"] == "number"
    assert list(props) == ["customer_name", "plan", "is_existing", "age"]
    assert schema["required"] == ["plan"]


def test_extract_variable_parameters_examples_folded_into_description() -> None:
    schema = extract_variable_parameters(
        {
            "variables": [
                {
                    "name": "zip",
                    "type": "string",
                    "description": "ZIP code.",
                    "examples": ["94110", "10001"],
                }
            ]
        }
    )
    assert "94110" in schema["properties"]["zip"]["description"]


def test_variable_to_string_bools_lowercase() -> None:
    assert variable_to_string(True) == "true"
    assert variable_to_string(False) == "false"
    assert variable_to_string(42) == "42"
    assert variable_to_string("x") == "x"


def test_cal_event_type_id_forms() -> None:
    assert cal_event_type_id({"event_type_id": 123}, {}) == 123
    assert cal_event_type_id({"event_type_id": "456"}, {}) == 456
    assert cal_event_type_id({"event_type_id": "{{etid}}"}, {"etid": "789"}) == 789
    assert cal_event_type_id({"event_type_id": "not-a-number"}, {}) is None
    assert cal_event_type_id({"event_type_id": True}, {}) is None
    assert cal_event_type_id({}, {}) is None


def test_cal_default_end_date_is_one_week_out() -> None:
    # Matches the tool schema's promise: "Defaults to one week after start_date."
    assert cal_default_end_date("2026-07-14") == "2026-07-21"
    assert cal_default_end_date("2026-12-28") == "2027-01-04"
    # Unparseable input falls through unchanged (Cal.com returns the error).
    assert cal_default_end_date("next tuesday") == "next tuesday"


def test_cal_timezone_resolves_variables() -> None:
    assert cal_timezone({}, {}) == "UTC"
    assert cal_timezone({"timezone": "America/New_York"}, {}) == "America/New_York"
    assert cal_timezone({"timezone": "{{tz}}"}, {"tz": "Europe/Rome"}) == "Europe/Rome"


def test_sms_numbers_by_direction() -> None:
    inbound = {"direction": "inbound", "from_number": "+15550001111", "to_number": "+15550002222"}
    agent, user = sms_numbers(inbound)
    assert (agent, user) == ("+15550002222", "+15550001111")

    outbound = {
        "direction": "outbound",
        "from_number": "+15550002222",
        "to_number": "+15550001111",
    }
    agent, user = sms_numbers(outbound)
    assert (agent, user) == ("+15550002222", "+15550001111")

    assert sms_numbers(None) == ("", "")


def test_sms_static_content_only_for_predefined() -> None:
    entry = {"sms_content": {"type": "predefined", "content": "Hi {{name}}!"}}
    assert sms_static_content(entry, {"name": "Ada"}) == "Hi Ada!"
    # type defaults to predefined on the Retell wire
    assert sms_static_content({"sms_content": {"content": "Yo"}}, {}) == "Yo"
    assert sms_static_content({"sms_content": {"type": "inferred", "prompt": "..."}}, {}) is None
    assert sms_static_content({}, {}) is None


def test_collected_dynamic_variables_in_finalize_payload() -> None:
    state = CallState(call_id="c1")
    state.collected_dynamic_variables["plan"] = "pro"
    payload = state.build_finalize_payload()
    assert payload["collected_dynamic_variables"] == {"plan": "pro"}

    empty = CallState(call_id="c2").build_finalize_payload()
    assert empty["collected_dynamic_variables"] is None
