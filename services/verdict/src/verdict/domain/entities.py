"""What `verdict` keeps and works with: the key row of a run, the agent's answer and the suggestion (AD-6, AD-15)."""

from dataclasses import dataclass
from datetime import datetime

from contracts.enums import ReasonEffect, RetrieverConfig, SystemReason, Verdict
from contracts.models.verdict import Reason


@dataclass(frozen=True, slots=True)
class RunKey:
    """The idempotency key of a verdict run command (AD-6)."""

    case_id: str
    retriever_config: RetrieverConfig


@dataclass(frozen=True, slots=True)
class KeyRow:
    """The key row of one verdict run: one per key, whatever is repeated."""

    verdict_run_id: str
    key: RunKey
    started_at: datetime
    # The stored stage result, as JSON, once the run has ended; None while
    # it runs.
    result_json: str | None

    @property
    def running(self) -> bool:
        return self.result_json is None


@dataclass(frozen=True, slots=True)
class AgentAnswer:
    """The agent's final answer: its text, and why the model stopped (AD-16)."""

    # As the model gave it; the domain parses it. Empty when it gave none.
    text: str
    # The model's finish reason as a short code (`stop`, `length`,
    # `content_filter`), `none` when it named none, `other` for anything
    # that is not a plain code. `length` means the answer was cut off at
    # the token limit.
    finish_reason: str


@dataclass(frozen=True, slots=True)
class Rating:
    """What a rule's text says a case that meets it is rated: read by code, never by the model."""

    effect: ReasonEffect
    # Set when, and only when, the effect is `debit`; never 0.
    debit_pct: int | None = None


@dataclass(frozen=True, slots=True)
class Suggestion:
    """What is stored of a done run. Every field is set by domain code (AD-15)."""

    verdict: Verdict
    # Set when, and only when, the verdict is `loaded`.
    loading_pct: int | None
    # The agent's own figure; None when the agent gave no answer.
    confidence: float | None
    reasons: tuple[Reason, ...]
    system_reasons: tuple[SystemReason, ...]
    # How many proposed reasons were not kept. Counted for the log; never
    # stored and never shown.
    dropped: int = 0
