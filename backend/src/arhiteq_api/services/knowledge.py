"""Knowledge-base retrieval — lexical BM25 over the stored text sources.

Backs the worker's `kb_lookup` tool (docs/RETELL_INTEGRATION_MAP.md Surface 3:
a Retell built-in with no URL, so it resolves against our own knowledge bases
rather than a customer endpoint).

Deliberately dependency-free: sources are chunked, scored with textbook BM25
and returned as snippets. No embeddings, no vector column, no ingestion
pipeline — retrieval is computed from `KnowledgeBase.sources` on each lookup,
which is fast at the size a voice agent's FAQ actually is and keeps the whole
thing deterministic and unit-testable. The cost is recall on pure paraphrase
("what's it cost" against "pricing: $29/mo"); swapping in an embedding ranker
later only needs to replace `rank_chunks`, since chunking and the result shape
live outside it.

Only `type: "text"` sources are indexed today. URL sources store just a URL and
uploaded documents are opaque blobs, so neither has text to search yet;
`search` reports them in `skipped_sources` so the caller can tell "no answer in
the KB" apart from "that source type isn't searchable yet".
"""

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Chunking: sources are split on blank lines and re-packed to roughly this many
# words, so a hit returns a coherent paragraph or two rather than a whole
# document. Overlap keeps a fact that straddles a boundary findable from both
# sides.
CHUNK_TARGET_WORDS = 120
CHUNK_OVERLAP_WORDS = 30
# Cap what a single snippet can feed back to the model — a live call pays for
# every token, and an agent reading from a 20-page chunk is not answering.
MAX_SNIPPET_CHARS = 1200
DEFAULT_TOP_K = 3
MAX_TOP_K = 10
# Weak matches are dropped RELATIVE to the best hit, not against a fixed score.
# An absolute floor breaks on exactly the corpus this serves: when a term
# appears in nearly every chunk of a single company's knowledge base its IDF
# approaches zero, so every genuine hit for "what does the plan cover" scores
# below any fixed cutoff and the tool reports nothing. A chunk that shares no
# query term already scores 0 and never reaches here, so the ratio's only job
# is to drop the chunk that matched one incidental word when a better one
# matched three.
SCORE_FLOOR_RATIO = 0.25
# What the agent is told when nothing matched. Spelling out the fallback keeps a
# miss from reading as "the tool broke" — the model's next move should be to say
# it doesn't know. Mirrored in the worker as tools.KB_LOOKUP_NO_RESULTS, which
# shapes the same result for a live call.
NO_RESULTS_MESSAGE = (
    "No matching information in the knowledge base. Tell the user you don't have "
    "that information rather than guessing."
)

# Textbook BM25 constants (term-frequency saturation, length normalization).
BM25_K1 = 1.5
BM25_B = 0.75
# A `category` argument that matches a chunk's title or body promotes it
# without ever excluding the rest — categories are a caller-side taxonomy
# (the consumer's enum is company/product/emergency/local/meds/faq), not
# something our sources are tagged with, so a hard filter would mostly drop
# the right answer for having the wrong label.
CATEGORY_BOOST = 1.25

_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)
# Dropped from queries only. These carry no signal in a question ("what is the
# cost of the plan") and, on a corpus of a handful of chunks, would otherwise
# out-weigh the words that matter.
# Held as text rather than inlined into the frozenset() call so it stays one
# readable block: ruff's SIM905 rewrites a literal `"...".split()` argument into
# a list, which the formatter then explodes to one word per line.
_STOPWORD_TEXT = """
a about an and any are as at be been but by can could did do does doing for from get
got had has have how i if in into is it its just me my no not of on or our out so some
tell that the their them then there these they this those to us was we were what when
where which who whom why will with would you your
"""
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def _singular(token: str) -> str:
    """Fold a trailing plural 's' so "costs" matches a query for "cost".

    Deliberately only plurals rather than a real stemmer: suffix stripping that
    reaches for -ing/-ed/-ly needs the rewrite rules that come with it ("pricing"
    and "prices" both truncate to "pric", which then matches neither "price" nor
    each other reliably), and getting that wrong silently degrades every query.
    Plural folding is the high-frequency mismatch and it is unambiguous.
    """
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    return [_singular(t) for t in _TOKEN_RE.findall(text.casefold())]


def query_terms(query: str) -> list[str]:
    """Query tokens with stopwords removed — unless that empties the query.

    Stopwords are matched BEFORE plural folding: "this" and "does" fold to
    "thi" and "doe", which are in no stopword list, so filtering afterwards
    would let every one of them back in as a scoring term.

    A caller who asks only "who are you" still deserves a ranking rather than
    silence, so an all-stopword question falls back to its raw tokens.
    """
    raw = _TOKEN_RE.findall(query.casefold())
    meaningful = [t for t in raw if t not in _STOPWORDS]
    return [_singular(t) for t in (meaningful or raw)]


@dataclass(frozen=True, slots=True)
class KnowledgeBaseView:
    """A KnowledgeBase row's searchable fields, detached from the session.

    `search` reads its inputs through getattr, so ORM rows work directly; this
    exists for callers that outlive their session (the simulation harness loads
    knowledge bases up front and searches them during the run).
    """

    knowledge_base_id: str
    knowledge_base_name: str
    sources: list[Any]

    @classmethod
    def of(cls, kb: Any) -> KnowledgeBaseView:
        return cls(
            knowledge_base_id=str(getattr(kb, "knowledge_base_id", "")),
            knowledge_base_name=str(getattr(kb, "knowledge_base_name", "")),
            sources=list(getattr(kb, "sources", None) or []),
        )


