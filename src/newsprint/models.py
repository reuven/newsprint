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
    """Where a document came from, and how to retire it afterwards.

    `folder` matters because a uid is only meaningful inside the folder
    it was issued in: uid 5 in one folder and uid 5 in another are
    different messages, so retiring by number alone would move the wrong
    mail once a run reads from more than one place.
    """

    kind: Literal["email", "url"]
    identifier: str
    uid: int | None = None
    folder: str | None = None


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

    `kind` distinguishes a genuine chrome removal (the default,
    "chrome" - text that is gone from the printed output entirely) from
    "duplicate_title" (clean.py's _strip_duplicate_title_block): that
    text is not lost, only de-duplicated - render.py synthesises its own
    headline from the message's Subject regardless, so the exact same
    text still appears in the printout. Reporting both the same way
    alarmed the user for real ('removed block: "This isn't just about
    Jaguar Land Rover (or VW)"' was the message's own subject, not data
    loss); see cli.py's own use of this field, and the derive-chrome
    spec's part E.
    """

    text: str
    kind: Literal["chrome", "duplicate_title"] = "chrome"


@dataclass(frozen=True, slots=True)
class Document:
    """One thing to be printed, cleaned and ready to render."""

    origin: Origin
    publication: str
    title: str
    date: datetime
    html: str
    author: str | None = None
    # The host that says where this newsletter comes from - the List-Id
    # host when the message has a usable one, else the sender's domain.
    # picker.source_label turns it into something to show beside a
    # publication name that does not identify its own newsletter.
    source_host: str | None = None
    images_kept: int = 0
    images_dropped: tuple[DroppedImage, ...] = ()
    blocks_dropped: tuple[DroppedBlock, ...] = ()
