"""Equation transition conditions (no livekit stack).

No account fixture contains an `equation` condition, so every case here is
hand-written from Retell's documented syntax. The per-equation shape
({"left", "operator", "right"}) is OUR READING of their OpenAPI schema, which
pins only `equations` and `operator` — if a real flow ever contradicts it, this
file and `evaluate_equation_condition` are the two places to change.

The operator NAMES, unlike that shape, are pinned: the `Equation.operator`
enum in create-conversation-flow is `== != > >= < <= contains not_contains
exists not_exist`, and that wire spelling is what a real flow carries. The
prose docs show the editor's display syntax instead (`CONTAINS`, `NOT
CONTAINS`, `not exists`); this module used to accept ONLY that, so half the
enum silently evaluated False. Both spellings are tested below — dropping
either is a regression.
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
        # `>=` / `<=` are in the API enum but were never implemented, so they
        # took the "unrecognized operator" branch and were False even when
        # true. The boundary cases are the ones that distinguish them from
        # `>` / `<`, so both are asserted at equality.
        (_eq("{{user_age}}", ">=", 21), True),
        (_eq("{{user_age}}", ">=", 22), False),
        (_eq("{{user_age}}", "<=", 21), True),
        (_eq("{{user_age}}", "<=", 20), False),
        (_eq("{{user_location}}", "==", "New York"), True),
        (_eq("{{user_location}}", "!=", "New York"), False),
        # Reversed form from the docs: literal list contains a variable. Both
        # the wire spelling and the docs' display syntax must work.
        (_eq("New York, Los Angeles", "contains", "{{user_location}}"), True),
        (_eq("New York, Los Angeles", "not_contains", "{{user_location}}"), False),
        (_eq("New York, Los Angeles", "not_contains", "Boston"), True),
        (_eq("New York, Los Angeles", "CONTAINS", "{{user_location}}"), True),
        (_eq("New York, Los Angeles", "NOT CONTAINS", "{{user_location}}"), False),
        (_eq("{{user_location}}", "exists", None), True),
        # An empty value is still a DEFINED one: "Empty strings are considered
        # defined" (/build/dynamic-variables). This asserted False until the
        # operator fix — a caller who left a field blank took the wrong edge.
        (_eq("{{empty}}", "exists", None), True),
        (_eq("{{never_set}}", "exists", None), False),
        # `not_exist` is the exact complement of `exists`, including on empty.
        (_eq("{{never_set}}", "not_exist", None), True),
        (_eq("{{empty}}", "not_exist", None), False),
        (_eq("{{user_location}}", "not_exist", None), False),
        (_eq("{{never_set}}", "not exists", None), True),
        # Malformed: nothing to test the presence of, so False for BOTH unary
        # operators — `not_exist` must not report True for a missing operand.
        (_eq(None, "exists", None), False),
        (_eq(None, "not_exist", None), False),
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


def test_contains_is_substring_not_membership() -> None:
    """This PINS a deliberate reading, not a desired outcome.

    Retell's only documented CONTAINS example is a comma-separated list
    (`"New York, Los Angeles" CONTAINS {{user_location}}`), which reads as
    naturally as list membership as it does substring. We implement raw
    Python substring (see the comment in `_evaluate_single_equation`), so a
    mere fragment of one list entry also matches. If that ever gets changed
    to real membership semantics, this test is expected to start failing --
    that's the point: whoever changes it should see the decision being
    reversed, not an incidental pass.
    """
    fragment = _eq("New York, Los Angeles", "CONTAINS", "{{user_location}}")
    assert evaluate_equation_condition(_cond(fragment), {"user_location": "York"}) is True


def test_not_contains_mirrors_the_substring_reading() -> None:
    """Mirror of test_contains_is_substring_not_membership for NOT CONTAINS."""
    fragment = _eq("New York, Los Angeles", "NOT CONTAINS", "{{user_location}}")
    assert evaluate_equation_condition(_cond(fragment), {"user_location": "York"}) is False


def test_present_variable_whose_value_contains_braces_is_not_missing() -> None:
    """Regression: a variable's *value* containing `{{` must not be mistaken
    for an unresolved placeholder. `resolve_template` never re-scans a
    substituted value, so both sides here resolve to the identical literal
    string and the variable must be treated as present."""
    variables = {"note": "Say {{hi}} to caller"}
    equation = _eq("{{note}}", "==", "Say {{hi}} to caller")
    assert evaluate_equation_condition(_cond(equation), variables) is True


@pytest.mark.parametrize(
    "operator",
    [
        "==",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "contains",
        "not_contains",
        "CONTAINS",
        "NOT CONTAINS",
        "exists",
    ],
)
def test_genuinely_missing_variable_is_false_for_every_operator(operator) -> None:
    """`not_exist` is deliberately absent: it is the one operator a missing
    variable makes TRUE, which `test_single_equation` covers."""
    equation = _eq("{{never_set}}", operator, "anything" if operator != "exists" else None)
    assert evaluate_equation_condition(_cond(equation), {}) is False


#: The `Equation.operator` enum from Retell's create-conversation-flow schema,
#: paired with operands that make each one TRUE. Every entry must evaluate
#: True: an operator this module does not recognize returns False from the
#: "unrecognized operator" fallthrough, so a False here means that operator is
#: silently dead on every imported flow — the exact bug this table guards.
DOCUMENTED_OPERATORS = [
    ("==", "{{user_location}}", "New York"),
    ("!=", "{{user_location}}", "Boston"),
    (">", "{{user_age}}", 18),
    (">=", "{{user_age}}", 21),
    ("<", "{{user_age}}", 22),
    ("<=", "{{user_age}}", 21),
    ("contains", "{{user_location}}", "New"),
    ("not_contains", "{{user_location}}", "Boston"),
    ("exists", "{{user_location}}", None),
    ("not_exist", "{{never_set}}", None),
]


@pytest.mark.parametrize(("operator", "left", "right"), DOCUMENTED_OPERATORS)
def test_every_documented_operator_is_implemented(operator, left, right) -> None:
    assert evaluate_equation_condition(_cond(_eq(left, operator, right)), VARS) is True


def test_the_enum_is_covered_in_full() -> None:
    """Guard against the table above quietly losing an operator."""
    assert {operator for operator, _left, _right in DOCUMENTED_OPERATORS} == {
        "==",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "contains",
        "not_contains",
        "exists",
        "not_exist",
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("inf", False),
        ("-inf", False),
        ("nan", False),
    ],
)
def test_non_finite_floats_are_not_numeric(text, expected) -> None:
    # "inf"/"-inf"/"nan" must not be treated as numeric: "inf" > 18 would
    # otherwise be True, and comparing two "nan" strings would make `!=`
    # True even though the raw text is identical.
    assert evaluate_equation_condition(_cond(_eq(text, ">", 18)), {}) is expected
    assert evaluate_equation_condition(_cond(_eq(text, "!=", text)), {}) is False


def test_system_variable_resolves_inside_equation() -> None:
    """The fixtures' branch conditions are time-based; {{current_time}} must
    resolve through the same ResolutionVariables a live call would build.

    America/Phoenix never observes DST, so pinning the agent's configured
    timezone to it makes the {{current_time}} abbreviation ("MST") knowable
    regardless of when this test runs -- unlike the default
    America/Los_Angeles, whose PST/PDT abbreviation depends on the calendar
    and would make an exact assertion here flaky.
    """
    call = CallConfig(
        call_id="call_123",
        direction="outbound",
        from_number="+15551234567",
        to_number="+15557654321",
        call_type="phone_call",
        agent=AgentConfig(timezone="America/Phoenix"),
        llm=LLMConfig(),
        dynamic_variables={},
        metadata={},
        function_secret="secret",
    )
    variables = call.resolution_variables()
    cond = _cond(_eq("{{current_time}}", "CONTAINS", "MST"), operator="&&")
    assert evaluate_equation_condition(cond, variables) is True
    # current_time always resolves to a non-empty string, so `exists` is True.
    # This also exercises the new direct-lookup path in `_resolve_operand`
    # (a bare {{name}} operand), confirming a system variable still resolves
    # through it.
    exists_cond = _cond(_eq("{{current_time}}", "exists", None))
    assert evaluate_equation_condition(exists_cond, variables) is True
