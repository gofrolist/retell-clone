"""Simulation testing endpoints (Retell `/…-test-case-definition`, `/…-batch-test`).

Test cases hang off a *response engine* (a Retell LLM or a conversation flow),
not off an agent — same as Retell, so cases follow the prompt they were written
for even when agents are duplicated off it.

`POST /generate-test-case-definitions` is an Arhiteq extra (additive, so the
Retell contract is untouched): it drafts cases from the agent's own prompt and
tool catalog instead of making the operator write them by hand.
"""

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session, session_factory
from ..ids import new_test_case_batch_job_id, new_test_case_job_id
from ..models import (
    Agent,
    ApiKey,
    ConversationFlow,
    RetellLLM,
    TestCaseBatchJob,
    TestCaseDefinition,
    TestCaseJob,
    now_ms,
)
from ..schemas import CompatModel, ResponseEngine, coerce_dynamic_variables
from ..services import simulation
from ._deps import apply_keyset_page, get_owned

log = logging.getLogger(__name__)
router = APIRouter(tags=["tests"])

# Retell caps a batch at 1000 cases.
MAX_BATCH_TEST_CASES = 1000
# Cases one generate call may draft. Kept modest: each is a full LLM-written
# scenario and the operator has to read every one of them.
MAX_GENERATED_CASES = 10


class TestCaseDefinitionRequest(CompatModel):
    name: str = "Untitled test"
    response_engine: ResponseEngine
    user_prompt: str = ""
    metrics: list[str] = Field(default_factory=list)
    dynamic_variables: dict[str, Any] | None = None
    tool_mocks: list[dict[str, Any]] = Field(default_factory=list)
    llm_model: str | None = None


class UpdateTestCaseDefinitionRequest(CompatModel):
    name: str | None = None
    response_engine: ResponseEngine | None = None
    user_prompt: str | None = None
    metrics: list[str] | None = None
    dynamic_variables: dict[str, Any] | None = None
    tool_mocks: list[dict[str, Any]] | None = None
    llm_model: str | None = None


class CreateBatchTestRequest(CompatModel):
    test_case_definition_ids: list[str]
    response_engine: ResponseEngine
    # Arhiteq extra: lets the dashboard list a batch under the agent it was
    # launched from (Retell scopes only by response engine).
    agent_id: str | None = None


class GenerateTestCasesRequest(CompatModel):
    agent_id: str | None = None
    llm_id: str | None = None
    count: int = 4
    # Persist the drafts straight away; false returns them for review only.
    save: bool = True


def _definition_to_dict(row: TestCaseDefinition) -> dict[str, Any]:
    return {
        "test_case_definition_id": row.test_case_definition_id,
        "type": "simulation",
        "name": row.name,
        "response_engine": _engine_to_dict(row),
        "user_prompt": row.user_prompt,
        "metrics": list(row.metrics or []),
        "dynamic_variables": dict(row.dynamic_variables or {}),
        "tool_mocks": list(row.tool_mocks or []),
        "llm_model": row.llm_model,
        # Arhiteq extra: `manual` | `generated`.
        "source": row.source,
        "creation_timestamp": row.creation_timestamp,
        "user_modified_timestamp": row.user_modified_timestamp,
    }


def _engine_to_dict(row: TestCaseDefinition | TestCaseBatchJob) -> dict[str, Any]:
    engine: dict[str, Any] = {"type": row.engine_type}
    if row.engine_type == "conversation-flow":
        engine["conversation_flow_id"] = row.conversation_flow_id
    else:
        engine["llm_id"] = row.llm_id
    if row.engine_version is not None:
        engine["version"] = row.engine_version
    return engine


def _batch_to_dict(row: TestCaseBatchJob) -> dict[str, Any]:
    return {
        "test_case_batch_job_id": row.test_case_batch_job_id,
        "status": row.status,
        "response_engine": _engine_to_dict(row),
        "pass_count": row.pass_count,
        "fail_count": row.fail_count,
        "error_count": row.error_count,
        "total_count": row.total_count,
        "agent_id": row.agent_id,  # Arhiteq extra
        "creation_timestamp": row.creation_timestamp,
        "user_modified_timestamp": row.user_modified_timestamp,
    }


