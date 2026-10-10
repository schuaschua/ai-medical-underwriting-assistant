"""Reads the scoreboard files the bake-off runner wrote (spine AD-17).

The scores are files. `web` reads one from its folder, checks it against its
contracts model and answers it as it is: it works no score out, stores none
and has no route that takes one. A file that is not there means the bake-off
has not been run; a file that does not fit its model is a fault, answered
without any part of it.
"""

import logging
from pathlib import Path

from pydantic import ValidationError

from contracts.base import ContractModel
from contracts.errors import DomainError, ErrorCode
from contracts.models.web import (
    ClassificationScoreboard,
    RedactionScoreboard,
    RetrievalScoreboard,
)
from web.adapters.http.errors import INTERNAL_ERROR_MESSAGE

logger = logging.getLogger(__name__)

# The names the runner writes its files under. Nothing else in the folder
# is read, whatever it holds.
RETRIEVAL_FILE = "retrieval.json"
REDACTION_FILE = "redaction.json"
# Story 4.3: the classifier bake-off's file, written by a run of its own.
CLASSIFICATION_FILE = "classification.json"
# A scoreboard is a few kilobytes; a file far beyond that is not one.
MAX_FILE_BYTES = 1_000_000

NOT_RUN_MESSAGE = "That scoreboard has not been written yet."


class ScoreboardReader:
    """Reads the scoreboard files of one folder."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def retrieval(self) -> RetrievalScoreboard:
        return self._read(RETRIEVAL_FILE, RetrievalScoreboard)

    def redaction(self) -> RedactionScoreboard:
        return self._read(REDACTION_FILE, RedactionScoreboard)

    def classification(self) -> ClassificationScoreboard:
        return self._read(CLASSIFICATION_FILE, ClassificationScoreboard)

    def _read[Board: ContractModel](self, name: str, model: type[Board]) -> Board:
        file = self._folder / name
        try:
            found = file.is_file()
            if found:
                if file.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError("too large")
                return model.model_validate_json(file.read_bytes())
        except (OSError, ValueError) as error:
            # security rule 31: the file's name and the error's type, never
            # the error's message, which quotes what the file holds. A file
            # that breaks its model raises pydantic's `ValidationError`,
            # which is a `ValueError`.
            fields = error.error_count() if isinstance(error, ValidationError) else 0
            logger.error(
                "scoreboard file is not usable: file=%s type=%s fields=%d",
                name,
                type(error).__qualname__,
                fields,
            )
            raise DomainError(
                ErrorCode.INTERNAL_ERROR, INTERNAL_ERROR_MESSAGE
            ) from None
        raise DomainError(ErrorCode.NOT_FOUND, NOT_RUN_MESSAGE)
