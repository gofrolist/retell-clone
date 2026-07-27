"""Lexical knowledge-base retrieval + the internal endpoint behind kb_lookup."""

import logging
import time
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


def test_a_hit_always_contains_what_matched() -> None:
    """A snippet that omits the match is worse than no hit at all.

    An FAQ pasted with single newlines is one paragraph. When that was left
    whole, the chunk ran past MAX_SNIPPET_CHARS and the snippet was its
    opening — so a question answered on "page three" came back as a confident
    hit whose text does not mention it, and the agent answered from the wrong
    section instead of being told to say it doesn't know.
    """
    lines = [f"Q{i}: question {i}? A{i}: filler answer text goes here." for i in range(120)]
    lines.insert(100, "Q: What is the refund policy? A: Refunds are issued within five days.")
    found = knowledge.search(
        [
            _kb(
                {
                    "type": "text",
                    "source_id": "src_faq",
                    "title": "FAQ",
                    "content": "\n".join(lines),
                }
            )
        ],
        "what is the refund policy",
    )

    assert found["results"], "the answer is in the source; it must be findable"
    assert "refund" in found["results"][0]["content"].lower()


def test_a_long_unbroken_source_is_chunked() -> None:
    one_paragraph = " ".join(f"word{i}" for i in range(2000))
    chunks = knowledge.split_text(one_paragraph)
    assert len(chunks) > 1
    # Bounded, not exactly TARGET: re-packing prepends the previous chunk's
    # overlap tail, so a chunk tops out at target + overlap.
    ceiling = knowledge.CHUNK_TARGET_WORDS + knowledge.CHUNK_OVERLAP_WORDS
    assert all(len(c.split()) <= ceiling for c in chunks)
    # Nothing is lost: the first and last words both survive the windowing.
    joined = " ".join(chunks)
    assert "word0" in joined
    assert "word1999" in joined


def test_snippet_windows_around_the_match() -> None:
    text = ("filler " * 400) + "the refund policy is five days " + ("tail " * 400)
    snippet = knowledge._snippet(text, ["refund"])
    assert len(snippet) <= knowledge.MAX_SNIPPET_CHARS + 2  # +2 for the ellipses
    assert "refund policy is five days" in snippet


def test_document_frequency_is_computed_once_per_term() -> None:
    """Ranking must not be quadratic in the number of chunks.

    Recomputing df inside the per-chunk loop took ~60s on a few thousand
    chunks — synchronously, inside an async handler, so it blocked the whole
    API process and blew past the worker's 10s lookup timeout.
    """
    content = "\n\n".join(
        f"section {i} " + " ".join(["medication billing coverage detail"] * 30) for i in range(2000)
    )
    chunks, _ = knowledge.chunk_sources(
        "k", "n", [{"type": "text", "source_id": "s", "title": "T", "content": content}]
    )
    assert len(chunks) > 3000
    start = time.perf_counter()
    ranked = knowledge.rank_chunks(chunks, "what does the medication billing plan cover")
    elapsed = time.perf_counter() - start
    assert ranked
    # ~0.3s once df is hoisted, ~18s when it is not. 5s sits far enough from
    # both to fail loudly on a regression without flaking on a slow CI box.
    assert elapsed < 5.0, f"ranking {len(chunks)} chunks took {elapsed:.1f}s"


def test_search_warns_when_only_unsearchable_sources_exist(caplog) -> None:
    """ "Attach our handbook" builds a KB of PDFs that silently answers nothing."""
    with caplog.at_level(logging.WARNING):
        found = knowledge.search([_kb(A_URL, A_FILE)], "what is the refund policy")
    assert found["results"] == []
    assert "not searchable" in caplog.text


def test_split_text_ignores_blank_content() -> None:
    assert knowledge.split_text("") == []
    assert knowledge.split_text("   \n\n  \n ") == []


