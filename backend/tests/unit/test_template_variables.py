"""`prompt_variables` — which placeholder names a prompt reads.

The grammar itself is exercised through `resolve_template` in the contract
suite; what matters here is that reporting agrees with resolving, since a name
this misses is a variable nobody knows to set.
"""

from arhiteq_api.services.template_variables import prompt_variables, resolve_template


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
