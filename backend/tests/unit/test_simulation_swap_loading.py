"""Loading the agents a simulated call can be handed to.

A router that swaps to a specialist is one call in production, so a run has to
carry the whole chain. These cover which destinations load and — more
importantly — which ones must not.
"""

import arhiteq_api.db as db_module
from arhiteq_api.models import Agent, RetellLLM
from arhiteq_api.services import simulation
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