def test_chunk_sources_reports_unsearchable_types() -> None:
    chunks, skipped = knowledge.chunk_sources("know_1", "USAN", [PRICING, A_URL, A_FILE, "junk"])
    assert [c.source_id for c in chunks] == ["src_pricing"]
    assert {s["type"] for s in skipped} == {"url", "document"}
    assert {s["title"] for s in skipped} == {"https://example.com/faq", "handbook.pdf"}


# --- uploaded documents ----------------------------------------------------

MARKDOWN = b"""---
version: 0.2.0
category: product
nested:
  key: value
---

# Pricing Snapshot

Intro line about the plans.

## Current trial offer

- **7-day free trial:** $0, no card required
- Trial includes daily morning calls

### Fine print

The trial converts unless cancelled.

## Cancellation

Cancel any time by calling us.
"""


def _file(
    source_id="src_doc", filename="pricing_snapshot.md", data=MARKDOWN, ctype="text/markdown"
):
    return knowledge.KnowledgeBaseFileView(
        source_id=source_id, filename=filename, content_type=ctype, data=data
    )


def _doc_kb(*files, sources=None):
    srcs = sources or [
        {"type": "document", "source_id": f.source_id, "filename": f.filename} for f in files
    ]
    return SimpleNamespace(
        knowledge_base_id="know_docs",
        knowledge_base_name="Docs",
        sources=srcs,
        files={f.source_id: f for f in files},
    )


def test_indexable_file_detection() -> None:
    assert knowledge.is_indexable_file("a.md", "text/markdown; charset=utf-8")
    assert knowledge.is_indexable_file("notes.txt", "text/plain")
    # Uploaders routinely send octet-stream for markdown; the suffix decides.
    assert knowledge.is_indexable_file("handbook.md", "application/octet-stream")
    assert not knowledge.is_indexable_file("handbook.pdf", "application/pdf")
    assert not knowledge.is_indexable_file("scan.png", "image/png")


def test_decode_rejects_bytes_that_are_not_text() -> None:
    assert knowledge.decode_file("héllo".encode()) == "héllo"
    assert knowledge.decode_file(b"%PDF-1.4\n\x80\x81\x82") is None
    # A declared text type is a claim, not a fact — a NUL means binary.
    assert knowledge.decode_file(b"text\x00more") is None


def test_frontmatter_is_parsed_and_stripped() -> None:
    fields, body = knowledge.split_frontmatter(MARKDOWN.decode())
    assert fields["category"] == "product"
    assert fields["version"] == "0.2.0"
    assert "nested" in fields  # the scalar line; its indented child is skipped
    assert "key" not in fields
    assert body.lstrip().startswith("# Pricing Snapshot")
    # A document without frontmatter is returned untouched.
    assert knowledge.split_frontmatter("# Title\n\nbody") == ({}, "# Title\n\nbody")


def test_markdown_sections_carry_their_heading_path() -> None:
    _fields, body = knowledge.split_frontmatter(MARKDOWN.decode())
    sections = dict(knowledge.split_markdown_sections(body, "pricing_snapshot.md"))
    assert "pricing_snapshot.md › Pricing Snapshot › Current trial offer" in sections
    assert "pricing_snapshot.md › Pricing Snapshot › Current trial offer › Fine print" in sections
    # Dropping back to ## resets the deeper trail rather than accumulating it.
    assert "pricing_snapshot.md › Pricing Snapshot › Cancellation" in sections
    assert "free trial" in sections["pricing_snapshot.md › Pricing Snapshot › Current trial offer"]


def test_a_markdown_upload_becomes_searchable() -> None:
    kb = _doc_kb(_file())
    found = knowledge.search([kb], "is there a free trial")
    assert found["results"], "an uploaded markdown file must be searchable"
    top = found["results"][0]
    assert "free trial" in top["content"]
    # The heading path is what tells the agent where the answer came from.
    assert "Current trial offer" in top["title"]
    assert found["skipped_sources"] == []


def test_a_pdf_upload_is_skipped_with_a_reason() -> None:
    pdf = _file(
        source_id="src_pdf",
        filename="handbook.pdf",
        data=b"%PDF-1.4\n\x80",
        ctype="application/pdf",
    )
    found = knowledge.search([_doc_kb(pdf)], "anything at all")
    assert found["results"] == []
    assert found["skipped_sources"][0]["reason"] == "application/pdf"


