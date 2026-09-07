"""The data that flows down the pipeline."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal


class Verdict(Enum):
    """What `trim.py` decided about a document's final cell."""

    FILLER = "filler"
    WIDOW = "widow"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class Origin:
    """Where a document came from, and how to retire it afterwards."""

    kind: Literal["email", "url"]
    identifier: str
    uid: int | None = None


@dataclass(frozen=True, slots=True)
class DroppedImage:
    src: str
    reason: str


@dataclass(frozen=True, slots=True)
class DroppedBlock:
    """A top-level block clean.py judged to be chrome and removed.

    Kept for reporting, so a wrong removal is visible rather than
    invisible - the same principle trim.py already applies to a dropped
    cell.
    """

    text: str


@dataclass(frozen=True, slots=True)
class Document:
    """One thing to be printed, cleaned and ready to render."""

    origin: Origin
    publication: str
    title: str
    date: datetime
    html: str
    author: str | None = None
    images_kept: int = 0
    images_dropped: tuple[DroppedImage, ...] = ()
    blocks_dropped: tuple[DroppedBlock, ...] = ()
