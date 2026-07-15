from datetime import datetime, timezone

import pytest

from architeq_worker import variables as variables_mod
from architeq_worker.variables import ResolutionVariables, resolve_deep, resolve_template


def test_basic_substitution() -> None:
    assert resolve_template("Hello {{first_name}}!", {"first_name": "John"}) == "Hello John!"


def test_multiple_and_repeated_keys() -> None:
    text = "{{a}} and {{b}} and {{a}}"
    assert resolve_template(text, {"a": "1", "b": "2"}) == "1 and 2 and 1"


def test_unknown_variable_stays_literal() -> None:
    assert (
        resolve_template("Hi {{first_name}}, {{unknown}}", {"first_name": "Jo"})
        == "Hi Jo, {{unknown}}"
    )


def test_whitespace_inside_braces() -> None:
    assert resolve_template("{{ first_name }}", {"first_name": "Jo"}) == "Jo"


def test_arbitrary_keys_not_renamed() -> None:
    # ARCHITECTURE.md rule 5: arbitrary string keys, no renaming/dropping.
    variables = {"weird key.with-chars": "v", "is_day_1": "true"}
    assert resolve_template("{{weird key.with-chars}}/{{is_day_1}}", variables) == "v/true"


def test_non_string_value_stringified() -> None:
    assert resolve_template("n={{n}}", {"n": 5}) == "n=5"


def test_empty_string_value() -> None:
    assert resolve_template("[{{x}}]", {"x": ""}) == "[]"


def test_no_placeholders_passthrough() -> None:
    assert resolve_template("plain text", {"a": "b"}) == "plain text"


def test_resolve_deep_nested_structures() -> None:
    value = {
        "description": "Call {{first_name}}",
        "list": ["{{a}}", 3, {"inner": "{{b}}"}],
        "untouched": 7,
    }
    resolved = resolve_deep(value, {"first_name": "Jo", "a": "1", "b": "2"})
    assert resolved == {
        "description": "Call Jo",
        "list": ["1", 3, {"inner": "2"}],
        "untouched": 7,
    }


def test_resolve_deep_does_not_rewrite_keys() -> None:
    value = {"{{key}}": "{{val}}"}
    assert resolve_deep(value, {"key": "k", "val": "v"}) == {"{{key}}": "v"}


# --- Retell default system variables (docs.retellai.com/build/dynamic-variables) ---


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    # 2024-03-28 22:30 UTC == 3:30 PM PDT (Thursday) == 7:30 AM JST (Friday)
    now = datetime(2024, 3, 28, 22, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(variables_mod, "_utcnow", lambda: now)
    return now


def _phone_vars(**overrides) -> ResolutionVariables:
    kwargs = {
        "call_id": "call_abc",
        "direction": "inbound",
        "from_number": "+12137771234",
        "to_number": "+12137771235",
        "call_type": "phone_call",
    }
    kwargs.update(overrides)
    return ResolutionVariables({}, **kwargs)


def test_current_time_default_timezone(frozen_now: datetime) -> None:
    assert (
        resolve_template("{{current_time}}", ResolutionVariables({}))
        == "Thursday, March 28, 2024 at 3:30 PM PDT"
    )


def test_current_time_timezone_variant(frozen_now: datetime) -> None:
    assert (
        resolve_template("{{current_time_Asia/Tokyo}}", ResolutionVariables({}))
        == "Friday, March 29, 2024 at 7:30 AM JST"
    )


def test_current_hour_is_fraction(frozen_now: datetime) -> None:
    variables = ResolutionVariables({})
    assert resolve_template("{{current_hour}}", variables) == "15.5"
    assert resolve_template("{{current_hour_Asia/Tokyo}}", variables) == "7.5"


def test_current_calendar_14_days(frozen_now: datetime) -> None:
    lines = resolve_template("{{current_calendar}}", ResolutionVariables({})).split("\n")
    assert len(lines) == 14
    assert lines[0] == "Thursday, March 28, 2024 PDT (Today)"
    assert lines[1] == "Friday, March 29, 2024 PDT"
    assert lines[13] == "Wednesday, April 10, 2024 PDT"


def test_unknown_timezone_stays_literal(frozen_now: datetime) -> None:
    text = "{{current_time_Fake/Zone}}"
    assert resolve_template(text, ResolutionVariables({})) == text


def test_nested_variable_resolves_innermost_first(frozen_now: datetime) -> None:
    variables = ResolutionVariables({"user_timezone": "Asia/Tokyo"})
    assert (
        resolve_template("{{current_time_{{user_timezone}}}}", variables)
        == "Friday, March 29, 2024 at 7:30 AM JST"
    )


def test_nested_with_unknown_inner_stays_literal(frozen_now: datetime) -> None:
    text = "{{current_time_{{user_timezone}}}}"
    assert resolve_template(text, ResolutionVariables({})) == text


def test_value_containing_placeholder_is_not_reexpanded() -> None:
    # Contract: values reach the agent verbatim — substitution output is
    # never re-scanned, so {{...}} text inside a value stays literal even
    # when it names a known variable (incl. captured/system ones).
    variables = ResolutionVariables(
        {"notes": "send the {{first_name}} template", "first_name": "Bob"},
        call_id="call_abc",
        call_type="phone_call",
    )
    assert resolve_template("{{notes}}", variables) == "send the {{first_name}} template"
    assert resolve_template("{{notes}}", {"notes": "id is {{call_id}}"}) == "id is {{call_id}}"


def test_phone_call_variables_inbound() -> None:
    variables = _phone_vars()
    assert (
        resolve_template(
            "{{direction}} {{user_number}} {{agent_number}} {{call_id}} {{call_type}}", variables
        )
        == "inbound +12137771234 +12137771235 call_abc phone_call"
    )


def test_phone_call_variables_outbound_swaps_numbers() -> None:
    variables = _phone_vars(direction="outbound")
    assert resolve_template("{{user_number}} {{agent_number}}", variables) == (
        "+12137771235 +12137771234"
    )


def test_web_call_has_no_phone_variables() -> None:
    variables = _phone_vars(call_type="web_call", from_number="", to_number="")
    assert resolve_template("{{direction}} {{user_number}} {{agent_number}}", variables) == (
        "{{direction}} {{user_number}} {{agent_number}}"
    )
    assert resolve_template("{{call_id}} {{call_type}}", variables) == "call_abc web_call"


def test_session_type_is_voice() -> None:
    assert resolve_template("{{session_type}}", ResolutionVariables({})) == "voice"


def test_session_duration(frozen_now: datetime) -> None:
    answered = int((frozen_now.timestamp() - 20 * 60 - 30) * 1000)
    variables = ResolutionVariables({}, answered_at_ms=answered)
    assert resolve_template("{{session_duration}}", variables) == "20 minutes 30 seconds"


def test_session_duration_unavailable_before_answer() -> None:
    text = "{{session_duration}}"
    assert resolve_template(text, ResolutionVariables({})) == text


def test_user_variables_override_system(frozen_now: datetime) -> None:
    variables = ResolutionVariables({"current_time": "half past nine"})
    assert resolve_template("{{current_time}}", variables) == "half past nine"


def test_update_still_wins_over_system(frozen_now: datetime) -> None:
    # tools.py merges captured response/extract variables via dict.update.
    variables = ResolutionVariables({})
    assert isinstance(variables, dict)
    variables.update({"current_time": "captured"})
    assert resolve_template("{{current_time}}", variables) == "captured"


def test_get_falls_back_to_system() -> None:
    variables = _phone_vars()
    assert variables.get("call_id") == "call_abc"
    assert variables.get("nope", "default") == "default"