def test_a_mislabelled_binary_is_skipped_rather_than_indexed_as_mojibake() -> None:
    fake = _file(source_id="src_fake", filename="notes.md", data=b"\xff\xfe\x00binary")
    found = knowledge.search([_doc_kb(fake)], "binary")
    assert found["results"] == []
    assert found["skipped_sources"][0]["reason"] == "not valid UTF-8 text"


def test_an_oversized_file_is_skipped() -> None:
    huge = _file(source_id="src_big", filename="big.md", data=b"word " * 900_000)
    assert len(huge.data) > knowledge.MAX_INDEXED_FILE_BYTES
    found = knowledge.search([_doc_kb(huge)], "word")
    assert found["results"] == []
    assert "larger than" in found["skipped_sources"][0]["reason"]


def test_documents_without_loaded_blobs_say_so() -> None:
    """A caller that passed KnowledgeBase rows has no bytes to offer."""
    kb = SimpleNamespace(
        knowledge_base_id="k",
        knowledge_base_name="n",
        sources=[{"type": "document", "source_id": "src_x", "filename": "a.md"}],
    )
    found = knowledge.search([kb], "anything")
    assert found["skipped_sources"][0]["reason"] == "not loaded"


def test_declared_category_beats_a_coincidental_word() -> None:
    """Frontmatter is the source naming its own topic; a substring is a guess."""
    declared = _file(
        source_id="src_declared",
        filename="terms.md",
        data=b"---\ncategory: product\n---\n\n# Terms\n\nThe plan renews monthly.\n",
    )
    coincidence = _file(
        source_id="src_word",
        filename="notes.md",
        data=b"# Notes\n\nThe product plan renews monthly for everyone.\n",
    )
    found = knowledge.search([_doc_kb(declared, coincidence)], "plan renewal", category="product")
    assert found["results"][0]["source_id"] == "src_declared"


def test_a_different_declared_category_is_not_penalised() -> None:
    """The right answer is often filed under a label the caller didn't guess."""
    doc = _file(
        source_id="src_meds",
        filename="meds.md",
        data=b"---\ncategory: meds\n---\n\n# Discounts\n\nPrescription discount cards are free.\n",
    )
    found = knowledge.search([_doc_kb(doc)], "prescription discount card", category="product")
    assert found["results"], "a mismatched category must not filter out the answer"


def test_csv_and_plain_text_uploads_are_indexed() -> None:
    csv = _file(
        source_id="src_csv",
        filename="rates.csv",
        data=b"drug,price\nLipitor,12.40\n",
        ctype="text/csv",
    )
    txt = _file(
        source_id="src_txt",
        filename="hours.txt",
        data=b"Support hours are nine to five.\n",
        ctype="text/plain",
    )
    kb = _doc_kb(csv, txt)
    assert knowledge.search([kb], "Lipitor price")["results"]
    assert knowledge.search([kb], "support hours")["results"]


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


async def test_an_uploaded_markdown_file_is_searchable_end_to_end(client) -> None:
    """The whole path: multipart upload -> attached KB -> kb_lookup query.

    This is how a knowledge base is actually built ("attach our handbook"), and
    it is the shape the production KB has: markdown files, not pasted text.
    """
    resp = await client.post(
        "/create-knowledge-base",
        files={"knowledge_base_files": ("pricing.md", MARKDOWN, "text/markdown")},
        data={"knowledge_base_name": "Handbook"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    kb_id = resp.json()["knowledge_base_id"]
    await _attach_to_llm([kb_id])

    call_id = await _make_call(client)
    resp = await client.post(
        f"/internal/calls/{call_id}/knowledge-base/query",
        json={"query": "is there a free trial", "category": "product"},
        headers=INTERNAL_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["results"], "an uploaded markdown file must be reachable from a call"
    assert "free trial" in body["results"][0]["content"]
    assert "Current trial offer" in body["results"][0]["title"]


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
