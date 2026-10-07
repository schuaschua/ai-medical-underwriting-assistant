"""Train the Document Intelligence classifier, once (spine AD-13, story 4.2).

What the training job does, apart from how it is started: when the
classifier of the configured id is there, nothing; otherwise the labelled
pages in the `classifier-training` container are checked, and the service is
asked to build the classifier from them.

The pages are not read here and not redacted here. They were redacted before
they came into the container, by the pipeline itself (each was uploaded as a
case), and the job calls no service of ours.
"""

from collections import Counter
from dataclasses import dataclass

from classification.domain.entities import ListedPage, StoredBlob
from classification.domain.ports import ClassifierBuilder, TrainingPages
from contracts.enums import PageType

# The fewest pages of one page type the classifier is trained on.
MIN_PAGES_PER_TYPE = 5


class TrainingRefused(Exception):
    """The pages in the container are not what a classifier may be trained on.

    `reason` is a short code for the log, and `subject` what it is about: a
    page type or a blob's name, never anything of a page.
    """

    def __init__(self, reason: str, subject: str, pages: int = 0) -> None:
        super().__init__(reason)
        self.reason = reason
        self.subject = subject
        self.pages = pages


@dataclass(frozen=True, slots=True)
class TrainReport:
    """How a run of the job ended."""

    # False when the classifier was there already and nothing was done.
    trained: bool
    pages: int = 0
    page_types: int = 0


def folder_of(page_type: PageType) -> str:
    """The folder of the container that holds the pages of one type."""
    return f"{page_type.value}/"


def check_pages(
    listed: list[ListedPage], blobs: list[StoredBlob]
) -> dict[PageType, int]:
    """How many pages each page type has, or `TrainingRefused`.

    The container must hold exactly the listed pages, each once and each
    with the content the list vouches for: the service takes one folder per
    type and learns from whatever is in it. So a blob the list does not
    name, a listed page that is missing or named twice, and a page whose
    content is not the prepared one (an unredacted page under a prepared
    page's name) each refuse the training. Every label must be a page type,
    every page lie in its type's folder, and every page type have at least
    `MIN_PAGES_PER_TYPE` pages.
    """
    names = Counter(page.file for page in listed)
    for name, times in names.items():
        if times > 1:
            raise TrainingRefused("page_listed_twice", name)
    stored = {blob.name: blob for blob in blobs}
    for blob in blobs:
        if blob.name not in names:
            raise TrainingRefused("page_without_label", blob.name)
    counts: Counter[PageType] = Counter()
    for page in listed:
        try:
            page_type = PageType(page.page_type)
        except ValueError:
            raise TrainingRefused("page_without_label", page.file) from None
        if not page.file.startswith(folder_of(page_type)):
            raise TrainingRefused("page_outside_its_folder", page.file)
        held = stored.get(page.file)
        if held is None:
            raise TrainingRefused("page_missing", page.file)
        if held.md5 is None or held.md5.lower() != page.md5.lower():
            raise TrainingRefused("page_content_differs", page.file)
        counts[page_type] += 1
    for page_type in PageType:
        if counts[page_type] < MIN_PAGES_PER_TYPE:
            raise TrainingRefused("too_few_pages", page_type.value, counts[page_type])
    return dict(counts)


async def train_classifier(
    *, pages: TrainingPages, builder: ClassifierBuilder
) -> TrainReport:
    """Build the classifier unless it exists; idempotent on the classifier id."""
    if await builder.exists():
        return TrainReport(trained=False)
    counts = check_pages(*await pages.contents())
    await builder.build(
        pages.container_url(), {kind.value: folder_of(kind) for kind in PageType}
    )
    return TrainReport(trained=True, pages=sum(counts.values()), page_types=len(counts))
