"""Lexical knowledge-base retrieval + the internal endpoint behind kb_lookup."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

import arhiteq_api.db as db_module
from arhiteq_api.models import KnowledgeBase, RetellLLM
from arhiteq_api.services import knowledge

from ..conftest import (
    AGENT_ID,
    AUTH_HEADERS,
    FROM_NUMBER,
    INTERNAL_HEADERS,
    LLM_ID,
    OTHER_WORKSPACE_ID,
    WORKSPACE_ID,
)

PRICING = {
    "type": "text",
    "source_id": "src_pricing",
    "title": "Pricing",
    "content": (
        "The USANRetirement plan costs $29 per month with a 14 day free trial.\n\n"
        "Annual billing saves twenty percent."
    ),
}
CRISIS = {
    "type": "text",
    "source_id": "src_crisis",
    "title": "Emergency resources",
    "content": "If you are in crisis, call 988 for the Suicide and Crisis Lifeline.",
}
A_URL = {"type": "url", "source_id": "src_url", "url": "https://example.com/faq"}
A_FILE = {"type": "document", "source_id": "src_doc", "filename": "handbook.pdf"}


def _kb(*sources, kb_id: str = "know_test000000001", name: str = "USAN") -> SimpleNamespace:
    return SimpleNamespace(knowledge_base_id=kb_id, knowledge_base_name=name, sources=list(sources))


# --- chunking / tokenizing -------------------------------------------------


def test_query_terms_drops_stopwords_but_never_empties() -> None:
    assert knowledge.query_terms("how much does the plan cost") == ["much", "plan", "cost"]
    # An all-stopword question still has to rank something rather than nothing.
    assert knowledge.query_terms("who are you") == ["who", "are", "you"]


def test_plural_folding_matches_singular_queries() -> None:
    assert knowledge.tokenize("the plan costs money") == ["the", "plan", "cost", "money"]
    # Not folded: too short to be a safe plural, and a genuine double-s ending.
    assert knowledge.tokenize("is business") == ["is", "business"]
    found = knowledge.search([_kb(PRICING)], "what is the cost")
    assert found["results"], "a query for 'cost' must reach a source that says 'costs'"


def test_stopwords_are_matched_before_plural_folding() -> None:
    """ "this"/"does" fold to "thi"/"doe", which no stopword list contains."""
    assert "thi" not in knowledge.query_terms("what does this plan cover")
    assert "doe" not in knowledge.query_terms("what does this plan cover")
    assert knowledge.query_terms("what does this plan cover") == ["plan", "cover"]


def test_a_term_in_every_chunk_still_returns_hits() -> None:
    """Near-zero IDF must not sink every result.

    A single company's knowledge base repeats its own vocabulary, so the words
    a caller asks with are often in every chunk. An absolute score floor
    reported "nothing found" for exactly those questions.
    """
    sources = [
        {
            "type": "text",
            "source_id": f"src_{i}",
            "title": f"Plan detail {i}",
            "content": f"The plan covers benefit {i}.",
        }
        for i in range(6)
    ]
    found = knowledge.search([_kb(*sources)], "what does the plan cover")
    assert found["results"], "every-chunk terms must still produce hits"


def test_a_weak_incidental_match_is_dropped_beside_a_strong_one() -> None:
    strong = {
        "type": "text",
        "source_id": "src_strong",
        "title": "Refund policy",
        "content": "Refunds are issued within five business days of a cancellation request.",
    }
    weak = {
        "type": "text",
        "source_id": "src_weak",
        "title": "Office hours",
        "content": "Our office is open five days a week.",
    }
    found = knowledge.search([_kb(strong, weak)], "how do refund cancellation work")
    assert [r["source_id"] for r in found["results"]] == ["src_strong"]


def test_split_text_packs_paragraphs_and_overlaps() -> None:
    paragraphs = [f"paragraph {i} " + " ".join(["word"] * 50) for i in range(4)]
    chunks = knowledge.split_text("\n\n".join(paragraphs))
    assert len(chunks) > 1
    assert all(chunk.strip() for chunk in chunks)
    # Every paragraph survives chunking somewhere.
    joined = " ".join(chunks)
    for i in range(4):
        assert f"paragraph {i}" in joined
    # Consecutive chunks share a tail so a fact on the boundary stays findable.
    assert set(chunks[0].split()) & set(chunks[1].split())


def test_split_text_ignores_blank_content() -> None:
    assert knowledge.split_text("") == []
    assert knowledge.split_text("   \n\n  \n ") == []


def test_chunk_sources_reports_unsearchable_types() -> None:
    chunks, skipped = knowledge.chunk_sources("know_1", "USAN", [PRICING, A_URL, A_FILE, "junk"])
    assert [c.source_id for c in chunks] == ["src_pricing"]
    assert {s["type"] for s in skipped} == {"url", "document"}
    assert {s["title"] for s in skipped} == {"https://example.com/faq", "handbook.pdf"}


# --- ranking ---------------------------------------------------------------


def test_search_finds_the_relevant_source() -> None:
    found = knowledge.search([_kb(PRICING, CRISIS)], "how much does the plan cost")
    assert [r["title"] for r in found["results"]] == ["Pricing"]
    assert "$29 per month" in found["results"][0]["content"]
    assert found["results"][0]["source_id"] == "src_pricing"


def test_search_returns_nothing_for_an_unrelated_question() -> None:
    found = knowledge.search([_kb(PRICING, CRISIS)], "zebra migration patterns")
    assert found["results"] == []


def test_search_ranks_by_relevance_not_order() -> None:
    found = knowledge.search([_kb(PRICING, CRISIS)], "crisis lifeline number")
    assert found["results"][0]["title"] == "Emergency resources"


def test_idf_stays_positive_on_a_tiny_corpus() -> None:
    """A term in most chunks must still rank its chunks above the rest.

    The classic BM25 IDF goes negative once a term appears in more than half
    the corpus — on a handful of chunks that inverts the ranking.
    """
    common = [
        {
            "type": "text",
            "source_id": f"src_{i}",
            "title": f"Doc {i}",
            "content": f"medication reminder detail number {i}",
        }
        for i in range(3)
    ]
    unrelated = {
        "type": "text",
        "source_id": "src_other",
        "title": "Other",
        "content": "unrelated content about billing",
    }
    ranked = knowledge.rank_chunks(
        knowledge.chunk_sources("know_1", "USAN", [*common, unrelated])[0], "medication"
    )
    assert ranked, "a term present in most chunks must still score above zero"
    assert all(score > 0 for _, score in ranked)
    assert "Other" not in [chunk.title for chunk, _ in ranked]


def test_category_boosts_without_excluding() -> None:
    tagged = {
        "type": "text",
        "source_id": "src_a",
        "title": "Product terms",
        "content": "The product plan renews monthly.",
    }
    untagged = {
        "type": "text",
        "source_id": "src_b",
        "title": "Notes",
        "content": "The plan renews monthly.",
    }
    boosted = knowledge.search([_kb(tagged, untagged)], "plan renewal", category="product")
    assert boosted["results"][0]["source_id"] == "src_a"
    # A category no source carries must not filter everything out.
    unmatched = knowledge.search([_kb(tagged, untagged)], "plan renewal", category="emergency")
    assert len(unmatched["results"]) == 2


def test_title_alone_can_match() -> None:
    source = {
        "type": "text",
        "source_id": "src_t",
        "title": "Refunds",
        "content": "We process these within five business days.",
    }
    found = knowledge.search([_kb(source)], "refunds")
    assert found["results"][0]["source_id"] == "src_t"


def test_top_k_is_clamped() -> None:
    sources = [
        {"type": "text", "source_id": f"s{i}", "title": f"T{i}", "content": "shared keyword here"}
        for i in range(12)
    ]
    assert len(knowledge.search([_kb(*sources)], "keyword", top_k=99)["results"]) == (
        knowledge.MAX_TOP_K
    )
    # 0 is "unset" on the wire (the endpoint reads it the same way), not "none".
    assert len(knowledge.search([_kb(*sources)], "keyword", top_k=0)["results"]) == (
        knowledge.DEFAULT_TOP_K
    )
    assert len(knowledge.search([_kb(*sources)], "keyword", top_k=-5)["results"]) == 1
    assert len(knowledge.search([_kb(*sources)], "keyword")["results"]) == (knowledge.DEFAULT_TOP_K)


def test_snippet_is_capped() -> None:
    source = {
        "type": "text",
        "source_id": "src_long",
        "title": "Long",
        "content": "keyword " + "filler " * 5000,
    }
    found = knowledge.search([_kb(source)], "keyword")
    assert len(found["results"][0]["content"]) <= knowledge.MAX_SNIPPET_CHARS + 1


def test_search_spans_multiple_knowledge_bases() -> None:
    found = knowledge.search(
        [_kb(PRICING, kb_id="know_a", name="A"), _kb(CRISIS, kb_id="know_b", name="B")],
        "crisis lifeline",
    )
    assert found["results"][0]["knowledge_base_id"] == "know_b"
    assert found["results"][0]["knowledge_base_name"] == "B"


def test_search_handles_an_empty_workspace() -> None:
    assert knowledge.search([], "anything")["results"] == []
    assert knowledge.search([_kb()], "anything")["results"] == []


def test_knowledge_base_view_detaches_a_row() -> None:
    view = knowledge.KnowledgeBaseView.of(_kb(PRICING))
    assert view.knowledge_base_id == "know_test000000001"
    assert knowledge.search([view], "monthly cost")["results"]


# --- the internal endpoint -------------------------------------------------


async def _seed_kb(sources: list[dict], *, workspace_id: str = WORKSPACE_ID) -> str:
    kb_id = f"know_{workspace_id[-8:]}0000000"
    async with db_module.session_factory()() as session:
        session.add(
            KnowledgeBase(
                knowledge_base_id=kb_id,
                workspace_id=workspace_id,
                knowledge_base_name="Company KB",
                sources=sources,
            )
        )
        await session.commit()
    return kb_id


async def _attach_to_llm(kb_ids: list[str]) -> None:
    async with db_module.session_factory()() as session:
        llm = await session.scalar(select(RetellLLM).where(RetellLLM.llm_id == LLM_ID))
        llm.knowledge_base_ids = kb_ids
        await session.commit()


async def _make_call(client) -> str:
    resp = await client.post(
        "/v2/create-phone-call",
        json={
            "from_number": FROM_NUMBER,
            "to_number": "+15551230000",
            "override_agent_id": AGENT_ID,
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["call_id"]


@pytest.fixture
async def call_id(client) -> str:
    return await _make_call(client)


async def test_query_endpoint_returns_matching_snippets(client, call_id) -> None:
    kb_id = await _seed_kb([PRICING, CRISIS])
    resp = await client.post(
        f"/internal/calls/{call_id}/knowledge-base/query",
        json={"query": "how much per month", "knowledge_base_ids": [kb_id]},
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["results"][0]["title"] == "Pricing"
    assert "$29" in body["results"][0]["content"]


async def test_query_falls_back_to_the_llms_attached_bases(client) -> None:
    kb_id = await _seed_kb([CRISIS])
    await _attach_to_llm([kb_id])
    call_id = await _make_call(client)
    resp = await client.post(
        f"/internal/calls/{call_id}/knowledge-base/query",
        json={"query": "crisis lifeline"},
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["title"] == "Emergency resources"


async def test_query_without_any_attached_base_is_empty(client, call_id) -> None:
    resp = await client.post(
        f"/internal/calls/{call_id}/knowledge-base/query",
        json={"query": "anything"},
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["results"] == []


async def test_query_cannot_reach_another_workspaces_base(client, call_id, other_workspace) -> None:
    """The requested ids come from user-editable tool config — the call's
    workspace, not the request, decides what is readable."""
    foreign = await _seed_kb([PRICING], workspace_id=OTHER_WORKSPACE_ID)
    resp = await client.post(
        f"/internal/calls/{call_id}/knowledge-base/query",
        json={"query": "how much per month", "knowledge_base_ids": [foreign]},
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["results"] == []


async def test_query_requires_the_internal_token(client, call_id) -> None:
    resp = await client.post(
        f"/internal/calls/{call_id}/knowledge-base/query", json={"query": "hello"}
    )
    assert resp.status_code in (401, 403)


async def test_query_for_an_unknown_call_is_404(client) -> None:
    resp = await client.post(
        "/internal/calls/call_does_not_exist/knowledge-base/query",
        json={"query": "hello"},
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 404
