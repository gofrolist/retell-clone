"""`prompt_variables` — which placeholder names a prompt reads.

The grammar itself is exercised through `resolve_template` in the contract
suite; what matters here is that reporting agrees with resolving, since a name
this misses is a variable nobody knows to set. `CallVariables` is here too: it
is what makes a simulated call resolve the same system placeholders a live one
does.
"""

import pytest

from arhiteq_api.services.template_variables import (
    CallVariables,
    ChatVariables,
    parse_clock,
    prompt_variables,
    resolve_deep,
    resolve_template,
)


def test_lists_names_once_in_first_appearance_order():
    text = "Hi {{first_name}}, calling {{phone}}. Bye {{first_name}}."
    assert prompt_variables(text) == ["first_name", "phone"]


def test_tolerates_inner_whitespace_like_the_resolver():
    assert prompt_variables("{{  phone  }}") == ["phone"]
    assert resolve_template("{{  phone  }}", {"phone": "+1"}) == "+1"


def test_nesting_reports_the_inner_name():
    """The inner name is the only part a caller can address directly."""
    assert prompt_variables("{{current_time_{{user_timezone}}}}") == ["user_timezone"]


def test_setting_the_inner_name_alone_leaves_a_nested_placeholder_literal():
    """The limit callers have to know about: reporting a name is not a promise
    that setting it resolves the placeholder it appeared in. The outer key is
    composed from the inner value and then looked up in turn."""
    text = "{{current_time_{{user_timezone}}}}"
    zone = "America/Los_Angeles"
    assert resolve_template(text, {"user_timezone": zone}) == text
    resolved = resolve_template(
        text, {"user_timezone": zone, f"current_time_{zone}": "Sunday at 5 PM"}
    )
    assert resolved == "Sunday at 5 PM"


def test_no_placeholders_reports_nothing():
    assert prompt_variables("You are Clara, a scheduling assistant.") == []
    assert prompt_variables("") == []
    assert prompt_variables(None) == []


def test_every_reported_name_is_one_the_resolver_substitutes():
    text = 'If {{is_last_day_of_trial}} = "true", greet {{first_name}} on {{phone}}.'
    values = {name: f"<{name}>" for name in prompt_variables(text)}
    assert "{{" not in resolve_template(text, values)


# ------------------------------------------------------- CallVariables


def test_call_variables_resolve_the_call_scoped_placeholders():
    """A simulated call has no `calls` row, so these come from here or nowhere.

    Prompts pass `{{call.call_id}}` straight into tool arguments; left literal
    it shows up as the placeholder in every mocked call an operator reads.
    """
    variables = CallVariables({}, call_id="call_abc")
    text = "id={{call_id}} dotted={{call.call_id}} type={{call_type}}"
    assert resolve_template(text, variables) == "id=call_abc dotted=call_abc type=phone_call"
    assert resolve_deep({"retell_call_id": "{{call.call_id}}"}, variables) == {
        "retell_call_id": "call_abc"
    }


def test_call_variables_answer_the_rest_of_the_dotted_family_empty():
    """The worker stores all four `call.*` keys; a simulation has no phone leg.

    Empty is what a live web call resolves them to, and it keeps placeholder
    text out of the tool arguments an operator reads back.
    """
    variables = CallVariables({}, call_id="call_abc")
    assert resolve_deep(
        {"phone": "{{call.from_number}}", "dialled": "{{call.to_number}}"}, variables
    ) == {"phone": "", "dialled": ""}


def test_call_variables_do_not_invent_a_direction_or_numbers():
    """`start_speaker` says who talks first, not who dialled — so guessing a
    direction from it would test a prompt in the branch a live call never
    takes, which is the failure this class exists to remove."""
    text = "{{direction}} {{user_number}} {{agent_number}}"
    variables = CallVariables({}, call_id="call_abc")
    assert resolve_template(text, variables) == text
    # A scenario that knows the answer can still set them.
    variables["direction"] = "inbound"
    assert resolve_template("{{direction}}", variables) == "inbound"


def test_call_variables_answer_the_time_family_in_the_agents_zone():
    variables = CallVariables({}, default_timezone="Asia/Tokyo")
    assert "JST" in resolve_template("{{current_time}}", variables)
    # A suffixed name still wins over the agent's zone, and an unknown zone
    # stays literal rather than silently answering in the wrong one.
    assert "JST" not in resolve_template("{{current_time_America/New_York}}", variables)
    assert resolve_template("{{current_time_Mars/Olympus}}", variables) == (
        "{{current_time_Mars/Olympus}}"
    )


def test_call_variables_keep_the_workers_precedence():
    """Stored variables win over system values; the dotted keys are stored.

    Copied from the worker's ResolutionVariables including the asymmetry, so a
    scenario that sets one of these names is tested against the behaviour a live
    call would give it — a case pinning `{{current_time}}` to Tuesday evening
    gets Tuesday evening, and `{{call.call_id}}` stays the call's own id.
    """
    variables = CallVariables(
        {
            "current_time": "Tuesday at 7 PM",
            "call_id": "call_supplied",
            "call.call_id": "call_supplied",
        },
        call_id="call_real",
    )
    assert resolve_template("{{current_time}}", variables) == "Tuesday at 7 PM"
    assert resolve_template("{{call_id}}", variables) == "call_supplied"
    assert resolve_template("{{call.call_id}}", variables) == "call_real"


