"""Loading the agents a simulated call can be handed to.

A router that swaps to a specialist is one call in production, so a run has to
carry the whole chain. These cover which destinations load and — more
importantly — which ones must not.
"""

import arhiteq_api.db as db_module
from arhiteq_api.models import Agent, RetellLLM
from arhiteq_api.services import simulation, versions
from tests.conftest import OTHER_WORKSPACE_ID, WORKSPACE_ID


def _swap(name: str, agent_id: str) -> dict:
    return {"type": "agent_swap", "name": name, "agent_id": agent_id}


async def _seed_agent(
    agent_id: str,
    *,
    workspace_id: str = WORKSPACE_ID,
    tools: list | None = None,
    with_llm: bool = True,
    timezone: str | None = None,
) -> RetellLLM | None:
    """An agent plus its LLM, as the dashboard would create them."""
    llm = None
    async with db_module.session_factory()() as session:
        if with_llm:
            llm = RetellLLM(
                llm_id=f"llm_{agent_id}",
                workspace_id=workspace_id,
                general_prompt=f"You are {agent_id}.",
                general_tools=tools or [],
            )
            session.add(llm)
        session.add(
            Agent(
                agent_id=agent_id,
                workspace_id=workspace_id,
                agent_name=agent_id.replace("agent_", "").title(),
                response_engine=(
                    {"type": "retell-llm", "llm_id": f"llm_{agent_id}"} if with_llm else {}
                ),
                voice_id="cartesia-sonic",
                timezone=timezone,
                webhook_url=None,
            )
        )
        await session.commit()
    return llm


async def _load(llm: RetellLLM, workspace_id: str = WORKSPACE_ID) -> dict:
    async with db_module.session_factory()() as session:
        return await simulation._load_swap_destinations(session, llm, workspace_id)


async def test_the_whole_handoff_chain_loads():
    """Router → specialist → sub-specialist: all of it is the same call."""
    await _seed_agent("agent_nurse", timezone="America/New_York")
    await _seed_agent("agent_pharmacy", tools=[_swap("to_nurse", "agent_nurse")])
    router = await _seed_agent("agent_router", tools=[_swap("to_pharmacy", "agent_pharmacy")])

    destinations = await _load(router)

    assert set(destinations) == {"agent_pharmacy", "agent_nurse"}
    assert destinations["agent_pharmacy"]["general_prompt"] == "You are agent_pharmacy."
    assert [t["name"] for t in destinations["agent_pharmacy"]["catalog"]] == ["to_nurse"]
    assert destinations["agent_nurse"]["timezone"] == "America/New_York"
    assert destinations["agent_nurse"]["agent_name"] == "Nurse"


async def test_a_specialist_can_hand_the_call_back_without_looping_forever():
    """The router→specialist→router cycle is the normal shape, not a hang.

    The starting agent is loaded as a destination in its own right here, which
    is what makes the return trip work: after the specialist is done, the run
    has to be able to swap back to the config it started under.
    """
    await _seed_agent("agent_b", tools=[_swap("back", "agent_a")])
    a = await _seed_agent("agent_a", tools=[_swap("over", "agent_b")])

    assert set(await _load(a)) == {"agent_a", "agent_b"}


async def test_another_workspaces_agent_is_not_loaded(other_workspace):
    """The id comes from user-editable tool config.

    An unscoped lookup would pull another tenant's prompt and tool secrets into
    this run — the same reason `GET /internal/agents/{id}/config` takes a
    call_id. A live call 404s here and keeps talking as the current agent.
    """
    await _seed_agent("agent_theirs", workspace_id=OTHER_WORKSPACE_ID)
    router = await _seed_agent("agent_ours", tools=[_swap("steal", "agent_theirs")])

    assert await _load(router) == {}


async def test_a_destination_without_an_llm_is_not_loaded():
    """The worker refuses one — swapping to it would wipe the prompt and tools."""
    await _seed_agent("agent_empty", with_llm=False)
    router = await _seed_agent("agent_r2", tools=[_swap("to_empty", "agent_empty")])

    assert await _load(router) == {}


async def test_an_unknown_destination_is_not_loaded():
    router = await _seed_agent("agent_r3", tools=[_swap("nowhere", "agent_gone")])

    assert await _load(router) == {}


async def test_an_agent_with_no_swap_tools_loads_nothing():
    llm = await _seed_agent("agent_solo", tools=[{"type": "end_call", "name": "end_call"}])

    assert await _load(llm) == {}
    assert await simulation._load_swap_destinations(None, None, WORKSPACE_ID) == {}


async def test_a_destination_is_read_from_the_draft_not_the_published_snapshot():
    """Simulation grades what you are editing, and that has to survive a swap.

    A live call resolves a swap destination to its published version, because
    that is what a real call would have run. A simulated one must not: the whole
    point of the split-and-iterate loop this feature exists for is editing a
    specialist and re-running the suite. Reading Published there would grade the
    edits against text the operator had already replaced — and would read one
    transcript off a draft before the swap and a snapshot after it.
    """
    specialist = await _seed_agent("agent_spec")
    router = await _seed_agent("agent_router2", tools=[_swap("to_spec", "agent_spec")])

    async with db_module.session_factory()() as session:
        agent = await session.get(Agent, "agent_spec")
        await versions.publish(session, agent, agent.version)
        await session.commit()

    # The editor's autosave: the live row moves on, the snapshot stays behind.
    async with db_module.session_factory()() as session:
        live = await session.get(RetellLLM, specialist.llm_id)
        live.general_prompt = "the edit the operator is testing"
        agent = await session.get(Agent, "agent_spec")
        agent.version += 1
        await session.commit()

    destinations = await _load(router)
    assert destinations["agent_spec"]["general_prompt"] == "the edit the operator is testing"


async def test_destinations_sharing_a_knowledge_base_load_it_once():
    """The blobs are capped per lookup, not per run.

    Loading per destination re-reads the same megabytes for every case in a
    batch, times the length of the swap chain.
    """
    calls = []
    real = simulation._load_knowledge_bases

    async def counting(session, llm):
        calls.append(tuple(llm.knowledge_base_ids or []))
        return await real(session, llm)

    for name in ("agent_kb1", "agent_kb2"):
        llm = await _seed_agent(name)
        async with db_module.session_factory()() as session:
            row = await session.get(RetellLLM, llm.llm_id)
            row.knowledge_base_ids = ["know_shared"]
            await session.commit()
    router = await _seed_agent(
        "agent_router3", tools=[_swap("a", "agent_kb1"), _swap("b", "agent_kb2")]
    )

    simulation._load_knowledge_bases = counting
    try:
        destinations = await _load(router)
    finally:
        simulation._load_knowledge_bases = real

    assert set(destinations) == {"agent_kb1", "agent_kb2"}
    assert calls == [("know_shared",)]
    assert (
        destinations["agent_kb1"]["knowledge_bases"] is destinations["agent_kb2"]["knowledge_bases"]
    )
