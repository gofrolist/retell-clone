from architeq_worker.variables import resolve_deep, resolve_template


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
