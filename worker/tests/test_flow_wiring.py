"""The wiring decisions `main.py` makes, tested without the livekit stack.

`main.py` imports livekit at module scope, so it cannot be imported in the
worker's dev-only CI environment. Every decision the entrypoint makes that can
be expressed over plain data therefore lives in `arhiteq_worker.flow` — which
node installs which tools, which model id the flow named, who opens the call,
how a ``branch`` classifier is asked and how its answer is read back — and is
tested here. The part that genuinely needs livekit (actually constructing those
tools, and mapping the model id onto the Gemini catalogue) is in
`test_flow_entrypoint.py`, which skips itself when the stack is absent.
"""

from arhiteq_worker.config import ConversationFlowConfig
from arhiteq_worker.flow import (
    CLASSIFIER_NO_MATCH,
    TOOL_EXTRACT,
    TOOL_FUNCTION,
    TOOL_KB_LOOKUP,
    TOOL_TRANSITION,
    FlowGraph,
    classifier_prompt,
    flow_model_id,
    match_edge_id,
    node_tool_kinds,
    prompt_edges,
    start_speaker_for,
)

# Real prior-auth fixture node ids (see tests/conftest.py for the fixture).
GET_MEMBER = "node-1773865257897"  # function node, TWO usable success edges
GET_PA_CASES = "node-1773865358553"  # function node, exactly one
WELCOME = "start-node-1773864713478"  # conversation node, three prompt edges
BRANCH = "node-1773864774353"  # the flow's only global node
END = "node-1773865762237"


def _graph(flow: dict) -> FlowGraph:
    return FlowGraph.from_config(ConversationFlowConfig.from_dict(flow))


def _kinds(graph: FlowGraph, node_id: str, *, knowledge_base_ids=()) -> list[str]:
    node = graph.node(node_id)
    return node_tool_kinds(
        node,
        prompt_edges(node, graph.global_nodes),
        knowledge_base_ids=list(knowledge_base_ids),
    )


def _prompt_edge(edge_id: str, prompt: str, destination: str = "n2") -> dict:
    return {
        "id": edge_id,
        "transition_condition": {"type": "prompt", "prompt": prompt},
        "destination_node_id": destination,
    }


# ---------------------------------------------------------------------------
# flow_model_id — a flow's model_choice never reaches a provider unmapped
# ---------------------------------------------------------------------------


def test_flow_model_id_reads_the_fixture_model_choice(prior_auth_flow) -> None:
    flow = ConversationFlowConfig.from_dict(prior_auth_flow)
    # The real fixture names an OpenAI model; extraction is faithful, and
    # main.py is what maps it onto Gemini (test_flow_entrypoint.py).
    assert flow_model_id(flow.model_choice) == "gpt-5.1"


def test_flow_model_id_is_empty_when_the_flow_names_no_model() -> None:
    assert flow_model_id(None) == ""
    assert flow_model_id({}) == ""
    assert flow_model_id({"type": "cascading"}) == ""
    assert flow_model_id({"model": 7}) == ""
    assert flow_model_id("gpt-5.1") == ""  # not a mapping at all


def test_flow_model_id_passes_a_gemini_choice_through() -> None:
    assert flow_model_id({"type": "cascading", "model": "gemini-3.1-flash-lite"}) == (
        "gemini-3.1-flash-lite"
    )


# ---------------------------------------------------------------------------
# start_speaker_for — the start node may override who opens
# ---------------------------------------------------------------------------


def test_start_speaker_falls_back_to_the_flow_default() -> None:
    assert start_speaker_for({"id": "n1"}, "agent") == "agent"
    assert start_speaker_for({"id": "n1"}, "user") == "user"


def test_start_speaker_node_override_wins() -> None:
    assert start_speaker_for({"id": "n1", "start_speaker": "user"}, "agent") == "user"
    assert start_speaker_for({"id": "n1", "start_speaker": "agent"}, "user") == "agent"


def test_start_speaker_ignores_an_unusable_override() -> None:
    # It decides whether the agent opens the call or waits, so anything other
    # than the two documented values is ignored rather than trusted.
    assert start_speaker_for({"start_speaker": "both"}, "agent") == "agent"
    assert start_speaker_for({"start_speaker": None}, "user") == "user"


# ---------------------------------------------------------------------------
# node_tool_kinds — which tools a node installs
# ---------------------------------------------------------------------------


def test_multi_edge_function_node_still_gets_a_transition_tool(prior_auth_flow) -> None:
    """The regression this rule exists for.

    "Get Member" has two usable success edges, so the function tool
    deliberately does NOT auto-advance — the model has to choose. Without a
    transition tool alongside the function tool the node would stall with no
    way forward and the call would die there.
    """
    graph = _graph(prior_auth_flow)
    assert _kinds(graph, GET_MEMBER) == [TOOL_TRANSITION, TOOL_FUNCTION]


def test_single_edge_function_node_gets_no_transition_tool(prior_auth_flow) -> None:
    # "Get Pa Cases" has exactly one usable success edge, so its own tool
    # routes the call. A transition tool beside it would give the model a way
    # to skip the call it is there to make — or to move a second time along a
    # stale edge after the tool already advanced.
    graph = _graph(prior_auth_flow)
    assert _kinds(graph, GET_PA_CASES) == [TOOL_FUNCTION]


