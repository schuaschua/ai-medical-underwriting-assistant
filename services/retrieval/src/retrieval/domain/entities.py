"""What the service works with: the parsed manual, a chunk, the record every store holds, and a chunk as a search reads it."""

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
    """AD-12: one rule of the manual, cut from the parsed layout."""

    chunk_id: str
    chunk_set: ChunkSet
    # The one rule the text defines.
    rule_id: str
    # The rule's definition, with the text that belongs to it.
    text: str
    # The rules the text refers to and does not define, in order of first mention.
    reference_rule_ids: tuple[str, ...]
    # The numbered part of the manual the definition is printed in, such as
    # `2.4`, and that part's heading.
    section_id: str
    section_title: str
    # The heading of the numbered section the part belongs to.
    impairment: str
    # The 1-based page the definition is printed on.
    manual_page: int

    @property
    def rule_ids(self) -> tuple[str, ...]:
        """Only the rules the chunk defines, never ones it refers to (AD-12)."""
        return (self.rule_id,)


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    """A chunk as it is stored: the one source for every store (Epic 3 loads the same records)."""

    chunk: Chunk
    # One line, written by the chat model, that says where the rule sits in the manual.
    context_line: str
    # The vector of the context line followed by the chunk text.
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


@dataclass(frozen=True, slots=True)
class IngestRun:
    """What the stored chunk set was last built from, by a run that succeeded.

    A run that finds the same manual, prompt and deployments, and as many
    chunks as that run left, has nothing to do.
    """

    manual_sha256: str
    prompt_digest: str
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
