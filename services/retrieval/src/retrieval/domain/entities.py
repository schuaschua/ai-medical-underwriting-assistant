"""What the service works with: the parsed manual, a chunk, the record every store holds, and a chunk as a search reads it."""

from collections.abc import Mapping
from dataclasses import dataclass

from contracts.enums import ChunkSet

# AD-12: the one embedding deployment is `text-embedding-3-large`, whose
# vectors have this many dimensions. The chunk table's column has the same.
EMBEDDING_DIMENSIONS = 3072


@dataclass(frozen=True, slots=True)
class LayoutParagraph:
    """One paragraph the layout model found, in reading order."""

    # The 1-based page the paragraph starts on.
    page_number: int
    text: str
    # What the layout model says the paragraph is, when it says: a page
    # header, a section heading and so on.
    role: str | None = None


@dataclass(frozen=True, slots=True)
class LayoutPage:
    page_number: int
    # Whether the layout model read any text on the page at all.
    has_text: bool


@dataclass(frozen=True, slots=True)
class ParsedLayout:
    """The manual as the layout model read it."""

    pages: tuple[LayoutPage, ...]
    paragraphs: tuple[LayoutParagraph, ...]


@dataclass(frozen=True, slots=True)
class Chunk:
    """AD-12: one piece of the manual, cut from the parsed layout.

    A `smart` chunk is one rule's definition. A `fixed` chunk is a run of
    the body text of a fixed size, and defines whatever rules have their
    definition marker inside it: none, one or several.
    """

    chunk_id: str
    chunk_set: ChunkSet
    # Only the rules the text defines, never ones it refers to (AD-12).
    rule_ids: tuple[str, ...]
    # The text: a rule's definition, or the run of body text.
    text: str
    # The rules the text refers to and does not define, in order of first mention.
    reference_rule_ids: tuple[str, ...]
    # The numbered part of the manual the chunk starts in, such as `2.4`,
    # and that part's heading.
    section_id: str
    section_title: str
    # The heading of the numbered section the part belongs to.
    impairment: str
    # The 1-based page the chunk starts on.
    manual_page: int

    @property
    def rule_id(self) -> str:
        """The one rule a `smart` chunk defines."""
        (rule_id,) = self.rule_ids
        return rule_id


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    """A chunk as it is stored: the one source for every store (Epic 3 loads the same records)."""

    chunk: Chunk
    # One line, written by the chat model, that says where the rule sits in
    # the manual. Empty for a `fixed` chunk: the plain baseline has none.
    context_line: str
    # The vector of what was embedded: the context line followed by the
    # chunk text, or a `fixed` chunk's own text.
    embedding: tuple[float, ...]
    # What the context line and the vector were made from: see `fingerprint`.
    content_hash: str
    chat_deployment: str
    embedding_deployment: str


@dataclass(frozen=True, slots=True)
class StoredChunk:
    """What a run needs to know of a chunk an earlier run stored."""

    chunk_id: str
    content_hash: str
    manual_page: int
    # The rules the chunk defines: what a run must not lose too many of.
    rule_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IngestRun:
    """What the stored chunk set was last built from, by a run that succeeded.

    A run that finds the same manual, recipe and deployments, and as many
    chunks as that run left, has nothing to do.
    """

    manual_sha256: str
    # A digest of how the chunks are made, besides the manual and the
    # deployments: the context-line prompt for the `smart` set, the chunk
    # size and overlap for the `fixed` set (`ingest.recipe_digest`).
    prompt_digest: str
    # Empty for the `fixed` set, which asks no chat model.
    chat_deployment: str
    embedding_deployment: str
    chunk_count: int


@dataclass(frozen=True, slots=True)
class IngestPlan:
    """What a run does to the stored chunk set."""

    # New chunks and chunks whose text changed: each gets a context line and a vector.
    write: tuple[Chunk, ...]
    # Chunks whose text is as it was and whose page moved: no model call.
    move: tuple[Chunk, ...]
    # Stored chunks whose rule the manual no longer defines.
    remove: tuple[str, ...]
    # Chunks that are stored exactly as the manual has them.
    unchanged: int


@dataclass(frozen=True, slots=True)
class IngestReport:
    """Counts of one run, for its log line. No text of the manual."""

    pages: int
    chunks: int
    written: int
    moved: int
    removed: int
    unchanged: int
    # The manual, the prompt and the deployments were those of the last run:
    # nothing was parsed and nothing asked of a model.
    skipped: bool = False


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    """A stored chunk as a search or a rule read answers it (story 2.3)."""

    chunk_id: str
    chunk_set: ChunkSet
    # Only the rules the chunk defines (AD-12).
    rule_ids: tuple[str, ...]
    # The rules its text refers to, in order of first mention.
    reference_rule_ids: tuple[str, ...]
    text: str
    manual_page: int
    impairment: str
    # The cosine distance from the query's vector, 0 to 2, when the chunk
    # was found by the vector search; None otherwise.
    cosine_distance: float | None = None


@dataclass(frozen=True, slots=True)
class IndexDocument:
    """AD-11, row `r5`: one `smart` chunk record as the search service's index holds it.

    The stored record and nothing else: the same id, text, context line and
    vector as the chunk table has. Nothing is cut again and nothing is
    embedded for the index.
    """

    chunk_id: str
    chunk_set: ChunkSet
    rule_ids: tuple[str, ...]
    reference_rule_ids: tuple[str, ...]
    section_id: str
    section_title: str
    impairment: str
    manual_page: int
    text: str
    context_line: str
    embedding: tuple[float, ...]
    # The record's own hash, of what its context line and vector were made from.
    content_hash: str
    embedding_deployment: str
    # A hash of every field above but the vector, which `content_hash`
    # stands for: a load uploads a document only where this differs.
    document_hash: str


@dataclass(frozen=True, slots=True)
class IndexHoldings:
    """What the search service's index holds, as it says itself."""

    # The service's own count of its documents.
    count: int
    # Each document's `document_hash`, by `chunk_id`.
    document_hashes: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RankedDocument:
    """One document of the search service's answer to a query, in the service's order."""

    chunk_id: str
    rule_ids: tuple[str, ...]
    text: str
    manual_page: int
    impairment: str
    # The semantic ranker's score as the service gave it: 0 to 4.
    reranker_score: float
    # The deployment the document's vector was made with.
    embedding_deployment: str
    # The stored record's hash, as the index holds it: another one than
    # the chunk table's says the document is stale.
    content_hash: str


@dataclass(frozen=True, slots=True)
class IndexLoadReport:
    """Counts of one load of the search service's index, for its log line."""

    documents: int
    uploaded: int
    removed: int
    unchanged: int
    # Whether the load had to create the index.
    created: bool = False