def test_extract_node_with_several_edges_gets_a_transition_tool() -> None:
    node = {
        "id": "n1",
        "type": "extract_dynamic_variables",
        "edges": [_prompt_edge("e1", "Got it"), _prompt_edge("e2", "Refused", "n3")],
    }
    edges = prompt_edges(node, [])
    assert node_tool_kinds(node, edges, knowledge_base_ids=[]) == [
        TOOL_TRANSITION,
        TOOL_EXTRACT,
    ]


def test_conversation_node_gets_only_a_transition_tool(prior_auth_flow) -> None:
    graph = _graph(prior_auth_flow)
    assert _kinds(graph, WELCOME) == [TOOL_TRANSITION]


def test_kb_lookup_rides_along_on_conversation_nodes_when_the_flow_has_bases(
    prior_auth_flow,
) -> None:
    graph = _graph(prior_auth_flow)
    assert _kinds(graph, WELCOME, knowledge_base_ids=["kb_1"]) == [
        TOOL_TRANSITION,
        TOOL_KB_LOOKUP,
    ]


def test_kb_lookup_is_not_attached_without_knowledge_bases(prior_auth_flow) -> None:
    graph = _graph(prior_auth_flow)
    assert TOOL_KB_LOOKUP not in _kinds(graph, WELCOME, knowledge_base_ids=[])


def test_kb_lookup_is_attached_to_subagent_nodes_too() -> None:
    node = {"id": "n1", "type": "subagent", "edges": [_prompt_edge("e1", "Done")]}
    kinds = node_tool_kinds(node, prompt_edges(node, []), knowledge_base_ids=["kb_1"])
    assert kinds == [TOOL_TRANSITION, TOOL_KB_LOOKUP]


def test_action_nodes_are_not_given_a_competing_kb_tool(prior_auth_flow) -> None:
    # A function node's job is its own tool; a kb_lookup beside it would only
    # give the model something else to call.
    graph = _graph(prior_auth_flow)
    assert TOOL_KB_LOOKUP not in _kinds(graph, GET_MEMBER, knowledge_base_ids=["kb_1"])


def test_a_node_with_nothing_to_offer_installs_no_tools() -> None:
    node = {"id": "n1", "type": "conversation"}
    assert node_tool_kinds(node, prompt_edges(node, []), knowledge_base_ids=[]) == []


def test_end_node_is_offered_the_flows_global_escape_hatch(prior_auth_flow) -> None:
    # Every other node carries a synthetic edge to prior_auth's global branch
    # node, which is what a transition tool on an otherwise edgeless node is.
    graph = _graph(prior_auth_flow)
    assert _kinds(graph, END) == [TOOL_TRANSITION]


def test_bare_action_node_with_no_edges_still_gets_its_own_tool() -> None:
    # clara's action nodes carry `edges: []` and only an else_edge back to the
    # start node: that else_edge is where a result routes, so the node
    # auto-advances and needs no transition tool.
    from conftest import load_retell_flow_fixture

    graph = _graph(load_retell_flow_fixture("clara_outbound.json"))
    assert _kinds(graph, "node-1777592692787") == [TOOL_FUNCTION]


# ---------------------------------------------------------------------------
# branch classification — how the one extra LLM call is asked and read
# ---------------------------------------------------------------------------


def test_classifier_prompt_lists_every_option_with_its_condition() -> None:
    edges = [_prompt_edge("e1", "Caller is calling during working hours")]
    prompt = classifier_prompt("Route the caller.", edges)
    assert "Route the caller." in prompt
    assert "e1: Caller is calling during working hours" in prompt
    assert CLASSIFIER_NO_MATCH in prompt


def test_classifier_prompt_survives_an_edge_with_no_condition_text() -> None:
    edges = [{"id": "e1", "transition_condition": {"type": "prompt"}}]
    assert "e1: " in classifier_prompt("", edges)


def test_match_edge_id_takes_an_exact_answer() -> None:
    edges = [_prompt_edge("edge-a", "A"), _prompt_edge("edge-b", "B")]
    assert match_edge_id("edge-b", edges) == "edge-b"
    assert match_edge_id("  edge-b\n", edges) == "edge-b"


def test_match_edge_id_digs_the_id_out_of_prose() -> None:
    edges = [_prompt_edge("edge-a", "A"), _prompt_edge("edge-b", "B")]
    assert match_edge_id('The answer is "edge-a".', edges) == "edge-a"


def test_match_edge_id_prefers_the_longest_match() -> None:
    # One id being a prefix of another must not let the short one win.
    edges = [_prompt_edge("edge", "A"), _prompt_edge("edge-long", "B")]
    assert match_edge_id("I pick edge-long here", edges) == "edge-long"


def test_match_edge_id_returns_none_for_a_no_match_answer() -> None:
    edges = [_prompt_edge("edge-a", "A")]
    assert match_edge_id(CLASSIFIER_NO_MATCH, edges) is None
    assert match_edge_id("", edges) is None
    assert match_edge_id("none of these apply", edges) is None
