"""Every closed value set used on the wire (spine Consistency Conventions)."""

from enum import StrEnum


class Service(StrEnum):
    """The seven services, by Dapr app id (AD-1)."""

    WEB = "web"
    INTAKE = "intake"
    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    RETRIEVAL = "retrieval"
    VERDICT = "verdict"
    WORKFLOW = "workflow"


class Verdict(StrEnum):
    STANDARD = "standard"
    LOADED = "loaded"
    DECLINE = "decline"
    REFER = "refer"


class PageStatus(StrEnum):
    UPLOADED = "uploaded"
    CLASSIFIED = "classified"
    AWAITING_CUSTOMER = "awaiting_customer"
    AWAITING_TRIAGE = "awaiting_triage"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    DISCARDED = "discarded"
    DENIED = "denied"
    FAILED = "failed"


class CaseStatus(StrEnum):
    RUNNING = "running"
    AWAITING_HUMAN = "awaiting_human"
    COMPLETED = "completed"
    FAILED = "failed"


class StageStatus(StrEnum):
    """Status of one stage result (AD-6)."""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Decision(StrEnum):
    """The human-reserved actions (AD-10)."""

    KEEP = "keep"
    DISCARD = "discard"
    ACCEPT = "accept"
    DENY = "deny"


class DemoRole(StrEnum):
    """The values of the `X-Demo-Role` header (AD-9)."""

    CUSTOMER = "customer"
    UNDERWRITER = "underwriter"


class QueuedBy(StrEnum):
    """How a page came to wait in the triage queue (AD-7, AD-10)."""

    # The gate was not sure of the page.
    GATE = "gate"
    # The gate was sure the page is not medical, and the customer kept it.
    CUSTOMER = "customer"


class PageType(StrEnum):
    LAB_REPORT = "lab_report"
    ATTENDING_PHYSICIAN_STATEMENT = "attending_physician_statement"
    APPLICATION_FORM = "application_form"
    ID_DOCUMENT = "id_document"
    INVOICE = "invoice"
    OTHER = "other"


class ActorKind(StrEnum):
    HUMAN = "human"
    AI = "ai"


class ClassifierContender(StrEnum):
    LLM = "llm"
    DOC_INTELLIGENCE = "doc-intelligence"


class RetrieverConfig(StrEnum):
    """The retrieval ladder rows (AD-11)."""

    R1 = "r1"
    R2 = "r2"
    R3 = "r3"
    R4 = "r4"
    R5 = "r5"
    R6 = "r6"


class ChunkSet(StrEnum):
    FIXED = "fixed"
    SMART = "smart"


class ToolName(StrEnum):
    """The verdict agent's three tools (AD-15)."""

    LIST_FACTS = "list_facts"
    SEARCH_RULES = "search_rules"
    READ_RULE = "read_rule"


class ReasonEffect(StrEnum):
    NONE = "none"
    DEBIT = "debit"
    DECLINE = "decline"


class SystemReason(StrEnum):
    """Verdict reasons that need no citation (AD-15)."""

    NO_MATCHING_RULE = "no_matching_rule"
    CONFLICTING_RULES = "conflicting_rules"
    UNVERIFIED_QUOTE = "unverified_quote"
    LOW_CONFIDENCE = "low_confidence"
    STEP_LIMIT = "step_limit"


class StopAfter(StrEnum):
    """Where a case may be told to stop early (AD-17: the classifier bake-off)."""

    GATE = "gate"
