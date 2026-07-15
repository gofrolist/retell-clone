"""Call-scoped system variables (Retell parity).

Consumer tool specs instruct the model to pass ``{{call.call_id}}`` (e.g.
log_outcome REQUIRES retell_call_id={{call.call_id}}). Retell resolves those
from the live call object; the worker must expose the same keys.
"""

from architeq_worker.config import CallConfig

CONFIG_DICT = {
    "call_id": "call_abc123",
    "direction": "outbound",
    "from_number": "+19499195585",
    "to_number": "+15551234567",
    "agent": {},
    "llm": {},
    "dynamic_variables": {"phone": "+15551234567", "first_name": "John"},
    "metadata": {"lead_id": "L1"},
    "function_secret": "s",
}


def test_resolution_variables_include_call_scoped_keys() -> None:
    cfg = CallConfig.from_dict(CONFIG_DICT)
    variables = cfg.resolution_variables()
    assert variables["call.call_id"] == "call_abc123"
    assert variables["call.direction"] == "outbound"
    assert variables["call.from_number"] == "+19499195585"
    assert variables["call.to_number"] == "+15551234567"
    # user-provided dynamic variables still present, unrenamed
    assert variables["phone"] == "+15551234567"
    assert variables["first_name"] == "John"


def test_resolution_variables_expose_retell_system_names() -> None:
    cfg = CallConfig.from_dict({**CONFIG_DICT, "call_type": "phone_call"})
    variables = cfg.resolution_variables()
    assert variables["call_id"] == "call_abc123"
    assert variables["direction"] == "outbound"
    assert variables["call_type"] == "phone_call"
    assert variables["user_number"] == "+15551234567"  # outbound: user == to_number
    assert variables["agent_number"] == "+19499195585"
    assert variables["session_type"] == "voice"
    assert "current_time" in variables


def test_missing_call_type_fails_closed() -> None:
    # Older control planes omit call_type: the phone-vs-web gate cannot
    # decide, so the phone-call-only system variables stay literal.
    cfg = CallConfig.from_dict(CONFIG_DICT)
    assert cfg.call_type == ""
    variables = cfg.resolution_variables()
    for name in ("call_type", "direction", "user_number", "agent_number"):
        assert name not in variables
    assert variables["call_id"] == "call_abc123"  # not phone-gated
    assert variables["call.direction"] == "outbound"  # call.* unaffected


def test_call_scoped_keys_win_over_user_variables() -> None:
    poisoned = dict(CONFIG_DICT)
    poisoned["dynamic_variables"] = {"call.call_id": "stale_id"}
    cfg = CallConfig.from_dict(poisoned)
    # System values are facts about the call — authoritative.
    assert cfg.resolution_variables()["call.call_id"] == "call_abc123"


def test_tool_call_object_shape() -> None:
    cfg = CallConfig.from_dict(CONFIG_DICT)
    call = cfg.tool_call_object()
    assert call == {
        "call_id": "call_abc123",
        "direction": "outbound",
        "from_number": "+19499195585",
        "to_number": "+15551234567",
        "retell_llm_dynamic_variables": {"phone": "+15551234567", "first_name": "John"},
        "metadata": {"lead_id": "L1"},
    }
