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

Indexed: `type: "text"` sources, and uploaded documents whose bytes are already
text — markdown, plain text, CSV. Markdown gets two things beyond decoding,
because "attach our handbook" is how a knowledge base actually gets built:
sections are split on headings rather than blank lines (so a hit is titled
"Pricing Snapshot › Current trial offer" instead of the filename), and YAML
frontmatter is parsed so a `category:` field becomes an exact match for the
caller's `category` argument instead of a guess at the text.

Still opaque: PDF and Office documents (they need a parser dependency) and URL
sources (nothing has fetched them). `search` reports those in
`skipped_sources`, so the caller can tell "no answer in the KB" apart from
"that source type isn't searchable yet".
"""

import logging
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

log = logging.getLogger(__name__)

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
# A frontmatter `category:` is the source declaring its own topic, so matching
# it is evidence rather than the coincidence a substring hit represents. Worth
# more than CATEGORY_BOOST for that reason, and the two never both apply.
CATEGORY_EXACT_BOOST = 1.6

# Uploaded documents we can index without a parser dependency. Content types
# are checked first; browsers and CLI uploads frequently send
# application/octet-stream for .md, so the suffix is a fallback rather than a
# second opinion.
TEXT_FILE_CONTENT_TYPES = frozenset(
    {"text/markdown", "text/x-markdown", "text/plain", "text/csv", "text/tab-separated-values"}
)
TEXT_FILE_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".text", ".csv", ".tsv"})
MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
# Per-file ceiling on what we decode and index. Uploads are capped at 20MB
# each, and chunking a corpus that size on every lookup would put seconds of
# CPU inside a live call — the whole point of the per-lookup design is that a
# voice agent's knowledge base is small.
MAX_INDEXED_FILE_BYTES = 2 * 1024 * 1024

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


_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_FRONTMATTER_FIELD_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def _suffix(filename: str) -> str:
    _, dot, ext = filename.rpartition(".")
    return f".{ext.lower()}" if dot else ""


def is_indexable_file(filename: str, content_type: str) -> bool:
    """True when an uploaded document's bytes are text we can read as-is."""
    if (content_type or "").split(";")[0].strip().lower() in TEXT_FILE_CONTENT_TYPES:
        return True
    return _suffix(filename or "") in TEXT_FILE_SUFFIXES


def decode_file(data: bytes) -> str | None:
    """Uploaded bytes as text, or None when they are not text after all.

    A declared text/* content type is the uploader's claim, not a fact — a
    mislabelled PDF would otherwise be indexed as mojibake and pollute every
    ranking. Strict UTF-8 is the check: real markdown passes, binary does not.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    # A NUL byte survives UTF-8 decoding but never appears in a text document.
    return None if "\x00" in text else text


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """(YAML frontmatter fields, body).

    Scalar `key: value` lines only — enough for the `category:`/`title:` these
    documents carry, and not a reason to take a YAML dependency. Nested blocks
    and lists are simply not returned; the body is what gets indexed either way.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if line[:1].isspace() or line.lstrip().startswith("#"):
            continue  # a nested value or a comment, not a scalar field
        if field := _FRONTMATTER_FIELD_RE.match(line):
            fields[field.group(1).strip().lower()] = field.group(2).strip().strip("\"'")
    return fields, text[match.end() :]


def split_markdown_sections(text: str, root_title: str = "") -> list[tuple[str, str]]:
    """[(heading path, section body)] for a markdown document.

    Headings are the author's own boundaries, so they beat blank lines: a hit
    comes back titled "Pricing Snapshot › Current trial offer" rather than
    "pricing_snapshot.md", which is most of what tells the agent — and the
    operator reading the transcript — where an answer came from.
    """
    sections: list[tuple[str, str]] = []
    trail: list[str] = []
    title = root_title
    body: list[str] = []

    def flush() -> None:
        if content := "\n".join(body).strip():
            sections.append((title, content))

    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if not heading:
            body.append(line)
            continue
        flush()
        body = []
        depth = len(heading.group(1))
        del trail[depth - 1 :]
        trail.append(heading.group(2).strip())
        parts = ([root_title] if root_title else []) + trail
        title = " › ".join(parts)
    flush()
    return sections


