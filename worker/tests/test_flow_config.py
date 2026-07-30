"""Conversation-flow config parsing (no livekit stack)."""

from arhiteq_worker.config import CallConfig


def test_single_prompt_call_has_no_flow() -> None:
    cfg = CallConfig.from_dict({"call_id": "c1", "llm": {"general_prompt": "hi"}})
    assert cfg.conversation_flow is None


def test_flow_is_parsed_when_present(prior_auth_flow) -> None:
    cfg = CallConfig.from_dict({"call_id": "c1", "conversation_flow": prior_auth_flow})
    flow = cfg.conversation_flow
    assert flow is not None
    assert flow.start_node_id == prior_auth_flow["start_node_id"]
    assert len(flow.nodes) == len(prior_auth_flow["nodes"])
    assert flow.global_prompt == prior_auth_flow["global_prompt"]
    assert flow.tools == prior_auth_flow["tools"]
    assert flow.components == prior_auth_flow["components"]
    # Unknown/extra keys survive on raw, same contract as the other configs.
    assert flow.raw == prior_auth_flow


def test_flow_tolerates_a_minimal_object() -> None:
    cfg = CallConfig.from_dict({"call_id": "c1", "conversation_flow": {"nodes": []}})
    flow = cfg.conversation_flow
    assert flow is not None
    assert flow.nodes == []
    assert flow.start_node_id == ""
    assert flow.start_speaker == "agent"
    assert flow.tools == []
    assert flow.components == []