def _run_to_dict(row: TestCaseJob) -> dict[str, Any]:
    return {
        "test_case_job_id": row.test_case_job_id,
        "status": row.status,
        "test_case_batch_job_id": row.test_case_batch_job_id,
        "test_case_definition_id": row.test_case_definition_id,
        "test_case_definition_snapshot": row.test_case_definition_snapshot or {},
        "transcript_snapshot": row.transcript_snapshot,
        "result_explanation": row.result_explanation,
        # Arhiteq extra: per-criterion verdicts behind result_explanation.
        "metric_results": list(row.metric_results or []),
        "creation_timestamp": row.creation_timestamp,
        "user_modified_timestamp": row.user_modified_timestamp,
    }


async def _resolve_engine(
    session: AsyncSession, engine: ResponseEngine, workspace_id: str
) -> dict[str, Any]:
    """Validate a response engine and flatten it into column values.

    Custom LLMs are rejected: a simulation replays the agent's prompt itself, so
    there is nothing to run when the prompt lives on someone else's server.
    """
    if engine.type == "custom-llm":
        raise HTTPException(422, detail="Custom LLM response engines are not supported for tests")
    if engine.type == "conversation-flow":
        if not engine.conversation_flow_id:
            raise HTTPException(422, detail="conversation_flow_id is required")
        await get_owned(
            session,
            ConversationFlow,
            engine.conversation_flow_id,
            workspace_id,
            detail=f"conversation flow {engine.conversation_flow_id} not found",
            status=422,
        )
        return {
            "engine_type": "conversation-flow",
            "conversation_flow_id": engine.conversation_flow_id,
            "llm_id": None,
            "engine_version": engine.version,
        }
    if not engine.llm_id:
        raise HTTPException(422, detail="llm_id is required")
    await get_owned(
        session,
        RetellLLM,
        engine.llm_id,
        workspace_id,
        detail=f"llm {engine.llm_id} not found",
        status=422,
    )
    return {
        "engine_type": "retell-llm",
        "llm_id": engine.llm_id,
        "conversation_flow_id": None,
        "engine_version": engine.version,
    }


def _engine_filter(
    query: Any,
    model: type[Any],
    engine_type: str,
    llm_id: str | None,
    conversation_flow_id: str | None,
) -> Any:
    if engine_type == "conversation-flow":
        if not conversation_flow_id:
            raise HTTPException(
                422, detail="conversation_flow_id is required when type is conversation-flow"
            )
        return query.where(model.conversation_flow_id == conversation_flow_id)
    if not llm_id:
        raise HTTPException(422, detail="llm_id is required when type is retell-llm")
    return query.where(model.llm_id == llm_id)


# --------------------------------------------------------------- definitions