@dataclass(frozen=True, slots=True)
class Chunk:
    knowledge_base_id: str
    knowledge_base_name: str
    source_id: str
    title: str
    text: str

    @property
    def tokens(self) -> list[str]:
        # Title included: a source called "Pricing" should answer "how much",
        # even when the body never repeats the word.
        return tokenize(f"{self.title} {self.text}")


def split_text(content: str) -> list[str]:
    """Blank-line paragraphs re-packed into ~CHUNK_TARGET_WORDS windows."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
    if not paragraphs:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for paragraph in paragraphs:
        words = len(paragraph.split())
        if current and current_words + words > CHUNK_TARGET_WORDS:
            chunks.append("\n\n".join(current))
            # Carry the tail of the finished chunk into the next one so a fact
            # split across the boundary is reachable from either side.
            tail = current[-1].split()
            overlap = (
                " ".join(tail[-CHUNK_OVERLAP_WORDS:]) if len(tail) > CHUNK_OVERLAP_WORDS else ""
            )
            current = [overlap] if overlap else []
            current_words = len(overlap.split())
        current.append(paragraph)
        current_words += words
    if current:
        chunks.append("\n\n".join(current))
    # A single paragraph longer than the target is left whole rather than cut
    # mid-sentence; MAX_SNIPPET_CHARS bounds what actually reaches the model.
    return chunks


def chunk_sources(
    knowledge_base_id: str, knowledge_base_name: str, sources: Iterable[Any]
) -> tuple[list[Chunk], list[dict[str, str]]]:
    """(searchable chunks, sources skipped because they hold no text)."""
    chunks: list[Chunk] = []
    skipped: list[dict[str, str]] = []
    for source in sources or []:
        if not isinstance(source, Mapping):
            continue
        source_type = str(source.get("type") or "")
        source_id = str(source.get("source_id") or "")
        if source_type != "text":
            skipped.append(
                {
                    "source_id": source_id,
                    "type": source_type,
                    "title": str(
                        source.get("title") or source.get("filename") or source.get("url") or ""
                    ),
                }
            )
            continue
        title = str(source.get("title") or "")
        content = str(source.get("content") or "")
        for text in split_text(content):
            chunks.append(
                Chunk(
                    knowledge_base_id=knowledge_base_id,
                    knowledge_base_name=knowledge_base_name,
                    source_id=source_id,
                    title=title,
                    text=text,
                )
            )
    return chunks, skipped


def rank_chunks(
    chunks: Sequence[Chunk], query: str, category: str | None = None
) -> list[tuple[Chunk, float]]:
    """BM25-score every chunk against the query, best first.

    IDF uses the always-positive `log(1 + (N - df + 0.5) / (df + 0.5))` form:
    on a corpus of a handful of chunks the classic formulation goes negative
    for any term appearing in more than half of them, which would rank the
    chunks that contain the query word *below* the ones that don't.
    """
    terms = query_terms(query)
    if not chunks or not terms:
        return []
    documents = [chunk.tokens for chunk in chunks]
    total = len(documents)
    avg_len = sum(len(doc) for doc in documents) / total
    category_token = (category or "").strip().casefold()

    scored: list[tuple[Chunk, float]] = []
    for chunk, doc in zip(chunks, documents, strict=True):
        doc_len = len(doc) or 1
        counts: dict[str, int] = {}
        for token in doc:
            counts[token] = counts.get(token, 0) + 1
        score = 0.0
        for term in set(terms):
            freq = counts.get(term, 0)
            if not freq:
                continue
            containing = sum(1 for other in documents if term in other)
            idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
            score += idf * (
                freq * (BM25_K1 + 1) / (freq + BM25_K1 * (1 - BM25_B + BM25_B * doc_len / avg_len))
            )
        if score and category_token and category_token in f"{chunk.title} {chunk.text}".casefold():
            score *= CATEGORY_BOOST
        if score > 0:
            scored.append((chunk, score))
    # Ties broken by title then text so the same query always returns the same
    # ordering — a simulation run comparing transcripts depends on it.
    scored.sort(key=lambda pair: (-pair[1], pair[0].title, pair[0].text))
    return scored


def _snippet(text: str) -> str:
    if len(text) <= MAX_SNIPPET_CHARS:
        return text
    return text[:MAX_SNIPPET_CHARS].rstrip() + "…"


def search(
    knowledge_bases: Iterable[Any],
    query: str,
    *,
    category: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    """Top matching snippets across `knowledge_bases` for `query`.

    `knowledge_bases` are KnowledgeBase rows (anything exposing
    knowledge_base_id / knowledge_base_name / sources). The return shape is
    what the worker hands the model verbatim, so it stays flat and small.
    """
    top_k = max(1, min(int(top_k or DEFAULT_TOP_K), MAX_TOP_K))
    chunks: list[Chunk] = []
    skipped: list[dict[str, str]] = []
    for kb in knowledge_bases:
        kb_chunks, kb_skipped = chunk_sources(
            str(getattr(kb, "knowledge_base_id", "")),
            str(getattr(kb, "knowledge_base_name", "")),
            getattr(kb, "sources", None) or [],
        )
        chunks.extend(kb_chunks)
        skipped.extend(kb_skipped)

    ranked = rank_chunks(chunks, query, category)
    floor = ranked[0][1] * SCORE_FLOOR_RATIO if ranked else 0.0
    results = [
        {
            "title": chunk.title,
            "content": _snippet(chunk.text),
            "source_id": chunk.source_id,
            "knowledge_base_id": chunk.knowledge_base_id,
            "knowledge_base_name": chunk.knowledge_base_name,
            "score": round(score, 4),
        }
        for chunk, score in ranked[:top_k]
        if score >= floor
    ]
    return {"query": query, "results": results, "skipped_sources": skipped}
