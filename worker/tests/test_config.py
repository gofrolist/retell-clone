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
