"""The one settings object of the bake-off runner (coding-style rule 12)."""

import ipaddress
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from contracts.enums import RetrieverConfig
from contracts.ids import EvalRunId
from contracts.models.retrieval import DEFAULT_TOP_K, MAX_TOP_K

# The runner is never installed into an image: it runs from a checkout, and
# its defaults are paths of that checkout.
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
# Where the published scoreboards live. Only a run against the deployed
# environment writes here; `web` serves the files read-only (AD-17).
PUBLISHED_SCOREBOARDS = REPOSITORY_ROOT / "data" / "scoreboards"
# The gitignored scratch folder: where a run against local stand-ins writes.
SCRATCH = REPOSITORY_ROOT / ".work"

RETRIEVAL_FILE = "retrieval.json"
REDACTION_FILE = "redaction.json"

_LOOPBACK_NAMES = frozenset({"localhost"})


def is_loopback(host: str) -> bool:
    """Whether a host name or address is this machine."""
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    """Read once from environment variables prefixed `EVALS_`; the command line overrides them."""

    model_config = SettingsConfigDict(
        env_prefix="EVALS_", extra="ignore", frozen=True, populate_by_name=True
    )

    # AD-17: the one address the runner talks to.
    web_address: str = "http://localhost:8000"
    # Whether the run is against the deployed environment, with the real AI
    # services. Nothing can be read from `web` that says so: the operator
    # says it. Without it the figures are marked as stand-in figures.
    deployed: bool = False
    # One id for the whole bake-off run. Given again, the run is resumed:
    # cases already uploaded under it are not uploaded again.
    eval_run_id: EvalRunId | None = None

    # The cases to run, by their key (`case-001`); every case when not given.
    cases: list[str] | None = None
    # The ladder rows to try; every row when not given. A row that answers
    # "not available" is recorded as not measured either way.
    rows: Annotated[list[RetrieverConfig], Field(min_length=1)] | None = None
    # AD-17: a hit is an expected rule in the top 5.
    top_k: Annotated[int, Field(ge=1, le=MAX_TOP_K)] = DEFAULT_TOP_K

    # The runner is gentle on the system: `workflow` runs five activities at
    # once and every stage shares one token limit. Cases under way at once:
    case_concurrency: Annotated[int, Field(ge=1, le=8)] = 2
    # Eval searches under way at once.
    search_concurrency: Annotated[int, Field(ge=1, le=4)] = 2

    # Every wait has a deadline. One call to `web` (its own deadline for a
    # call to a service is 20 s):
    request_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    # The upload (`web` gives `intake` 120 s):
    upload_timeout_seconds: Annotated[float, Field(gt=0)] = 150.0
    # One case, from its start to its final status. In Azure a case costs a
    # model call per page for each stage and one agent run per row.
    case_deadline_seconds: Annotated[float, Field(gt=0)] = 1800.0
    # How often a case's progress is read.
    poll_seconds: Annotated[float, Field(gt=0)] = 2.0
    # A call that got no answer, or a 429, 502, 503 or 504, is sent again
    # this often, this far apart. Every call the runner makes is safe to repeat.
    request_retries: Annotated[int, Field(ge=0, le=5)] = 2
    retry_seconds: Annotated[float, Field(ge=0)] = 2.0

    # The synthetic cases and the answer key (`data/`).
    data_dir: Path = REPOSITORY_ROOT / "data"
    static_metrics_file: Path = REPOSITORY_ROOT / "evals" / "static-metrics.yaml"
    # Where the two scoreboard files are written. Not given: the published
    # folder for a run against the deployed environment, the scratch folder
    # for any other.
    output_dir: Path | None = None
    # Where a run keeps which case it uploaded as which case id, to resume by.
    state_dir: Path = SCRATCH / "evals"
    # Story 4.2: where `bakeoff.training_pages` writes the redacted training
    # pages and their list, for an operator to upload to `classifier-training`.
    training_pages_dir: Path = SCRATCH / "classifier-training"

    @field_validator("web_address")
    @classmethod
    def _address_is_safe_to_send_to(cls, value: str) -> str:
        address = value.rstrip("/")
        parts = urlsplit(address)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("web_address must be an http or https address")
        if parts.path or parts.query or parts.fragment or parts.username:
            raise ValueError("web_address must be the address alone, with no path")
        # The case documents travel to this address: in the clear only on this machine.
        if parts.scheme == "http" and not is_loopback(parts.hostname):
            raise ValueError("web_address must be https unless it is this machine")
        return address

    @model_validator(mode="after")
    def _only_a_deployed_run_is_published(self) -> Self:
        local = is_loopback(urlsplit(self.web_address).hostname or "")
        if self.deployed and local:
            raise ValueError(
                "a run against this machine is not a run against the deployed environment"
            )
        published = _within(self.scoreboard_dir, PUBLISHED_SCOREBOARDS)
        if not self.deployed and published:
            # AD-17: figures from stand-ins are not results and are never published.
            raise ValueError(
                "only a run against the deployed environment (--deployed) writes "
                "data/scoreboards"
            )
        if published and (self.cases is not None or self.rows is not None):
            # A part of the bake-off must not replace the published whole.
            raise ValueError(
                "a run narrowed with cases or rows does not write data/scoreboards; "
                "give it another output folder"
            )
        return self

    @property
    def scoreboard_dir(self) -> Path:
        """Where this run writes its two files."""
        if self.output_dir is not None:
            return self.output_dir
        return PUBLISHED_SCOREBOARDS if self.deployed else SCRATCH / "scoreboards"

    @property
    def cases_dir(self) -> Path:
        return self.data_dir / "cases"

    @property
    def answer_key_dir(self) -> Path:
        return self.data_dir / "answer-key" / "cases"


def _within(path: Path, folder: Path) -> bool:
    return path.resolve().is_relative_to(folder.resolve())