def test_only_a_session_that_asked_for_it_can_pin_the_clock():
    """A real session's `current_time` stays the string the customer sent.

    Chat sessions resolve customer-supplied variables through the same class,
    and one passing a timestamp means it for {{current_time}} — moving
    {{current_hour}} to a moment they never set would make the same prompt read
    differently on chat than on the voice call the worker serves.
    """
    unpinned = CallVariables({"current_time": "2026-07-27T08:15"})
    assert resolve_template("{{current_time}}", unpinned) == "2026-07-27T08:15"
    assert resolve_template("{{current_hour}}", unpinned) != "8.25"
    assert ChatVariables({"current_time": "2026-07-27T08:15"})["current_time"] == (
        "2026-07-27T08:15"
    )


@pytest.mark.parametrize("pinned", ["2026-07-27", "2026-07-27T08", "next Tuesday", ""])
def test_a_pin_with_no_time_of_day_is_not_a_clock(pinned):
    """A bare date would pin midnight, which is the one reading nobody means.

    Someone writing just the date means *the day*; taking it as 00:00 puts
    every time-gated branch out of reach at once — deterministically worse than
    the real clock this replaces, which at least lands in the window sometimes.
    """
    assert parse_clock(pinned) is None
    # So it stays an ordinary variable: verbatim, and owning only its own key.
    variables = CallVariables({"current_time": pinned}, pin_clock=True)
    assert resolve_template("{{current_time}}", variables) == pinned


def test_a_pinned_current_time_drives_the_whole_time_family():
    """One timestamp puts every time placeholder at the same moment.

    A prompt gating a step on the clock reads more than one of these — the
    dose window compares {{current_time_<zone>}}, the flow picker reads the
    hour — so pinning only the exact key would leave the branch under test
    being judged against the real wall clock after all.
    """
    variables = CallVariables({"current_time": "2026-07-27T08:15"}, pin_clock=True)
    assert resolve_template("{{current_time}}", variables) == (
        "Monday, July 27, 2026 at 8:15 AM PDT"
    )
    assert resolve_template("{{current_hour}}", variables) == "8.25"
    assert resolve_template("{{current_calendar}}", variables).startswith(
        "Monday, July 27, 2026 PDT (Today)"
    )


def test_a_pinned_clock_without_an_offset_reads_the_same_in_every_zone():
    """Naive means wall-clock: "08:15" is 08:15 wherever the prompt looks.

    Someone pinning the morning dose window means "the agent is on a call at a
    quarter past eight", not "at 15:15 in Tokyo because I happened to write it
    in California time".
    """
    variables = CallVariables({"current_time": "2026-07-27 08:15"}, pin_clock=True)
    for zone in ("America/New_York", "Asia/Tokyo"):
        assert "at 8:15 AM" in resolve_template(f"{{{{current_time_{zone}}}}}", variables)


def test_a_pinned_clock_with_an_offset_converts_between_zones():
    variables = CallVariables({"current_time": "2026-07-27T08:15-07:00"}, pin_clock=True)
    tokyo = resolve_template("{{current_time_Asia/Tokyo}}", variables)
    assert "at 12:15 AM" in tokyo and "July 28" in tokyo


def test_a_current_time_that_is_not_a_timestamp_stays_an_ordinary_variable():
    """The pin is opt-in: prose keeps the old behaviour rather than erroring."""
    variables = CallVariables({"current_time": "Tuesday at 7 PM"}, pin_clock=True)
    assert resolve_template("{{current_time}}", variables) == "Tuesday at 7 PM"
    # The rest of the family is unpinned, so it still reports a real clock.
    assert resolve_template("{{current_hour}}", variables).replace(".", "").isdigit()


def test_a_pinned_clock_reaches_a_nested_zone_placeholder():
    """The shape the failing check-in case needs: pin the hour, set the zone.

    `{{current_time_{{user_timezone}}}}` is how a prompt asks what time it is
    for this caller, and a dose-window branch is graded on the answer.
    """
    variables = CallVariables(
        {"current_time": "2026-07-27T08:15", "user_timezone": "America/Los_Angeles"},
        pin_clock=True,
    )
    resolved = resolve_template("{{current_time_{{user_timezone}}}}", variables)
    assert resolved == "Monday, July 27, 2026 at 8:15 AM PDT"


def test_call_variables_leave_a_nested_time_key_to_the_scenario():
    """`{{current_time_{{user_timezone}}}}` needs the zone name a case sets.

    Fidelity, deliberately: the zone comes from the contact on a live call, and
    a simulation has no contact — inventing one would hide the branch a prompt
    takes when it genuinely does not know the caller's timezone.
    """
    text = "{{current_time_{{user_timezone}}}}"
    variables = CallVariables({}, default_timezone="Asia/Tokyo")
    assert resolve_template(text, variables) == text
    variables["user_timezone"] = "America/New_York"
    assert "{{" not in resolve_template(text, variables)