@dataclass(frozen=True, slots=True)
class KnowledgeBaseFileView:
    """An uploaded document's bytes, detached from the session."""

    source_id: str
    filename: str
    content_type: str
    data: bytes

    @classmethod
    def of(cls, row: Any) -> KnowledgeBaseFileView:
        return cls(
            source_id=str(getattr(row, "source_id", "")),
            filename=str(getattr(row, "filename", "")),
            content_type=str(getattr(row, "content_type", "")),
            data=bytes(getattr(row, "data", b"") or b""),
        )


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
    # Uploaded blobs, keyed by source_id. Absent for a caller that only has the
    # KnowledgeBase row; those knowledge bases simply index their text sources.
    files: dict[str, KnowledgeBaseFileView] = field(default_factory=dict)

    @classmethod
    def of(cls, kb: Any, files: Iterable[Any] = ()) -> KnowledgeBaseView:
        return cls(
            knowledge_base_id=str(getattr(kb, "knowledge_base_id", "")),
            knowledge_base_name=str(getattr(kb, "knowledge_base_name", "")),
            sources=list(getattr(kb, "sources", None) or []),
            files={
                view.source_id: view
                for view in (KnowledgeBaseFileView.of(row) for row in files)
                if view.source_id
            },
        )


@dataclass(frozen=True, slots=True)
class Chunk:
    knowledge_base_id: str
    knowledge_base_name: str
    source_id: str
    title: str
    text: str
    # From a document's frontmatter, when it declares one. Empty for text
    # sources and for markdown without frontmatter.
    category: str = ""

    @property
    def tokens(self) -> list[str]:
        # Title included: a source called "Pricing" should answer "how much",
        # even when the body never repeats the word.
        return tokenize(f"{self.title} {self.text}")


def _split_paragraph(paragraph: str) -> list[str]:
    """One oversized paragraph as overlapping word windows.

    Blank lines are not a reliable boundary: an FAQ pasted with single
    newlines, or any long section, arrives as ONE paragraph. Leaving it whole
    used to produce a chunk far longer than a snippet, and `_snippet` then
    returned its opening — so a query matching something on page three came
    back "successful" with text that does not contain the answer. That is
    worse than a miss, because the agent answers confidently from the wrong
    section instead of being told to say it doesn't know.
    """
    words = paragraph.split()
    if len(words) <= CHUNK_TARGET_WORDS:
        return [paragraph]
    step = CHUNK_TARGET_WORDS - CHUNK_OVERLAP_WORDS
    return [
        " ".join(window)
        for start in range(0, len(words), step)
        if (window := words[start : start + CHUNK_TARGET_WORDS])
    ]


def split_text(content: str) -> list[str]:
    """Blank-line paragraphs re-packed into ~CHUNK_TARGET_WORDS windows."""
    paragraphs = [
        piece
        for block in re.split(r"\n\s*\n", content)
        if block.strip()
        for piece in _split_paragraph(block.strip())
    ]
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
    return chunks


def _document_text(
    source: Mapping[str, Any], files: Mapping[str, KnowledgeBaseFileView]
) -> tuple[str | None, str]:
    """(decoded text, reason it was skipped) for an uploaded document source."""
    source_id = str(source.get("source_id") or "")
    blob = files.get(source_id)
    if blob is None:
        # The caller did not load blobs (or the row is gone). Not a format
        # problem, so say so rather than blaming the file.
        return None, "not loaded"
    if not is_indexable_file(blob.filename, blob.content_type):
        return None, blob.content_type or "unknown type"
    if len(blob.data) > MAX_INDEXED_FILE_BYTES:
        return None, f"larger than {MAX_INDEXED_FILE_BYTES // (1024 * 1024)}MB"
    text = decode_file(blob.data)
    if text is None:
        return None, "not valid UTF-8 text"
    return text, ""


