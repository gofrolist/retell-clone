"""`prompt_variables` — which placeholder names a prompt reads.

The grammar itself is exercised through `resolve_template` in the contract
suite; what matters here is that reporting agrees with resolving, since a name
this misses is a variable nobody knows to set. `CallVariables` is here too: it
is what makes a simulated call resolve the same system placeholders a live one
does.
"""

from arhiteq_api.services.template_variables import (
    CallVariables,
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