@router.post("/create-test-case-definition", status_code=201)
async def create_test_case_definition(
    body: TestCaseDefinitionRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    engine = await _resolve_engine(session, body.response_engine, api_key.workspace_id)
    row = TestCaseDefinition(
        workspace_id=api_key.workspace_id,
        name=body.name,
        user_prompt=body.user_prompt,
        metrics=list(body.metrics),
        dynamic_variables=coerce_dynamic_variables(body.dynamic_variables),
        tool_mocks=list(body.tool_mocks),
        llm_model=body.llm_model,
        **engine,
    )
    session.add(row)
    await session.commit()
    return _definition_to_dict(row)


@router.get("/get-test-case-definition/{test_case_definition_id}")
async def get_test_case_definition(
    test_case_definition_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    row = await get_owned(
        session,
        TestCaseDefinition,
        test_case_definition_id,
        api_key.workspace_id,
        detail="Test case definition not found",
    )
    return _definition_to_dict(row)


@router.put("/update-test-case-definition/{test_case_definition_id}")
async def update_test_case_definition(
    test_case_definition_id: str,
    body: UpdateTestCaseDefinitionRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    row = await get_owned(
        session,
        TestCaseDefinition,
        test_case_definition_id,
        api_key.workspace_id,
        detail="Test case definition not found",
    )
    if body.response_engine is not None:
        for field, value in (
            await _resolve_engine(session, body.response_engine, api_key.workspace_id)
        ).items():
            setattr(row, field, value)
    if body.name is not None:
        row.name = body.name
    if body.user_prompt is not None:
        row.user_prompt = body.user_prompt
    if body.metrics is not None:
        row.metrics = list(body.metrics)
    if body.dynamic_variables is not None:
        row.dynamic_variables = coerce_dynamic_variables(body.dynamic_variables)
    if body.tool_mocks is not None:
        row.tool_mocks = list(body.tool_mocks)
    if body.llm_model is not None:
        row.llm_model = body.llm_model
    row.user_modified_timestamp = now_ms()
    await session.commit()
    return _definition_to_dict(row)


@router.delete("/delete-test-case-definition/{test_case_definition_id}", status_code=204)
async def delete_test_case_definition(
    test_case_definition_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    row = await get_owned(
        session,
        TestCaseDefinition,
        test_case_definition_id,
        api_key.workspace_id,
        detail="Test case definition not found",
    )
    await session.delete(row)
    await session.commit()


@router.get("/v2/list-test-case-definitions")
async def list_test_case_definitions(
    type: Literal["retell-llm", "conversation-flow"] = Query("retell-llm"),
    llm_id: str | None = None,
    conversation_flow_id: str | None = None,
    limit: int = Query(50, ge=1, le=1000),
    pagination_key: str | None = None,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    query = _engine_filter(
        select(TestCaseDefinition).where(TestCaseDefinition.workspace_id == api_key.workspace_id),
        TestCaseDefinition,
        type,
        llm_id,
        conversation_flow_id,
    )
    query = await apply_keyset_page(
        session,
        query,
        TestCaseDefinition,
        TestCaseDefinition.creation_timestamp,
        TestCaseDefinition.test_case_definition_id,
        pagination_key=pagination_key,
        ascending=False,
    )
    rows = (await session.scalars(query.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [_definition_to_dict(r) for r in rows],
        "has_more": has_more,
        "pagination_key": rows[-1].test_case_definition_id if has_more and rows else None,
    }


# ------------------------------------------------------------- batch + runs


@router.post("/create-batch-test", status_code=201)
async def create_batch_test(
    body: CreateBatchTestRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    if not body.test_case_definition_ids:
        raise HTTPException(422, detail="test_case_definition_ids must not be empty")
    if len(body.test_case_definition_ids) > MAX_BATCH_TEST_CASES:
        raise HTTPException(422, detail=f"At most {MAX_BATCH_TEST_CASES} test cases per batch")
    engine = await _resolve_engine(session, body.response_engine, api_key.workspace_id)
    if body.agent_id:
        await get_owned(
            session,
            Agent,
            body.agent_id,
            api_key.workspace_id,
            detail=f"agent {body.agent_id} not found",
            status=422,
        )

    definitions = []
    for definition_id in body.test_case_definition_ids:
        definitions.append(
            await get_owned(
                session,
                TestCaseDefinition,
                definition_id,
                api_key.workspace_id,
                detail=f"test case definition {definition_id} not found",
                status=422,
            )
        )

    # Explicit id: the column default only fires on flush, and the jobs below
    # need the batch id before then.
    batch = TestCaseBatchJob(
        test_case_batch_job_id=new_test_case_batch_job_id(),
        workspace_id=api_key.workspace_id,
        agent_id=body.agent_id,
        total_count=len(definitions),
        **engine,
    )
    session.add(batch)
    jobs = [
        TestCaseJob(
            test_case_job_id=new_test_case_job_id(),
            workspace_id=api_key.workspace_id,
            test_case_batch_job_id=batch.test_case_batch_job_id,
            test_case_definition_id=d.test_case_definition_id,
            test_case_definition_snapshot=simulation.definition_snapshot(d),
        )
        for d in definitions
    ]
    session.add_all(jobs)
    await session.commit()

    # Runs are LLM round-trips; the caller polls get-batch-test / list-test-runs.
    simulation.spawn_batch(
        session_factory(), batch.test_case_batch_job_id, [j.test_case_job_id for j in jobs]
    )
    return _batch_to_dict(batch)


@router.get("/get-batch-test/{test_case_batch_job_id}")
async def get_batch_test(
    test_case_batch_job_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    batch = await get_owned(
        session,
        TestCaseBatchJob,
        test_case_batch_job_id,
        api_key.workspace_id,
        detail="Batch test not found",
    )
    return _batch_to_dict(batch)


@router.get("/v2/list-batch-tests")
async def list_batch_tests(
    type: Literal["retell-llm", "conversation-flow"] = Query("retell-llm"),
    llm_id: str | None = None,
    conversation_flow_id: str | None = None,
    limit: int = Query(50, ge=1, le=1000),
    pagination_key: str | None = None,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    query = _engine_filter(
        select(TestCaseBatchJob).where(TestCaseBatchJob.workspace_id == api_key.workspace_id),
        TestCaseBatchJob,
        type,
        llm_id,
        conversation_flow_id,
    )
    query = await apply_keyset_page(
        session,
        query,
        TestCaseBatchJob,
        TestCaseBatchJob.creation_timestamp,
        TestCaseBatchJob.test_case_batch_job_id,
        pagination_key=pagination_key,
        ascending=False,
    )
    rows = (await session.scalars(query.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [_batch_to_dict(r) for r in rows],
        "has_more": has_more,
        "pagination_key": rows[-1].test_case_batch_job_id if has_more and rows else None,
    }


@router.get("/get-test-run/{test_case_job_id}")
async def get_test_run(
    test_case_job_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    job = await get_owned(
        session,
        TestCaseJob,
        test_case_job_id,
        api_key.workspace_id,
        detail="Test run not found",
    )
    return _run_to_dict(job)


@router.get("/v2/list-test-runs/{test_case_batch_job_id}")
async def list_test_runs(
    test_case_batch_job_id: str,
    limit: int = Query(50, ge=1, le=1000),
    pagination_key: str | None = None,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    # 404s a batch in another workspace before leaking whether it exists.
    await get_owned(
        session,
        TestCaseBatchJob,
        test_case_batch_job_id,
        api_key.workspace_id,
        detail="Batch test not found",
    )
    query = select(TestCaseJob).where(
        TestCaseJob.workspace_id == api_key.workspace_id,
        TestCaseJob.test_case_batch_job_id == test_case_batch_job_id,
    )
    query = await apply_keyset_page(
        session,
        query,
        TestCaseJob,
        TestCaseJob.creation_timestamp,
        TestCaseJob.test_case_job_id,
        pagination_key=pagination_key,
        ascending=True,
    )
    rows = (await session.scalars(query.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [_run_to_dict(r) for r in rows],
        "has_more": has_more,
        "pagination_key": rows[-1].test_case_job_id if has_more and rows else None,
    }


# ------------------------------------------------- generation (Arhiteq extra)


@router.post("/generate-test-case-definitions", status_code=201)
async def generate_test_case_definitions(
    body: GenerateTestCasesRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    """Draft test cases from an agent's own prompt and tools.

    Either `agent_id` (its response engine is used) or `llm_id` identifies the
    prompt under test.
    """
    llm_id = body.llm_id
    if not llm_id:
        if not body.agent_id:
            raise HTTPException(422, detail="agent_id or llm_id is required")
        agent = await get_owned(
            session,
            Agent,
            body.agent_id,
            api_key.workspace_id,
            detail=f"agent {body.agent_id} not found",
            status=422,
        )
        llm_id = (agent.response_engine or {}).get("llm_id")
        if not llm_id:
            raise HTTPException(
                422,
                detail="Test generation needs a Retell LLM agent (conversation flows are not supported yet)",
            )
    llm = await get_owned(
        session,
        RetellLLM,
        llm_id,
        api_key.workspace_id,
        detail=f"llm {llm_id} not found",
        status=422,
    )
    if not (llm.general_prompt or "").strip():
        raise HTTPException(422, detail="This agent has no prompt to generate tests from")

    count = max(1, min(body.count, MAX_GENERATED_CASES))
    try:
        drafts = await simulation.generate_test_cases(llm, count)
    except Exception as exc:
        log.exception("test-case generation failed for %s", llm_id)
        raise HTTPException(502, detail=f"Test-case generation failed: {exc}") from exc

    if not body.save:
        return {"items": drafts, "saved": False}

    rows = [
        TestCaseDefinition(
            workspace_id=api_key.workspace_id,
            engine_type="retell-llm",
            llm_id=llm.llm_id,
            name=d["name"],
            user_prompt=d["user_prompt"],
            metrics=d["metrics"],
            tool_mocks=d["tool_mocks"],
            source="generated",
        )
        for d in drafts
    ]
    session.add_all(rows)
    await session.commit()
    return {"items": [_definition_to_dict(r) for r in rows], "saved": True}
