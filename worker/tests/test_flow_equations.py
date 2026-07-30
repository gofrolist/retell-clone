"""Equation transition conditions (no livekit stack).

No account fixture contains an `equation` condition, so every case here is
hand-written from Retell's documented syntax. The per-equation shape
({"left", "operator", "right"}) is OUR READING of their OpenAPI schema, which
pins only `equations` and `operator` — if a real flow ever contradicts it, this
file and `evaluate_equation_condition` are the two places to change.
"""

import pytest

from arhiteq_worker.config import AgentConfig, CallConfig, LLMConfig
from arhiteq_worker.flow import evaluate_equation_condition


def _cond(*equations, operator="&&"):
    return {"type": "equation", "equations": list(equations), "operator": operator}


def _eq(left, operator, right):
    return {"left": left, "operator": operator, "right": right}


VARS = {"user_age": "21", "user_location": "New York", "empty": ""}


@pytest.mark.parametrize(
    ("equation", "expected"),
    [
        # Variables arrive as strings; a numeric comparand must still compare numerically.
        (_eq("{{user_age}}", ">", 18), True),
        (_eq("{{user_age}}", "<", 18), False),
        (_eq("{{user_location}}", "==", "New York"), True),
        (_eq("{{user_location}}", "!=", "New York"), False),
        # Reversed form from the docs: literal list CONTAINS a variable.
        (_eq("New York, Los Angeles", "CONTAINS", "{{user_location}}"), True),
        (_eq("New York, Los Angeles", "NOT CONTAINS", "{{user_location}}"), False),
        (_eq("{{user_location}}", "exists", None), True),
        (_eq("{{empty}}", "exists", None), False),
        (_eq("{{never_set}}", "exists", None), False),
        # A numeric operator against a non-numeric string is False, not an error.
        (_eq("{{user_location}}", ">", 18), False),
        # A missing variable is False for every operator.
        (_eq("{{never_set}}", "==", "New York"), False),
        (_eq("{{never_set}}", ">", 1), False),
    ],
)
def test_single_equation(equation, expected) -> None:
    assert evaluate_equation_condition(_cond(equation), VARS) is expected


def test_and_requires_every_equation() -> None:
    both = _cond(_eq("{{user_age}}", ">", 18), _eq("{{user_location}}", "==", "New York"))
    assert evaluate_equation_condition(both, VARS) is True
    one = _cond(_eq("{{user_age}}", ">", 18), _eq("{{user_location}}", "==", "Boston"))
    assert evaluate_equation_condition(one, VARS) is False


def test_or_requires_any_equation() -> None:
    cond = _cond(
        _eq("{{user_age}}", "<", 18),
        _eq("{{user_location}}", "==", "New York"),
        operator="||",
    )
    assert evaluate_equation_condition(cond, VARS) is True


@pytest.mark.parametrize(
    "condition",
    [
        {"type": "equation", "equations": [], "operator": "&&"},
        {"type": "equation", "equations": [_eq("{{user_age}}", ">", 1)]},  # no operator
        {"type": "equation"},
        {"type": "prompt", "prompt": "not an equation"},
        None,
        "nonsense",
    ],
)
def test_malformed_conditions_are_false_and_never_raise(condition) -> None:
    assert evaluate_equation_condition(condition, VARS) is False


def test_many_equations_do_not_raise() -> None:
    cond = _cond(*[_eq("{{user_age}}", ">", 1)] * 60, operator="&&")
    assert evaluate_equation_condition(cond, VARS) in (True, False)


def test_system_variable_resolves_inside_equation() -> None:
    """The fixtures' branch conditions are time-based; {{current_time}} must
    resolve through the same ResolutionVariables a live call would build."""
    call = CallConfig(
        call_id="call_123",
        direction="outbound",
        from_number="+15551234567",
        to_number="+15557654321",
        call_type="phone_call",
        agent=AgentConfig(),
        llm=LLMConfig(),
        dynamic_variables={},
        metadata={},
        function_secret="secret",
    )
    variables = call.resolution_variables()
    cond = _cond(_eq("{{current_time}}", "CONTAINS", "PST"), operator="&&")
    result = evaluate_equation_condition(cond, variables)
    assert result in (True, False)
    # current_time always resolves to a non-empty string, so `exists` is True.
    exists_cond = _cond(_eq("{{current_time}}", "exists", None))
    assert evaluate_equation_condition(exists_cond, variables) is True
