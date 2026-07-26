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


def test_nesting_reports_the_inner_name_that_can_be_set():
    """The composed key is derived, so `user_timezone` is the settable name."""
    assert prompt_variables("{{current_time_{{user_timezone}}}}") == ["user_timezone"]


def test_no_placeholders_reports_nothing():
    assert prompt_variables("You are Clara, a scheduling assistant.") == []
    assert prompt_variables("") == []
    assert prompt_variables(None) == []


def test_every_reported_name_is_one_the_resolver_substitutes():
    text = 'If {{is_last_day_of_trial}} = "true", greet {{first_name}} on {{phone}}.'
    values = {name: f"<{name}>" for name in prompt_variables(text)}
    assert "{{" not in resolve_template(text, values)