def _chunk_document(
    text: str, *, filename: str, is_markdown: bool
) -> tuple[list[tuple[str, str]], str]:
    """([(title, chunk text)], declared category) for one decoded document."""
    fields, body = split_frontmatter(text) if is_markdown else ({}, text)
    category = fields.get("category", "")
    root = fields.get("title") or filename
    pieces: list[tuple[str, str]] = []
    sections = split_markdown_sections(body, root) if is_markdown else [(root, body)]
    for title, section in sections:
        # Sections still go through word packing: an author's heading says where
        # a topic starts, not that it is short enough to hand a model whole.
        pieces.extend((title, chunk) for chunk in split_text(section))
    return pieces, category


def chunk_sources(
    knowledge_base_id: str,
    knowledge_base_name: str,
    sources: Iterable[Any],
    files: Mapping[str, KnowledgeBaseFileView] | None = None,
) -> tuple[list[Chunk], list[dict[str, str]]]:
    """(searchable chunks, sources skipped because they hold no readable text)."""
    files = files or {}
    chunks: list[Chunk] = []
    skipped: list[dict[str, str]] = []

    def add(source_id: str, title: str, text: str, category: str = "") -> None:
        chunks.append(
            Chunk(
                knowledge_base_id=knowledge_base_id,
                knowledge_base_name=knowledge_base_name,
                source_id=source_id,
                title=title,
                text=text,
                category=category,
            )
        )

    for source in sources or []:
        if not isinstance(source, Mapping):
            continue
        source_type = str(source.get("type") or "")
        source_id = str(source.get("source_id") or "")
        label = str(source.get("title") or source.get("filename") or source.get("url") or "")

        if source_type == "text":
            for text in split_text(str(source.get("content") or "")):
                add(source_id, str(source.get("title") or ""), text)
            continue

        if source_type == "document":
            text, reason = _document_text(source, files)
            if text is None:
                skipped.append(
                    {"source_id": source_id, "type": source_type, "title": label, "reason": reason}
                )
                continue
            filename = str(source.get("filename") or label)
            pieces, category = _chunk_document(
                text, filename=filename, is_markdown=_suffix(filename) in MARKDOWN_SUFFIXES
            )
            for title, chunk in pieces:
                add(source_id, title, chunk, category)
            continue

        skipped.append(
            {
                "source_id": source_id,
                "type": source_type,
                "title": label,
                "reason": "no text has been extracted for this source type",
            }
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

    Document frequency is computed ONCE per term. It is a property of the
    corpus, not of the chunk being scored, and recomputing it inside the loop
    made ranking quadratic: a pasted handbook of a few thousand chunks took
    tens of seconds, and since this runs synchronously inside an async request
    handler it blocked the whole API process — well past the worker's 10s
    lookup timeout — on every kb_lookup.
    """
    terms = query_terms(query)
    if not chunks or not terms:
        return []
    counts: list[Counter[str]] = [Counter(chunk.tokens) for chunk in chunks]
    total = len(counts)
    avg_len = sum(sum(c.values()) for c in counts) / total
    category_token = (category or "").strip().casefold()

    idfs: dict[str, float] = {}
    for term in set(terms):
        containing = sum(1 for c in counts if term in c)
        idfs[term] = math.log(1 + (total - containing + 0.5) / (containing + 0.5))

    scored: list[tuple[Chunk, float]] = []
    for chunk, count in zip(chunks, counts, strict=True):
        doc_len = sum(count.values()) or 1
        score = 0.0
        for term, idf in idfs.items():
            freq = count.get(term, 0)
            if not freq:
                continue
            score += idf * (
                freq * (BM25_K1 + 1) / (freq + BM25_K1 * (1 - BM25_B + BM25_B * doc_len / avg_len))
            )
        if score and category_token:
            if chunk.category:
                # The source declared its own topic, so this is a real answer to
                # the caller's category rather than a word that happened to
                # appear. A chunk that declares a DIFFERENT category is left
                # alone rather than penalised: categories are the caller's
                # taxonomy, and the right answer is often filed elsewhere.
                if chunk.category.strip().casefold() == category_token:
                    score *= CATEGORY_EXACT_BOOST
            elif category_token in f"{chunk.title} {chunk.text}".casefold():
                score *= CATEGORY_BOOST
        if score > 0:
            scored.append((chunk, score))
    # Ties broken by title then text so the same query always returns the same
    # ordering — a simulation run comparing transcripts depends on it.
    scored.sort(key=lambda pair: (-pair[1], pair[0].title, pair[0].text))
    return scored


def _snippet(text: str, terms: Sequence[str]) -> str:
    """At most MAX_SNIPPET_CHARS of `text`, windowed around what matched.

    Chunking already keeps chunks well under the cap, so this is the backstop
    for the pathological source (one enormous unbroken line). Taking the head
    unconditionally is what must not happen: it can return a "hit" whose text
    does not contain the match, which reads to the agent as an answer.
    """
    if len(text) <= MAX_SNIPPET_CHARS:
        return text
    lowered = text.casefold()
    hits = [pos for term in terms if (pos := lowered.find(term)) >= 0]
    if not hits:
        return text[:MAX_SNIPPET_CHARS].rstrip() + "…"
    # Open the window a third of the way before the first match, so the
    # matched sentence keeps the lead-in that gives it meaning.
    start = max(0, min(hits) - MAX_SNIPPET_CHARS // 3)
    start = min(start, len(text) - MAX_SNIPPET_CHARS)
    piece = text[start : start + MAX_SNIPPET_CHARS].strip()
    return ("…" if start else "") + piece + ("…" if start + MAX_SNIPPET_CHARS < len(text) else "")


async def load_views(session: Any, knowledge_base_ids: Sequence[str], workspace_id: str) -> list:
    """Knowledge bases + their uploaded blobs, detached from the session.

    The single place that decides what retrieval can see, so the live lookup
    and the simulation harness cannot drift on it. Always workspace-scoped —
    the ids reach here from user-editable tool config.
    """
    from ..models import KnowledgeBase, KnowledgeBaseFile

    ids = [str(i) for i in knowledge_base_ids if i]
    if not ids:
        return []
    rows = (
        await session.scalars(
            select(KnowledgeBase).where(
                KnowledgeBase.workspace_id == workspace_id,
                KnowledgeBase.knowledge_base_id.in_(ids),
            )
        )
    ).all()
    if not rows:
        return []
    found = [kb.knowledge_base_id for kb in rows]
    files = (
        await session.scalars(
            select(KnowledgeBaseFile).where(
                KnowledgeBaseFile.workspace_id == workspace_id,
                KnowledgeBaseFile.knowledge_base_id.in_(found),
                # Blobs we could never index are not worth reading out of the
                # database on every lookup.
                KnowledgeBaseFile.size_bytes <= MAX_INDEXED_FILE_BYTES,
            )
        )
    ).all()
    by_kb: dict[str, list[Any]] = {}
    for row in files:
        by_kb.setdefault(row.knowledge_base_id, []).append(row)
    return [KnowledgeBaseView.of(kb, by_kb.get(kb.knowledge_base_id, ())) for kb in rows]


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
            getattr(kb, "files", None) or {},
        )
        chunks.extend(kb_chunks)
        skipped.extend(kb_skipped)

    terms = query_terms(query)
    ranked = rank_chunks(chunks, query, category)
    floor = ranked[0][1] * SCORE_FLOOR_RATIO if ranked else 0.0
    results = [
        {
            "title": chunk.title,
            "content": _snippet(chunk.text, terms),
            "source_id": chunk.source_id,
            "knowledge_base_id": chunk.knowledge_base_id,
            "knowledge_base_name": chunk.knowledge_base_name,
            "score": round(score, 4),
        }
        for chunk, score in ranked[:top_k]
        if score >= floor
    ]
    if not results and skipped:
        # "Attach our handbook" builds a knowledge base entirely out of PDFs or
        # URLs. It stores fine and the dashboard shows it complete, but nothing
        # in it is searchable yet, so every lookup misses forever — a silence
        # that is indistinguishable from an honest "the KB has no answer".
        # This is the one line that tells an operator which one they have.
        log.warning(
            "knowledge lookup found nothing and %d source(s) were not searchable: %s",
            len(skipped),
            sorted({s["type"] for s in skipped}),
        )
    return {"query": query, "results": results, "skipped_sources": skipped}
