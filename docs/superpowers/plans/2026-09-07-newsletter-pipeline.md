# Newsletter Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch starred newsletters from the IMAP `toprint` folder, typeset them into quarter-sheet cells, drop advertising-only trailing pages, impose four cells to a side, print duplex, and retire the printed mail.

**Architecture:** A straight pipeline of small modules. Only `mail.py` and `printer.py` perform I/O; everything between them is a pure function over data, tested offline against real newsletters sampled from the user's own mbox. `Document` is the seam: everything upstream deals in MIME, everything downstream in cleaned HTML and PDF pages.

**Tech Stack:** Python 3.14, uv, pytest, WeasyPrint (HTML→PDF), PyMuPDF (PDF text inspection), pypdf (imposition), BeautifulSoup + lxml (HTML cleaning), keyring (IMAP password), click (CLI).

**Spec:** `docs/superpowers/specs/2026-09-07-shabbat-print-design.md`

## Scope

This plan implements spec phases 1–5: the complete newsletter path from IMAP to
printer, including retirement. Phases 6–8 (interactive selection of unstarred
mail, image judgment, web articles) are separate plans and are explicitly **out
of scope** here.

Until phase 7 lands, `clean.py` strips **all** images. That is a deliberate
temporary simplification, not the final behaviour — the spec calls for keeping
content figures.

Two functions are built and tested here but not yet called by anything:
`Mailbox.search_unflagged_since` (Task 9) and `runlog.last_successful_run`
(Task 10). Together they define the review window, which plan 2 consumes. They
are included now because retirement and the run log are meaningless without the
log format they imply, and splitting them across plans would mean writing the
log twice.

## Global Constraints

- Python `>=3.14`; the project is managed with `uv`. Add dependencies with
  `uv add`, run everything with `uv run`. Never invoke `pip` or activate a venv.
- Type hints on every function, parameter, and return value.
- TDD without exception: write the failing test, watch it fail, implement, watch
  it pass, commit. 100% `pytest` coverage of `src/`.
- `ruff format` and `ruff check` clean before every commit.
- One logical change per commit. Push to `origin main` after every commit.
- Default paper is **A4**; `--paper letter` selects Letter. Cell size is always
  derived from the paper, never configured separately.
- The retirement invariant: **a message is modified only if its content reached
  a spooled print job.** Every failure path leaves mail untouched.
- No secrets in the repo or in config files. The IMAP password comes from
  `keyring` at run time.
- **Nothing personal in the source.** No email address, mail host, printer
  name, or filesystem path belonging to one user may appear in `src/`. Those
  live in the user's own config file. The tool assumes IMAP and CUPS, which is
  a fair assumption — Gmail, Fastmail, and most hosts speak IMAP — but it must
  assume nothing about *whose* IMAP. Tests supply their own values; they never
  assert on a real person's.
- `tests/fixtures/` is real personal mail and is gitignored. Never commit it.

---

### Task 1: Paper geometry, configuration, and domain models

The foundation every later task imports. The geometry carries the design's
load-bearing property: a cell is one quarter of a sheet, so tiling never scales
anything.

**Files:**
- Create: `src/shabbat_print/geometry.py`
- Create: `src/shabbat_print/models.py`
- Create: `src/shabbat_print/config.py`
- Create: `tests/test_geometry.py`
- Create: `tests/test_config.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing.
- Produces: `Size(width_mm, height_mm)` with `.aspect` and `.as_points() -> tuple[float, float]`; `Paper(name, sheet)` with `.cell -> Size`; `A4`, `LETTER`, `paper_by_name(str) -> Paper`; `Verdict` enum with `FILLER`/`WIDOW`/`FULL`; `Origin(kind, identifier, uid)`; `DroppedImage(src, reason)`; `Document(origin, publication, title, date, html, author, images_kept, images_dropped)`; `Config` with `.mail`, `.printing`, `.layout`, `.fallback_days`; `load_config(path, paper_override) -> Config`.

- [ ] **Step 1: Add development dependencies**

```bash
cd ~/Consulting/shabbat-print
uv add --dev pytest pytest-cov ruff
```

- [ ] **Step 2: Write the failing geometry tests**

Create `tests/test_geometry.py`:

```python
"""Geometry tests. The central claim is that a cell is a quarter of a sheet."""

import pytest

from shabbat_print.geometry import A4, LETTER, Paper, Size, paper_by_name


def test_a4_cell_is_a6() -> None:
    assert A4.cell.width_mm == pytest.approx(105.0)
    assert A4.cell.height_mm == pytest.approx(148.5)


def test_letter_cell_is_quarter_letter() -> None:
    assert LETTER.cell.width_mm == pytest.approx(4.25 * 25.4)
    assert LETTER.cell.height_mm == pytest.approx(5.5 * 25.4)


@pytest.mark.parametrize("paper", [A4, LETTER])
def test_four_cells_cover_the_sheet(paper: Paper) -> None:
    assert paper.cell.width_mm * 2 == pytest.approx(paper.sheet.width_mm)
    assert paper.cell.height_mm * 2 == pytest.approx(paper.sheet.height_mm)


@pytest.mark.parametrize("paper", [A4, LETTER])
def test_cell_aspect_matches_sheet(paper: Paper) -> None:
    """A 2x2 grid halves both dimensions, so the shape is preserved."""
    assert paper.cell.aspect == pytest.approx(paper.sheet.aspect)


def test_a4_cell_in_points() -> None:
    width_pt, height_pt = A4.cell.as_points()
    assert width_pt == pytest.approx(297.638, abs=0.01)
    assert height_pt == pytest.approx(420.945, abs=0.01)


@pytest.mark.parametrize("name", ["a4", "A4", "  A4  "])
def test_paper_by_name_is_forgiving(name: str) -> None:
    assert paper_by_name(name) is A4


def test_paper_by_name_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown paper"):
        paper_by_name("foolscap")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_geometry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shabbat_print.geometry'`

- [ ] **Step 4: Implement the geometry**

Create `src/shabbat_print/geometry.py`:

```python
"""Paper and cell geometry.

A cell is one quarter of a sheet, arranged as a 2x2 grid. Because that halves
both dimensions at once, a cell always has the same aspect ratio as the sheet,
so `impose.py` can tile cells at 100% scale and nothing is ever resampled.
"""

from dataclasses import dataclass

MM_PER_INCH = 25.4
POINTS_PER_INCH = 72.0


@dataclass(frozen=True, slots=True)
class Size:
    """A rectangle, held in millimetres."""

    width_mm: float
    height_mm: float

    @property
    def aspect(self) -> float:
        return self.width_mm / self.height_mm

    def as_points(self) -> tuple[float, float]:
        """PDF geometry is in points; this is the only conversion site."""
        scale = POINTS_PER_INCH / MM_PER_INCH
        return (self.width_mm * scale, self.height_mm * scale)


@dataclass(frozen=True, slots=True)
class Paper:
    """A sheet, and the cell derived from it."""

    name: str
    sheet: Size

    @property
    def cell(self) -> Size:
        """One quarter of the sheet: half the width, half the height."""
        return Size(self.sheet.width_mm / 2, self.sheet.height_mm / 2)


A4 = Paper("A4", Size(210.0, 297.0))
LETTER = Paper("Letter", Size(8.5 * MM_PER_INCH, 11.0 * MM_PER_INCH))

PAPERS: dict[str, Paper] = {"a4": A4, "letter": LETTER}


def paper_by_name(name: str) -> Paper:
    key = name.strip().lower()
    if key not in PAPERS:
        known = ", ".join(sorted(PAPERS))
        raise ValueError(f"unknown paper {name!r}; known papers are: {known}")
    return PAPERS[key]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_geometry.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 6: Write the domain models**

No separate test file — these are data holders exercised by every later task.
Create `src/shabbat_print/models.py`:

```python
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
```

- [ ] **Step 7: Write the failing config tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

import pytest

from shabbat_print.config import load_config
from shabbat_print.geometry import A4, LETTER

SAMPLE = """
[mail]
host = "imap.example.com"
user = "someone@example.com"
folder = "INBOX/queue"

[print]
printer = "Test_Printer"
paper = "Letter"

[layout]
font_size_pt = 10.0
"""


def test_missing_file_yields_structural_defaults(tmp_path: Path) -> None:
    """Defaults describe the layout, never a person."""
    config = load_config(tmp_path / "absent.toml")
    assert config.mail.host == ""
    assert config.mail.user == ""
    assert config.printing.printer == ""
    assert config.mail.folder == "INBOX/toprint"
    assert config.printing.paper is A4
    assert config.layout.margin_mm == pytest.approx(9.0)
    assert config.fallback_days == 7


def test_no_personal_data_hides_in_the_defaults() -> None:
    """A guard for the open-source goal: catch a stray address or hostname."""
    import re

    from shabbat_print.config import DEFAULTS

    flattened = repr(DEFAULTS)
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", flattened)
    assert "emailsrvr" not in flattened
    assert not re.search(r"imap\.\w", flattened)


def test_require_mail_names_what_is_missing(tmp_path: Path) -> None:
    from shabbat_print.config import ConfigError

    config = load_config(tmp_path / "absent.toml")
    with pytest.raises(ConfigError, match="mail.host and mail.user"):
        config.require_mail()


def test_require_mail_passes_when_configured(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    load_config(path).require_mail()  # must not raise


def test_file_values_override_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    config = load_config(path)
    assert config.mail.host == "imap.example.com"
    assert config.mail.folder == "INBOX/queue"
    assert config.printing.printer == "Test_Printer"
    assert config.printing.paper is LETTER
    assert config.layout.font_size_pt == pytest.approx(10.0)


def test_unspecified_keys_keep_their_defaults(tmp_path: Path) -> None:
    """A partial [layout] table must not wipe out the other layout keys."""
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    config = load_config(path)
    assert config.layout.margin_mm == pytest.approx(9.0)
    assert config.layout.line_height == pytest.approx(1.35)


def test_paper_override_beats_the_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    config = load_config(path, paper_override="a4")
    assert config.printing.paper is A4


def test_unknown_paper_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[print]\npaper = "foolscap"\n')
    with pytest.raises(ValueError, match="unknown paper"):
        load_config(path)
```

- [ ] **Step 8: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shabbat_print.config'`

- [ ] **Step 9: Implement the config loader**

Create `src/shabbat_print/config.py`:

```python
"""Configuration, merged over defaults.

The password is deliberately absent: it is read from the macOS Keychain at run
time, so it never appears in a config file or in the repository.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .geometry import Paper, paper_by_name

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "shabbat-print" / "config.toml"

# Structural defaults only. Nothing here identifies a person, a mail host, or
# a printer: those come from the user's own config file. An empty printer name
# means "whatever CUPS treats as the default destination".
DEFAULTS: dict[str, dict[str, Any]] = {
    "mail": {"host": "", "user": "", "folder": "INBOX/toprint", "trash": "auto"},
    "print": {"printer": "", "paper": "A4", "duplex": "two-sided-long-edge"},
    "layout": {"margin_mm": 9.0, "font_size_pt": 9.0, "line_height": 1.35},
    "window": {"fallback_days": 7},
}


class ConfigError(Exception):
    """The configuration is missing something the run needs."""


@dataclass(frozen=True, slots=True)
class MailConfig:
    host: str
    user: str
    folder: str
    trash: str


@dataclass(frozen=True, slots=True)
class PrintConfig:
    printer: str
    paper: Paper
    duplex: str


@dataclass(frozen=True, slots=True)
class LayoutConfig:
    margin_mm: float
    font_size_pt: float
    line_height: float


@dataclass(frozen=True, slots=True)
class Config:
    mail: MailConfig
    printing: PrintConfig
    layout: LayoutConfig
    fallback_days: int
    path: Path

    def require_mail(self) -> None:
        """Fail early, and say exactly what to put where.

        Checked at the point of use rather than at load time, so that the
        parts of the tool that never touch mail stay usable without a
        configured account.
        """
        missing = [name for name in ("host", "user") if not getattr(self.mail, name)]
        if missing:
            keys = " and ".join(f"mail.{name}" for name in missing)
            raise ConfigError(
                f"{self.path}: {keys} must be set. "
                f"Copy config.example.toml to {self.path} and fill it in."
            )


def _merged(path: Path) -> dict[str, dict[str, Any]]:
    """Overlay the file's tables onto the defaults, key by key.

    Merging per key rather than per table matters: a config that sets only
    `font_size_pt` must not discard the default margin and line height.
    """
    merged = {section: dict(values) for section, values in DEFAULTS.items()}
    if not path.exists():
        return merged
    loaded = tomllib.loads(path.read_text())
    for section, values in loaded.items():
        merged.setdefault(section, {}).update(values)
    return merged


def load_config(
    path: Path = DEFAULT_CONFIG_PATH, paper_override: str | None = None
) -> Config:
    data = _merged(path)
    paper_name = paper_override if paper_override is not None else data["print"]["paper"]
    return Config(
        mail=MailConfig(**data["mail"]),
        printing=PrintConfig(
            printer=data["print"]["printer"],
            paper=paper_by_name(paper_name),
            duplex=data["print"]["duplex"],
        ),
        layout=LayoutConfig(**data["layout"]),
        fallback_days=data["window"]["fallback_days"],
        path=path,
    )
```

- [ ] **Step 10: Run the tests to verify they pass**

Run: `uv run pytest tests/ -v`
Expected: PASS, 14 tests.

- [ ] **Step 11: Write the example config**

This is the file a new user copies. It is the only place the tool documents
what it needs, so it carries a comment per key.

Create `config.example.toml` at the repository root:

```toml
# Copy to ~/.config/shabbat-print/config.toml and edit.
# The IMAP password is NOT stored here. Put it in your system keychain:
#     keyring set <mail.host> <mail.user>

[mail]
host   = "imap.gmail.com"        # e.g. imap.gmail.com, imap.fastmail.com
user   = "you@example.com"
folder = "INBOX/toprint"         # the folder you star things into
trash  = "auto"                  # "auto" discovers the \Trash folder

[print]
printer = ""                     # empty means your system default printer
paper   = "A4"                   # or "Letter"; override per run with --paper
duplex  = "two-sided-long-edge"

[layout]
margin_mm   = 9.0
font_size_pt = 9.0
line_height = 1.35

[window]
fallback_days = 7                # how far back to look with no previous run
```

Gmail note for the README later: Gmail requires an app password rather than
the account password, and its folder separator makes the queue folder look
like `toprint` rather than `INBOX/toprint`.

- [ ] **Step 12: Format, lint, commit, and push**

```bash
cd ~/Consulting/shabbat-print
uv run ruff format src tests
uv run ruff check src tests
git add -A
git commit -m "Add paper geometry, domain models, and configuration"
git push origin main
```

---

### Task 2: Mbox splitter and the fixture harness

Every later task is tested against real newsletters. This task produces them,
and carries the two guards that a previous archive project learned the hard way.

**Files:**
- Create: `src/shabbat_print/mbox.py`
- Create: `scripts/make_fixtures.py`
- Create: `tests/test_mbox.py`
- Modify: `Makefile` (create if absent)

**Interfaces:**
- Consumes: nothing.
- Produces: `split_mbox(path: Path) -> Iterator[bytes]`, yielding each message including its `From ` separator line, and raising `MboxIntegrityError` if the parsed bytes do not account for the whole file.

- [ ] **Step 1: Write the failing splitter tests**

Create `tests/test_mbox.py`:

```python
"""Splitter tests, built around the two ways this has silently failed before."""

from pathlib import Path

import pytest

from shabbat_print.mbox import (
    MboxIntegrityError,
    find_thunderbird_mbox,
    split_mbox,
)

NORMAL = (
    b"From sender@example.com Fri Sep  5 10:00:00 2026\r\n"
    b"From: sender@example.com\r\n"
    b"Subject: First\r\n"
    b"\r\n"
    b"Body of the first message.\r\n"
    b"\r\n"
)

SECOND = (
    b"From other@example.com Sat Sep  6 11:00:00 2026\r\n"
    b"From: other@example.com\r\n"
    b"Subject: Second\r\n"
    b"\r\n"
    b"Body of the second message.\r\n"
    b"\r\n"
)

# Thunderbird writes a separator with nothing after "From " but a carriage
# return. A splitter demanding a trailing four-digit year reads a whole folder
# as one message and reports it as safe to delete.
BARE = (
    b"From \r\n"
    b"From: third@example.com\r\n"
    b"Subject: Third\r\n"
    b"\r\n"
    b"Body of the third message.\r\n"
    b"\r\n"
)


def _write(tmp_path: Path, *chunks: bytes) -> Path:
    path = tmp_path / "folder"
    path.write_bytes(b"".join(chunks))
    return path


def test_splits_normal_separators(tmp_path: Path) -> None:
    messages = list(split_mbox(_write(tmp_path, NORMAL, SECOND)))
    assert len(messages) == 2
    assert b"Subject: First" in messages[0]
    assert b"Subject: Second" in messages[1]


def test_splits_a_bare_thunderbird_separator(tmp_path: Path) -> None:
    messages = list(split_mbox(_write(tmp_path, NORMAL, BARE)))
    assert len(messages) == 2
    assert b"Subject: Third" in messages[1]


def test_a_body_line_beginning_with_From_does_not_split(tmp_path: Path) -> None:
    """"From " mid-body is not a separator: no blank line before it, and the
    next line is not a header."""
    body = (
        b"From sender@example.com Fri Sep  5 10:00:00 2026\r\n"
        b"From: sender@example.com\r\n"
        b"Subject: Quoting\r\n"
        b"\r\n"
        b"She wrote:\r\n"
        b"From now on we do it differently.\r\n"
        b"And that was that.\r\n"
        b"\r\n"
    )
    assert len(list(split_mbox(_write(tmp_path, body)))) == 1


def test_every_byte_is_accounted_for(tmp_path: Path) -> None:
    path = _write(tmp_path, NORMAL, SECOND, BARE)
    parsed = sum(len(message) for message in split_mbox(path))
    assert parsed == path.stat().st_size


def test_integrity_error_when_bytes_go_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate the buffered-read bug: make the reader return a short file."""
    path = _write(tmp_path, NORMAL, SECOND)
    monkeypatch.setattr(
        Path, "read_bytes", lambda self: NORMAL + SECOND[: len(SECOND) // 2]
    )
    with pytest.raises(MboxIntegrityError, match="bytes"):
        list(split_mbox(path))


def test_empty_file_yields_nothing(tmp_path: Path) -> None:
    assert list(split_mbox(_write(tmp_path, b""))) == []


def test_no_thunderbird_profile_means_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert find_thunderbird_mbox() is None


def test_a_cached_folder_is_found_whatever_the_profile_is_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The profile directory and mail host are per-user; neither is hardcoded."""
    target = (
        tmp_path
        / "Library/Thunderbird/Profiles/xyz789.default"
        / "ImapMail/imap.example.com/INBOX.sbd/toprint"
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(NORMAL)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert find_thunderbird_mbox() == target
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_mbox.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shabbat_print.mbox'`

- [ ] **Step 3: Implement the splitter**

Create `src/shabbat_print/mbox.py`:

```python
"""Split a Thunderbird mbox into individual messages.

Two failure modes have bitten this before, both silent, both nearly
destructive:

1. Thunderbird writes a bare `From \\r` separator with no sender and no date.
   A splitter that requires the usual trailing four-digit year read a 1.1 GB,
   8,735-message folder as two messages.
2. A buffered reader flushing on a fixed size dropped 60 MB from a folder
   holding two 95 MB messages.

The defence against both is the same and is not optional: assert that the
bytes parsed add up to the size of the file.
"""

import re
from collections.abc import Iterator
from pathlib import Path

# A field name followed by a colon: RFC 5322 printable ASCII except colon.
_HEADER = re.compile(rb"[!-9;-~]+:")
_CANDIDATE = re.compile(rb"(?m)^From ")


class MboxIntegrityError(Exception):
    """The parsed messages do not account for every byte of the file."""


def _preceded_by_blank_line(data: bytes, position: int) -> bool:
    if position == 0:
        return True
    if data[position - 1 : position] != b"\n":
        return False
    if data[position - 2 : position - 1] == b"\n":
        return True
    return data[position - 3 : position - 1] == b"\r\n"


def _followed_by_header(data: bytes, position: int) -> bool:
    end_of_line = data.find(b"\n", position)
    if end_of_line == -1:
        return False
    return _HEADER.match(data, end_of_line + 1) is not None


def _separator_positions(data: bytes) -> list[int]:
    return [
        match.start()
        for match in _CANDIDATE.finditer(data)
        if _preceded_by_blank_line(data, match.start())
        and _followed_by_header(data, match.start())
    ]


def split_mbox(path: Path) -> Iterator[bytes]:
    """Yield each message, separator line included.

    Reads the whole file at once. At 201 MB that is entirely affordable, and it
    removes the buffering that caused the second failure above.
    """
    size = path.stat().st_size
    data = path.read_bytes()
    starts = _separator_positions(data)
    if not starts:
        if data:
            raise MboxIntegrityError(
                f"{path}: {len(data)} bytes but no message separator found"
            )
        return

    bounds = list(zip(starts, [*starts[1:], len(data)], strict=True))
    parsed = starts[0]  # any preamble before the first separator
    messages = []
    for begin, end in bounds:
        chunk = data[begin:end]
        parsed += len(chunk)
        messages.append(chunk)

    if parsed != size:
        raise MboxIntegrityError(
            f"{path}: parsed {parsed} bytes but the file holds {size}"
        )
    yield from messages


def find_thunderbird_mbox(folder: str = "toprint") -> Path | None:
    """Locate a local Thunderbird cache of an IMAP folder, if there is one.

    A development convenience for building test fixtures - not part of the
    tool's runtime. shabbat-print itself talks to IMAP and never reads a local
    mail store, so this returning None is normal on most machines.
    """
    root = Path.home() / "Library" / "Thunderbird" / "Profiles"
    candidates = sorted(root.glob(f"*/ImapMail/*/INBOX.sbd/{folder}"))
    return candidates[0] if candidates else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mbox.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Write the fixture generator**

Create `scripts/make_fixtures.py`:

```python
"""Sample one newsletter per sender into tests/fixtures/.

The fixtures are real personal mail and are gitignored. Regenerate them with
`make fixtures`.
"""

import email
import re
import sys
from email.message import Message
from pathlib import Path

from shabbat_print.mbox import find_thunderbird_mbox, split_mbox

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
UNSAFE = re.compile(r"[^a-z0-9]+")


def sender_of(message: Message) -> str:
    _, address = email.utils.parseaddr(message.get("From", ""))
    return address.lower()


def main() -> int:
    mbox = Path(sys.argv[1]) if len(sys.argv) > 1 else find_thunderbird_mbox()
    if mbox is None or not mbox.exists():
        print(
            "no local Thunderbird mbox found; pass the path as an argument",
            file=sys.stderr,
        )
        return 1
    print(f"reading {mbox}")
    FIXTURES.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    written = 0
    for raw in split_mbox(mbox):
        message = email.message_from_bytes(raw)
        sender = sender_of(message)
        if not sender or sender in seen:
            continue
        seen.add(sender)
        name = UNSAFE.sub("-", sender).strip("-")
        (FIXTURES / f"{name}.eml").write_bytes(raw)
        written += 1
    print(f"wrote {written} fixtures to {FIXTURES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Add the Makefile target**

Create `Makefile`:

```make
.PHONY: fixtures test lint

fixtures:
	uv run python scripts/make_fixtures.py

test:
	uv run pytest --cov=shabbat_print --cov-report=term-missing

lint:
	uv run ruff format src tests scripts
	uv run ruff check src tests scripts
```

- [ ] **Step 7: Generate the fixtures and verify against the real mbox**

```bash
cd ~/Consulting/shabbat-print
make fixtures
ls tests/fixtures | wc -l
```

Expected: roughly 200–400 `.eml` files (one per distinct sender), and no
`MboxIntegrityError`. If an integrity error appears, **stop** — the splitter is
wrong, and this is exactly the failure the guard exists to catch.

- [ ] **Step 8: Add the real-mbox integrity test**

Append to `tests/test_mbox.py`:

```python
REAL_MBOX = find_thunderbird_mbox()


@pytest.mark.skipif(REAL_MBOX is None, reason="no local Thunderbird mbox")
def test_real_mbox_parses_completely() -> None:
    """The guard that matters, run against a large real folder.

    Skipped on any machine without a local Thunderbird profile, which is most
    of them.
    """
    parsed = sum(len(message) for message in split_mbox(REAL_MBOX))
    assert parsed == REAL_MBOX.stat().st_size
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mbox.py -v`
Expected: PASS, 7 tests. The real-mbox test may take a few seconds.

- [ ] **Step 10: Format, lint, commit, and push**

```bash
cd ~/Consulting/shabbat-print
make lint
git add -A
git commit -m "Add mbox splitter with byte-accounting guard, and fixture harness"
git push origin main
```

---

### Task 3: Extract a Document from a MIME message

**Files:**
- Create: `src/shabbat_print/extract.py`
- Create: `tests/test_extract.py`

**Interfaces:**
- Consumes: `Document`, `Origin` from Task 1.
- Produces: `extract(raw: bytes, uid: int | None = None, names: Mapping[str, str] | None = None) -> Document`. The returned `Document.html` is the message's **raw** HTML; cleaning happens in Task 4.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_extract.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

import pytest

from shabbat_print.extract import extract

FIXTURES = Path(__file__).parent / "fixtures"


def message(headers: str, body: str) -> bytes:
    return (headers.strip() + "\n\n" + body).encode()


HTML_MESSAGE = message(
    """
From: Matt Levine <noreply@news.bloomberg.com>
Subject: Money Stuff: Private Credit Gets Complicated
Date: Fri, 5 Sep 2026 14:22:55 +0000
Message-ID: <abc123@bloomberg.net>
List-Id: Money Stuff <moneystuff.bloomberg.com>
Content-Type: text/html; charset="utf-8"
""",
    "<html><body><p>Private credit is having a moment.</p></body></html>",
)


def test_reads_the_obvious_fields() -> None:
    document = extract(HTML_MESSAGE, uid=42)
    assert document.title == "Money Stuff: Private Credit Gets Complicated"
    assert document.origin.identifier == "<abc123@bloomberg.net>"
    assert document.origin.uid == 42
    assert document.origin.kind == "email"
    assert "Private credit is having a moment." in document.html


def test_date_is_timezone_aware() -> None:
    document = extract(HTML_MESSAGE)
    assert document.date == datetime(2026, 9, 5, 14, 22, 55, tzinfo=timezone.utc)


def test_publication_comes_from_list_id() -> None:
    assert extract(HTML_MESSAGE).publication == "Money Stuff"


def test_publication_falls_back_to_display_name() -> None:
    raw = message(
        """
From: The Economist <noreply@e.economist.com>
Subject: Espresso
Date: Sat, 6 Sep 2026 06:00:00 +0000
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Good morning.</p></body></html>",
    )
    assert extract(raw).publication == "The Economist"


def test_publication_falls_back_to_domain() -> None:
    raw = message(
        """
From: noreply@dailydoseofds.com
Subject: Today's dose
Date: Sat, 6 Sep 2026 06:00:00 +0000
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hello.</p></body></html>",
    )
    assert extract(raw).publication == "dailydoseofds.com"


def test_names_override_wins() -> None:
    names = {"noreply@news.bloomberg.com": "Money Stuff (Bloomberg)"}
    assert extract(HTML_MESSAGE, names=names).publication == "Money Stuff (Bloomberg)"


def test_encoded_subject_is_decoded() -> None:
    raw = message(
        """
From: Simon Willison <simon@example.com>
Subject: =?utf-8?B?U2ltb24ncyBOZXdzbGV0dGVy?=
Date: Sat, 6 Sep 2026 06:00:00 +0000
Content-Type: text/html; charset="utf-8"
""",
        "<html><body><p>Hi.</p></body></html>",
    )
    assert extract(raw).title == "Simon's Newsletter"


def test_plain_text_only_message_is_wrapped() -> None:
    raw = message(
        """
From: Someone <someone@example.com>
Subject: Plain
Date: Sat, 6 Sep 2026 06:00:00 +0000
Content-Type: text/plain; charset="utf-8"
""",
        "Just words.\nAnd <angle> brackets.",
    )
    html = extract(raw).html
    assert "<pre>" in html
    assert "&lt;angle&gt;" in html


def test_the_largest_html_part_wins() -> None:
    raw = (
        b"From: Someone <someone@example.com>\n"
        b"Subject: Multipart\n"
        b"Date: Sat, 6 Sep 2026 06:00:00 +0000\n"
        b'Content-Type: multipart/alternative; boundary="B"\n'
        b"\n"
        b"--B\n"
        b'Content-Type: text/html; charset="utf-8"\n'
        b"\n"
        b"<p>short</p>\n"
        b"--B\n"
        b'Content-Type: text/html; charset="utf-8"\n'
        b"\n"
        b"<p>this alternative is considerably longer than the other one</p>\n"
        b"--B--\n"
    )
    assert "considerably longer" in extract(raw).html


@pytest.mark.skipif(not FIXTURES.exists(), reason="run `make fixtures` first")
def test_every_fixture_extracts() -> None:
    """Real mail, every sender. Nothing may raise, and nothing may come back
    without a publication and a title."""
    paths = sorted(FIXTURES.glob("*.eml"))
    assert paths, "no fixtures; run `make fixtures`"
    for path in paths:
        document = extract(path.read_bytes())
        assert document.publication, path.name
        assert document.html, path.name
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shabbat_print.extract'`

- [ ] **Step 3: Implement the extractor**

Create `src/shabbat_print/extract.py`:

```python
"""Turn a MIME message into a Document.

The HTML that comes out is the message's own, untouched. Cleaning is a
separate concern and lives in clean.py.
"""

import email
import email.utils
from collections.abc import Mapping
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from html import escape

from .models import Document, Origin


def _header(message: Message, name: str, default: str = "") -> str:
    raw = message.get(name)
    if raw is None:
        return default
    return str(make_header(decode_header(raw)))


def _decode(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _body_html(message: Message) -> str:
    """Prefer the largest text/html part; fall back to text/plain."""
    html_parts: list[str] = []
    text_parts: list[str] = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if "attachment" in (part.get("Content-Disposition") or "").lower():
            continue
        content_type = part.get_content_type()
        if content_type == "text/html":
            html_parts.append(_decode(part))
        elif content_type == "text/plain":
            text_parts.append(_decode(part))

    if html_parts:
        return max(html_parts, key=len)
    if text_parts:
        return f"<html><body><pre>{escape(max(text_parts, key=len))}</pre></body></html>"
    return "<html><body></body></html>"


def _publication(message: Message, address: str, names: Mapping[str, str]) -> str:
    if address in names:
        return names[address]

    list_id = _header(message, "List-Id")
    if list_id:
        label = list_id.split("<")[0].strip().strip('"').strip()
        if label:
            return label

    display, _ = email.utils.parseaddr(_header(message, "From"))
    if display:
        return display

    return address.partition("@")[2] or "unknown"


def _date(message: Message) -> datetime:
    raw = message.get("Date")
    if raw:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
    return datetime.now(timezone.utc)


def extract(
    raw: bytes,
    uid: int | None = None,
    names: Mapping[str, str] | None = None,
) -> Document:
    message = email.message_from_bytes(raw)
    display, raw_address = email.utils.parseaddr(_header(message, "From"))
    address = raw_address.lower()

    return Document(
        origin=Origin(
            kind="email",
            identifier=_header(message, "Message-ID", default=address),
            uid=uid,
        ),
        publication=_publication(message, address, names or {}),
        title=_header(message, "Subject", default="(no subject)"),
        author=display or None,
        date=_date(message),
        html=_body_html(message),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_extract.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Write the failing publication-names test**

The spec calls for a `publications.toml` so a cell reads "Money Stuff" rather
than `noreply@news.bloomberg.com`. `extract()` already accepts the mapping;
this loads it.

Append to `tests/test_config.py`:

```python
def test_publication_names_default_to_empty(tmp_path: Path) -> None:
    from shabbat_print.config import load_publication_names

    assert load_publication_names(tmp_path / "absent.toml") == {}


def test_publication_names_are_lowercased(tmp_path: Path) -> None:
    from shabbat_print.config import load_publication_names

    path = tmp_path / "publications.toml"
    path.write_text('[names]\n"NoReply@News.Bloomberg.com" = "Money Stuff"\n')
    assert load_publication_names(path) == {
        "noreply@news.bloomberg.com": "Money Stuff"
    }
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_publication_names'`

- [ ] **Step 7: Implement the loader**

Append to `src/shabbat_print/config.py`:

```python
DEFAULT_PUBLICATIONS_PATH = (
    Path.home() / ".config" / "shabbat-print" / "publications.toml"
)


def load_publication_names(
    path: Path = DEFAULT_PUBLICATIONS_PATH,
) -> dict[str, str]:
    """Map a sender address to the name that should appear on the cell.

    Addresses are compared in lower case, because senders are inconsistent
    about capitalisation and the mapping should not be.
    """
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text())
    return {
        address.lower(): name for address, name in data.get("names", {}).items()
    }
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/ -v`
Expected: PASS.

- [ ] **Step 9: Seed publications.toml from the real senders**

```bash
mkdir -p ~/.config/shabbat-print
uv run python - <<'SEED'
import email, email.utils, collections
from pathlib import Path
from shabbat_print.mbox import find_thunderbird_mbox, split_mbox

mbox = find_thunderbird_mbox()
if mbox is None:
    raise SystemExit("no local Thunderbird mbox found")
counts = collections.Counter()
names = {}
for raw in split_mbox(mbox):
    message = email.message_from_bytes(raw)
    display, address = email.utils.parseaddr(message.get("From", ""))
    if not address:
        continue
    address = address.lower()
    counts[address] += 1
    names.setdefault(address, display or address)

lines = ["# Sender address -> the name printed on the cell.", "[names]"]
for address, count in counts.most_common():
    lines.append(f'"{address}" = "{names[address]}"   # {count} messages')
Path.home().joinpath(".config/shabbat-print/publications.toml").write_text(
    "\n".join(lines) + "\n"
)
print(f"seeded {len(counts)} senders")
SEED
```

Then edit the file by hand: the display names Substack and Beehiiv send are
often the author's name rather than the publication's, and this is the one
place to fix that.

- [ ] **Step 10: Format, lint, commit, and push**

```bash
cd ~/Consulting/shabbat-print
make lint
git add -A
git commit -m "Extract a Document from a MIME message"
git push origin main
```

---

### Task 4: Boilerplate classification and content cleaning

The heart of the paper saving. `clean.py` emits **only** what it judges to be
article content, so chrome is never rendered and a filler page mostly cannot
come into existence.

`boilerplate.py` is a separate module on purpose: both `clean.py` and Task 6's
`trim.py` need the same judgment, and putting it here means `trim` never has to
import `clean`.

**Files:**
- Create: `src/shabbat_print/boilerplate.py`
- Create: `src/shabbat_print/clean.py`
- Create: `tests/test_boilerplate.py`
- Create: `tests/test_clean.py`

**Interfaces:**
- Consumes: `Document`, `DroppedImage` from Task 1.
- Produces: `is_boilerplate_line(line: str) -> bool`; `content_ratio(text: str) -> float`; `clean_document(document: Document) -> Document` returning a new `Document` whose `html` is content-only and whose `images_dropped` records what went.

- [ ] **Step 1: Write the failing boilerplate tests**

Create `tests/test_boilerplate.py`:

```python
import pytest

from shabbat_print.boilerplate import content_ratio, is_boilerplate_line

PROSE = "Private credit is having a moment, and not entirely a good one."


@pytest.mark.parametrize(
    "line",
    [
        "Unsubscribe",
        "You are receiving this because you signed up.",
        "View this email in your browser",
        "Manage your preferences",
        "© 2026 Bloomberg L.P. All rights reserved.",
        "https://example.com/some/tracking/link",
        "www.example.com",
        "Read more",
        "Sponsored by Acme",
    ],
)
def test_boilerplate_lines(line: str) -> None:
    assert is_boilerplate_line(line) is True


@pytest.mark.parametrize(
    "line",
    [
        PROSE,
        "The Federal Reserve declined to move rates this month, again.",
        "She wrote that the deal was, in her words, entirely unremarkable.",
    ],
)
def test_content_lines(line: str) -> None:
    assert is_boilerplate_line(line) is False


def test_blank_lines_are_not_boilerplate() -> None:
    assert is_boilerplate_line("   ") is False


def test_content_ratio_of_pure_footer_is_zero() -> None:
    footer = "Unsubscribe\nManage your preferences\nhttps://example.com/x\n"
    assert content_ratio(footer) == pytest.approx(0.0)


def test_content_ratio_of_pure_prose_is_one() -> None:
    assert content_ratio(PROSE) == pytest.approx(1.0)


def test_content_ratio_is_by_characters_not_lines() -> None:
    """A long ad page must score low even though it is long."""
    text = PROSE + "\n" + "\n".join(["Sponsored by Acme"] * 20)
    assert content_ratio(text) < 0.15


def test_content_ratio_of_empty_text_is_zero() -> None:
    assert content_ratio("\n \n") == pytest.approx(0.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_boilerplate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shabbat_print.boilerplate'`

- [ ] **Step 3: Implement the boilerplate classifier**

Create `src/shabbat_print/boilerplate.py`:

```python
"""Deciding what is newsletter chrome and what is newsletter content.

The rule is about what the text *is*, never how much of it there is: 1,200
characters of pure link roundup is chrome, and 200 characters of a real
closing paragraph is content.
"""

import re

PHRASES: tuple[str, ...] = (
    "unsubscribe",
    "manage your preferences",
    "manage preferences",
    "email preferences",
    "update your profile",
    "view in browser",
    "view this email",
    "you are receiving this",
    "you're receiving this",
    "you received this",
    "was this forwarded",
    "forward to a friend",
    "add us to your address book",
    "mailing address",
    "all rights reserved",
    "privacy policy",
    "terms of service",
    "sponsored by",
    "advertisement",
    "presented by",
    "follow us",
    "share this",
    "thanks for reading",
    "see you next week",
    "until next time",
    "©",
)

_URL_ONLY = re.compile(r"^(https?://\S+|www\.\S+)$", re.IGNORECASE)
_SENTENCE_END = (".", "!", "?", '"', "'", ")", ":", "”", "’")

# Below this, a line without sentence-ending punctuation reads as a
# link-roundup item or a navigation label rather than as prose.
SHORT_LINE = 40


def is_boilerplate_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if any(phrase in lowered for phrase in PHRASES):
        return True
    if _URL_ONLY.match(stripped):
        return True
    return len(stripped) < SHORT_LINE and not stripped.endswith(_SENTENCE_END)


def content_ratio(text: str) -> float:
    """Fraction of non-blank characters that look like real content."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    total = sum(len(line) for line in lines)
    content = sum(len(line) for line in lines if not is_boilerplate_line(line))
    return content / total
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_boilerplate.py -v`
Expected: PASS, 19 tests.

- [ ] **Step 5: Add the HTML dependencies**

```bash
cd ~/Consulting/shabbat-print
uv add beautifulsoup4 lxml
```

- [ ] **Step 6: Write the failing cleaner tests**

Create `tests/test_clean.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

import pytest

from shabbat_print.clean import clean_document
from shabbat_print.models import Document, Origin

FIXTURES = Path(__file__).parent / "fixtures"


def document(html: str) -> Document:
    return Document(
        origin=Origin(kind="email", identifier="<x@example.com>"),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=timezone.utc),
        html=html,
    )


NEWSLETTER = """
<html><head><style>p { color: red }</style></head><body>
  <div><a href="https://example.com/view">View this email in your browser</a></div>
  <div>
    <h1>The Fed Declines Again</h1>
    <p>The Federal Reserve declined to move rates this month, which surprised
       almost nobody who had been paying attention to the minutes.</p>
    <p>What happens next is the interesting part, and it turns on a detail
       buried in a footnote that almost no one has read carefully.</p>
  </div>
  <div>
    <a href="https://example.com/a">Read more</a><br>
    <a href="https://example.com/b">Another link</a><br>
    <a href="https://example.com/c">And a third</a>
  </div>
  <div>
    <p>You are receiving this because you signed up.</p>
    <p><a href="https://example.com/u">Unsubscribe</a> |
       <a href="https://example.com/p">Manage your preferences</a></p>
    <p>© 2026 Test Weekly. All rights reserved.</p>
  </div>
</body></html>
"""


def test_content_survives() -> None:
    cleaned = clean_document(document(NEWSLETTER))
    assert "The Fed Declines Again" in cleaned.html
    assert "surprised" in cleaned.html
    assert "buried in a footnote" in cleaned.html


def test_trailing_chrome_is_removed() -> None:
    cleaned = clean_document(document(NEWSLETTER))
    assert "Unsubscribe" not in cleaned.html
    assert "All rights reserved" not in cleaned.html
    assert "You are receiving this" not in cleaned.html


def test_trailing_link_roundup_is_removed() -> None:
    cleaned = clean_document(document(NEWSLETTER))
    assert "Another link" not in cleaned.html


def test_leading_chrome_is_removed() -> None:
    cleaned = clean_document(document(NEWSLETTER))
    assert "View this email" not in cleaned.html


def test_style_and_script_are_removed() -> None:
    cleaned = clean_document(document(NEWSLETTER))
    assert "color: red" not in cleaned.html


def test_images_are_dropped_and_recorded() -> None:
    html = (
        '<html><body><div><p>Real prose that continues for a good while '
        'here.</p>'
        '<img src="https://example.com/pixel.gif" width="1" height="1">'
        '<img src="https://example.com/chart.png" width="600">'
        "</div></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "<img" not in cleaned.html
    assert cleaned.images_kept == 0
    assert {dropped.src for dropped in cleaned.images_dropped} == {
        "https://example.com/pixel.gif",
        "https://example.com/chart.png",
    }


def test_table_wrapped_content_is_found() -> None:
    """Email HTML nests content inside layout tables; the cleaner must descend."""
    html = (
        "<html><body><table><tr><td><table><tr><td>"
        "<p>The Federal Reserve declined to move rates this month, which "
        "surprised almost nobody.</p>"
        "<p>Unsubscribe</p>"
        "</td></tr></table></td></tr></table></body></html>"
    )
    cleaned = clean_document(document(html))
    assert "Federal Reserve" in cleaned.html
    assert "Unsubscribe" not in cleaned.html


def test_an_all_chrome_document_comes_back_empty() -> None:
    html = "<html><body><div><p>Unsubscribe</p></div></body></html>"
    assert clean_document(document(html)).html.strip() == ""


def test_other_document_fields_are_preserved() -> None:
    cleaned = clean_document(document(NEWSLETTER))
    assert cleaned.title == "An Issue"
    assert cleaned.publication == "Test Weekly"
    assert cleaned.origin.identifier == "<x@example.com>"


@pytest.mark.skipif(not FIXTURES.exists(), reason="run `make fixtures` first")
def test_cleaning_every_fixture_never_raises() -> None:
    from shabbat_print.extract import extract

    paths = sorted(FIXTURES.glob("*.eml"))
    assert paths, "no fixtures; run `make fixtures`"
    for path in paths:
        clean_document(extract(path.read_bytes()))
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `uv run pytest tests/test_clean.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shabbat_print.clean'`

- [ ] **Step 8: Implement the cleaner**

Create `src/shabbat_print/clean.py`:

```python
"""Reduce a newsletter to its article content.

This is an allow-list, not a deny-list: the output contains what the cleaner
judged to be content, so chrome is never rendered rather than rendered and
then removed. That is what stops a filler page from existing in the first
place.

Phase 1 drops every image. Keeping content figures is spec phase 7 and is
deliberately not implemented here.
"""

from dataclasses import replace

from bs4 import BeautifulSoup, Tag

from .boilerplate import content_ratio
from .models import Document, DroppedImage

# Removed outright, wherever they appear.
_NEVER_CONTENT = ("script", "style", "noscript", "iframe", "form", "button", "head")

# Wrappers an email layout hides its content inside.
_CONTAINERS = frozenset(
    {"div", "table", "tbody", "tr", "td", "center", "article", "section", "main"}
)

# A block whose text scores below this is chrome, not content.
CHROME_RATIO = 0.15

_IMAGES_DEFERRED = "images deferred to phase 7"


def _content_root(soup: BeautifulSoup) -> Tag:
    """Descend through single-child layout wrappers to the real content."""
    node: Tag = soup.body if soup.body is not None else soup
    while True:
        children = [
            child
            for child in node.children
            if isinstance(child, Tag) and child.get_text(strip=True)
        ]
        if len(children) == 1 and children[0].name in _CONTAINERS:
            node = children[0]
            continue
        return node


def _strip_images(root: Tag) -> tuple[int, tuple[DroppedImage, ...]]:
    dropped = []
    for image in root.find_all("img"):
        dropped.append(
            DroppedImage(src=image.get("src", ""), reason=_IMAGES_DEFERRED)
        )
        image.decompose()
    return 0, tuple(dropped)


def _strip_chrome_blocks(root: Tag) -> None:
    """Remove any top-level block whose text is overwhelmingly chrome.

    Applied to every block, not only trailing ones: "view in browser" bars sit
    at the top, and unsubscribe blocks at the bottom.
    """
    for block in list(root.children):
        if not isinstance(block, Tag):
            continue
        text = block.get_text("\n", strip=True)
        if not text or content_ratio(text) < CHROME_RATIO:
            block.decompose()


def clean_document(document: Document) -> Document:
    soup = BeautifulSoup(document.html, "lxml")
    for tag_name in _NEVER_CONTENT:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    root = _content_root(soup)
    kept, dropped = _strip_images(root)
    _strip_chrome_blocks(root)

    return replace(
        document,
        html=root.decode_contents().strip(),
        images_kept=kept,
        images_dropped=dropped,
    )
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_clean.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 10: Format, lint, commit, and push**

```bash
cd ~/Consulting/shabbat-print
make lint
git add -A
git commit -m "Classify boilerplate and reduce newsletters to article content"
git push origin main
```

---

### Task 5: Render a Document onto cell-sized pages

**Files:**
- Create: `src/shabbat_print/pdfutil.py`
- Create: `src/shabbat_print/render.py`
- Create: `tests/test_render.py`

**Interfaces:**
- Consumes: `Document` (Task 1), `Config` (Task 1).
- Produces: `render(document, config, compression=1.0, out_dir=None) -> Path`; `page_count(path) -> int`; `page_text(path, index) -> str`; `text_extent_mm(path, index) -> float` (distance from the top of the page to the bottom of its lowest text block).

- [ ] **Step 1: Add the PDF dependencies**

```bash
cd ~/Consulting/shabbat-print
uv add weasyprint pymupdf
```

- [ ] **Step 2: Write the failing pdfutil and render tests**

Create `tests/test_render.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

import pytest

from shabbat_print.config import load_config
from shabbat_print.geometry import A4
from shabbat_print.models import Document, Origin
from shabbat_print.pdfutil import page_count, page_text, text_extent_mm
from shabbat_print.render import render

PROSE = (
    "<p>The Federal Reserve declined to move rates this month, which surprised "
    "almost nobody who had been paying attention to the minutes.</p>"
)


@pytest.fixture
def config(tmp_path: Path):
    return load_config(tmp_path / "absent.toml")


def document(html: str) -> Document:
    return Document(
        origin=Origin(kind="email", identifier="<x@example.com>"),
        publication="Money Stuff",
        title="Private Credit Gets Complicated",
        date=datetime(2026, 9, 5, tzinfo=timezone.utc),
        html=html,
    )


def test_page_is_exactly_one_cell(config, tmp_path: Path) -> None:
    import pymupdf

    pdf = render(document(PROSE), config, out_dir=tmp_path)
    with pymupdf.open(pdf) as opened:
        rect = opened[0].rect
    expected_width, expected_height = A4.cell.as_points()
    assert rect.width == pytest.approx(expected_width, abs=1.0)
    assert rect.height == pytest.approx(expected_height, abs=1.0)


def test_title_and_publication_appear(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE), config, out_dir=tmp_path)
    text = page_text(pdf, 0)
    assert "Private Credit Gets Complicated" in text
    assert "Money Stuff" in text.replace("\n", " ")


def test_content_appears(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE), config, out_dir=tmp_path)
    assert "Federal Reserve" in page_text(pdf, 0)


def test_long_document_spans_several_cells(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE * 40), config, out_dir=tmp_path)
    assert page_count(pdf) > 1


def test_compression_never_increases_the_page_count(config, tmp_path: Path) -> None:
    long_document = document(PROSE * 40)
    loose = render(long_document, config, out_dir=tmp_path)
    tight = render(long_document, config, compression=0.98, out_dir=tmp_path)
    assert page_count(tight) <= page_count(loose)


def test_compression_writes_a_distinct_file(config, tmp_path: Path) -> None:
    doc = document(PROSE)
    assert render(doc, config, out_dir=tmp_path) != render(
        doc, config, compression=0.98, out_dir=tmp_path
    )


def test_letter_paper_gives_a_letter_cell(tmp_path: Path) -> None:
    import pymupdf

    from shabbat_print.geometry import LETTER

    config = load_config(tmp_path / "absent.toml", paper_override="letter")
    pdf = render(document(PROSE), config, out_dir=tmp_path)
    with pymupdf.open(pdf) as opened:
        rect = opened[0].rect
    expected_width, _ = LETTER.cell.as_points()
    assert rect.width == pytest.approx(expected_width, abs=1.0)


def test_text_extent_of_a_short_page_is_small(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE), config, out_dir=tmp_path)
    assert text_extent_mm(pdf, 0) < A4.cell.height_mm / 2


def test_text_extent_of_a_full_page_is_large(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE * 40), config, out_dir=tmp_path)
    assert text_extent_mm(pdf, 0) > A4.cell.height_mm / 2


def test_dollar_signs_in_content_survive(config, tmp_path: Path) -> None:
    """The template substitutes with string.Template; $ in the content must
    not be treated as a placeholder."""
    pdf = render(document("<p>It cost $500 and $unexpected trouble.</p>"), config, out_dir=tmp_path)
    assert "$500" in page_text(pdf, 0)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shabbat_print.pdfutil'`

- [ ] **Step 4: Implement the PDF inspection helpers**

Create `src/shabbat_print/pdfutil.py`:

```python
"""Reading facts back out of a rendered PDF."""

from pathlib import Path

import pymupdf

from .geometry import MM_PER_INCH, POINTS_PER_INCH


def page_count(path: Path) -> int:
    with pymupdf.open(path) as document:
        return document.page_count


def page_text(path: Path, index: int) -> str:
    with pymupdf.open(path) as document:
        return document[index].get_text()


def text_extent_mm(path: Path, index: int) -> float:
    """Millimetres from the top of the page to the bottom of its lowest text.

    PyMuPDF reports block coordinates with the origin at the top left, so the
    largest y1 is the bottom of the text.
    """
    with pymupdf.open(path) as document:
        blocks = [
            block for block in document[index].get_text("blocks") if block[4].strip()
        ]
    if not blocks:
        return 0.0
    bottom_pt = max(block[3] for block in blocks)
    return bottom_pt / POINTS_PER_INCH * MM_PER_INCH
```

- [ ] **Step 5: Implement the renderer**

Create `src/shabbat_print/render.py`:

```python
"""Typeset a Document onto cell-sized pages.

The page box is exactly one cell, so `impose.py` can tile four of them at 100%
scale. A font specified at 9pt therefore measures 9pt on the paper.
"""

import hashlib
import tempfile
from html import escape
from pathlib import Path
from string import Template

from weasyprint import HTML

from .config import Config
from .models import Document

# string.Template, not str.format: the CSS is full of braces, and the
# substituted content is not rescanned, so a "$" in a newsletter is harmless.
_TEMPLATE = Template("""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>$title</title>
<style>
@page {
  size: ${width_mm}mm ${height_mm}mm;
  margin: ${margin_mm}mm;
  @bottom-center { content: counter(page); font-size: 6pt; color: #555; }
}
html { font-size: ${font_size_pt}pt; }
body { font-family: Charter, Georgia, "Times New Roman", serif;
       line-height: $line_height; margin: 0; hyphens: auto; text-align: justify; }
.masthead { font-family: -apple-system, "Helvetica Neue", Helvetica, sans-serif;
            font-size: 0.70rem; text-transform: uppercase; letter-spacing: 0.06em;
            border-bottom: 0.5pt solid #000; padding-bottom: 2pt; margin-bottom: 6pt; }
h1 { font-size: 1.15rem; line-height: 1.2; margin: 0 0 6pt; }
h2, h3, h4 { font-size: 1rem; margin: 8pt 0 3pt; }
p { margin: 0 0 5pt; orphans: 2; widows: 2; }
ul, ol { margin: 0 0 5pt; padding-left: 12pt; }
blockquote { margin: 0 0 5pt 8pt; font-style: italic; }
a { color: inherit; text-decoration: none; }
img { max-width: 100%; filter: grayscale(100%); }
</style></head>
<body>
<div class="masthead">$publication &middot; $date</div>
<h1>$title</h1>
$content
</body></html>""")


def _stem(document: Document, compression: float) -> str:
    digest = hashlib.sha256(document.origin.identifier.encode()).hexdigest()[:12]
    return f"{digest}-{compression:.2f}"


def render(
    document: Document,
    config: Config,
    compression: float = 1.0,
    out_dir: Path | None = None,
) -> Path:
    """Render to a cell-sized PDF. `compression` scales the line height."""
    cell = config.printing.paper.cell
    html = _TEMPLATE.substitute(
        width_mm=f"{cell.width_mm:g}",
        height_mm=f"{cell.height_mm:g}",
        margin_mm=f"{config.layout.margin_mm:g}",
        font_size_pt=f"{config.layout.font_size_pt:g}",
        line_height=f"{config.layout.line_height * compression:.4f}",
        publication=escape(document.publication),
        date=escape(document.date.strftime("%-d %B %Y")),
        title=escape(document.title),
        content=document.html,
    )
    directory = out_dir if out_dir is not None else Path(tempfile.mkdtemp())
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{_stem(document, compression)}.pdf"
    HTML(string=html).write_pdf(output)
    return output
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_render.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 7: Format, lint, commit, and push**

```bash
cd ~/Consulting/shabbat-print
make lint
git add -A
git commit -m "Render documents onto cell-sized pages"
git push origin main
```

---

### Task 6: Fit the final cell — drop filler, squeeze widows

`trim.py` is the one stage that reaches backwards. It cannot know whether a 1%
tighter render would save a cell without trying, so `render` hands it a
callback rather than being imported.

**Files:**
- Create: `src/shabbat_print/trim.py`
- Create: `tests/test_trim.py`

**Interfaces:**
- Consumes: `Verdict` (Task 1), `content_ratio` (Task 4), `page_count`/`page_text`/`text_extent_mm` (Task 5).
- Produces: `classify(pdf, paper, layout, *, filler_ratio=0.15, widow_fill=0.20) -> Verdict`; `fit(pdf, paper, layout, rerender) -> tuple[Path, Verdict]` where `rerender: Callable[[float], Path]`. The returned `Verdict` reports what was **done**: `FILLER` if a page was dropped, `WIDOW` if a squeeze succeeded, `FULL` otherwise.

- [ ] **Step 1: Add the imposition dependency**

```bash
cd ~/Consulting/shabbat-print
uv add pypdf
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_trim.py`:

```python
from pathlib import Path

import pymupdf
import pytest

from shabbat_print.config import LayoutConfig
from shabbat_print.geometry import A4, MM_PER_INCH, POINTS_PER_INCH, Paper
from shabbat_print.models import Verdict
from shabbat_print.pdfutil import page_count
from shabbat_print.trim import classify, fit

LAYOUT = LayoutConfig(margin_mm=9.0, font_size_pt=9.0, line_height=1.35)

FOOTER = "Unsubscribe\nManage your preferences\n© 2026 Test Weekly\nRead more"
PROSE = (
    "The Federal Reserve declined to move rates this month, which surprised "
    "almost nobody who had been paying attention to the minutes."
)


def build(path: Path, pages: list[str], paper: Paper = A4, fill: bool = False) -> Path:
    """Write a PDF of cell-sized pages, each carrying the given text."""
    width, height = paper.cell.as_points()
    margin = LAYOUT.margin_mm / MM_PER_INCH * POINTS_PER_INCH
    with pymupdf.open() as document:
        for text in pages:
            page = document.new_page(width=width, height=height)
            body = (text + "\n") * (40 if fill else 1)
            page.insert_textbox(
                pymupdf.Rect(margin, margin, width - margin, height - margin),
                body,
                fontsize=9,
            )
        document.save(path)
    return path


def test_a_pure_footer_page_is_filler(tmp_path: Path) -> None:
    pdf = build(tmp_path / "a.pdf", [PROSE, FOOTER])
    assert classify(pdf, A4, LAYOUT) is Verdict.FILLER


def test_a_long_advertising_page_is_still_filler(tmp_path: Path) -> None:
    """Length must not rescue a page that is entirely chrome."""
    pdf = build(tmp_path / "b.pdf", [PROSE, "Sponsored by Acme\n" * 30])
    assert classify(pdf, A4, LAYOUT) is Verdict.FILLER


def test_a_blank_final_page_is_filler(tmp_path: Path) -> None:
    pdf = build(tmp_path / "c.pdf", [PROSE, "   "])
    assert classify(pdf, A4, LAYOUT) is Verdict.FILLER


def test_a_short_prose_page_is_a_widow(tmp_path: Path) -> None:
    pdf = build(tmp_path / "d.pdf", [PROSE, PROSE])
    assert classify(pdf, A4, LAYOUT) is Verdict.WIDOW


def test_a_full_prose_page_is_full(tmp_path: Path) -> None:
    pdf = build(tmp_path / "e.pdf", [PROSE, PROSE], fill=True)
    assert classify(pdf, A4, LAYOUT) is Verdict.FULL


def test_fit_drops_a_filler_page(tmp_path: Path) -> None:
    pdf = build(tmp_path / "f.pdf", [PROSE, FOOTER])
    result, verdict = fit(pdf, A4, LAYOUT, rerender=lambda _: pdf)
    assert verdict is Verdict.FILLER
    assert page_count(result) == 1


def test_fit_never_drops_the_only_page(tmp_path: Path) -> None:
    """A one-page document that is all chrome must not become zero pages."""
    pdf = build(tmp_path / "g.pdf", [FOOTER])
    result, verdict = fit(pdf, A4, LAYOUT, rerender=lambda _: pdf)
    assert verdict is Verdict.FULL
    assert page_count(result) == 1


def test_fit_squeezes_a_widow_away(tmp_path: Path) -> None:
    wide = build(tmp_path / "h.pdf", [PROSE, PROSE])
    narrow = build(tmp_path / "h-tight.pdf", [PROSE])
    calls: list[float] = []

    def rerender(compression: float) -> Path:
        calls.append(compression)
        return narrow

    result, verdict = fit(wide, A4, LAYOUT, rerender=rerender)
    assert verdict is Verdict.WIDOW
    assert result == narrow
    assert calls == [0.99]


def test_fit_reverts_when_squeezing_does_not_help(tmp_path: Path) -> None:
    wide = build(tmp_path / "i.pdf", [PROSE, PROSE])
    calls: list[float] = []

    def rerender(compression: float) -> Path:
        calls.append(compression)
        return wide

    result, verdict = fit(wide, A4, LAYOUT, rerender=rerender)
    assert verdict is Verdict.FULL
    assert result == wide
    assert calls == [0.99, 0.98]


def test_fit_leaves_a_full_document_alone(tmp_path: Path) -> None:
    pdf = build(tmp_path / "j.pdf", [PROSE, PROSE], fill=True)
    result, verdict = fit(pdf, A4, LAYOUT, rerender=lambda _: pdf)
    assert verdict is Verdict.FULL
    assert result == pdf
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_trim.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shabbat_print.trim'`

- [ ] **Step 4: Implement the fitter**

Create `src/shabbat_print/trim.py`:

```python
"""Decide what to do about a document's final cell.

The judgment is about what the text *is*, never how much of it there is, and
the error is deliberately asymmetric: keeping a junk cell wastes a quarter of
one side of one sheet, while dropping a content cell destroys something the
reader wanted. So the test is "is there any real content here?".
"""

from collections.abc import Callable
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from .boilerplate import content_ratio
from .config import LayoutConfig
from .geometry import Paper
from .models import Verdict
from .pdfutil import page_count, page_text, text_extent_mm

FILLER_RATIO = 0.15
WIDOW_FILL = 0.20
COMPRESSIONS = (0.99, 0.98)


def classify(
    pdf: Path,
    paper: Paper,
    layout: LayoutConfig,
    *,
    filler_ratio: float = FILLER_RATIO,
    widow_fill: float = WIDOW_FILL,
) -> Verdict:
    last = page_count(pdf) - 1
    text = page_text(pdf, last)
    if not text.strip():
        return Verdict.FILLER
    if content_ratio(text) < filler_ratio:
        return Verdict.FILLER

    usable_mm = paper.cell.height_mm - 2 * layout.margin_mm
    used_mm = max(0.0, text_extent_mm(pdf, last) - layout.margin_mm)
    if used_mm / usable_mm < widow_fill:
        return Verdict.WIDOW
    return Verdict.FULL


def _without_last_page(pdf: Path) -> Path:
    reader = PdfReader(str(pdf))
    writer = PdfWriter()
    for page in reader.pages[:-1]:
        writer.add_page(page)
    output = pdf.with_name(f"{pdf.stem}-trimmed.pdf")
    with output.open("wb") as handle:
        writer.write(handle)
    return output


def fit(
    pdf: Path,
    paper: Paper,
    layout: LayoutConfig,
    rerender: Callable[[float], Path],
) -> tuple[Path, Verdict]:
    """Return the PDF to use, and what was actually done to get it."""
    original_pages = page_count(pdf)
    if original_pages <= 1:
        # Never leave a document with no pages at all.
        return pdf, Verdict.FULL

    verdict = classify(pdf, paper, layout)
    if verdict is Verdict.FILLER:
        return _without_last_page(pdf), Verdict.FILLER

    if verdict is Verdict.WIDOW:
        for compression in COMPRESSIONS:
            candidate = rerender(compression)
            if page_count(candidate) < original_pages:
                return candidate, Verdict.WIDOW

    return pdf, Verdict.FULL
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_trim.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 6: Format, lint, commit, and push**

```bash
cd ~/Consulting/shabbat-print
make lint
git add -A
git commit -m "Drop filler cells and squeeze widows away"
git push origin main
```

---

### Task 7: Impose four cells to a sheet

**Files:**
- Create: `src/shabbat_print/impose.py`
- Create: `tests/test_impose.py`

**Interfaces:**
- Consumes: `Paper` (Task 1).
- Produces: `impose(cell_pdfs: Sequence[Path], paper: Paper, out: Path) -> int`, returning the sheet-side count. Cell order within a sheet is reading order: top-left, top-right, bottom-left, bottom-right.

Note: no padding between documents is needed. Each document's PDF already
consists of whole cells, so concatenating them starts every document at a cell
boundary automatically.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_impose.py`:

```python
from pathlib import Path

import pymupdf
import pytest

from shabbat_print.geometry import A4, LETTER, Paper
from shabbat_print.impose import impose
from shabbat_print.pdfutil import page_count


def numbered_cells(path: Path, count: int, paper: Paper = A4) -> Path:
    """A PDF of cell-sized pages, each stamped with its own number."""
    width, height = paper.cell.as_points()
    with pymupdf.open() as document:
        for number in range(1, count + 1):
            page = document.new_page(width=width, height=height)
            page.insert_text((20, 40), f"PAGE {number}", fontsize=14)
        document.save(path)
    return path


def quadrant_of(sheet: pymupdf.Page, label: str) -> tuple[str, str]:
    """Which quadrant of the sheet a label's centre falls in."""
    rects = sheet.search_for(label)
    assert rects, f"{label} not found on this sheet"
    centre = rects[0].tl
    vertical = "top" if centre.y < sheet.rect.height / 2 else "bottom"
    horizontal = "left" if centre.x < sheet.rect.width / 2 else "right"
    return vertical, horizontal


def test_eight_cells_make_two_sheet_sides(tmp_path: Path) -> None:
    cells = numbered_cells(tmp_path / "cells.pdf", 8)
    out = tmp_path / "sheets.pdf"
    assert impose([cells], A4, out) == 2
    assert page_count(out) == 2


def test_sheet_is_full_paper_size(tmp_path: Path) -> None:
    cells = numbered_cells(tmp_path / "cells.pdf", 4)
    out = tmp_path / "sheets.pdf"
    impose([cells], A4, out)
    with pymupdf.open(out) as document:
        rect = document[0].rect
    expected_width, expected_height = A4.sheet.as_points()
    assert rect.width == pytest.approx(expected_width, abs=1.0)
    assert rect.height == pytest.approx(expected_height, abs=1.0)


def test_cells_land_in_reading_order(tmp_path: Path) -> None:
    cells = numbered_cells(tmp_path / "cells.pdf", 8)
    out = tmp_path / "sheets.pdf"
    impose([cells], A4, out)
    with pymupdf.open(out) as document:
        first, second = document[0], document[1]
        assert quadrant_of(first, "PAGE 1") == ("top", "left")
        assert quadrant_of(first, "PAGE 2") == ("top", "right")
        assert quadrant_of(first, "PAGE 3") == ("bottom", "left")
        assert quadrant_of(first, "PAGE 4") == ("bottom", "right")
        assert quadrant_of(second, "PAGE 5") == ("top", "left")
        assert quadrant_of(second, "PAGE 6") == ("top", "right")


def test_several_documents_are_concatenated(tmp_path: Path) -> None:
    first = numbered_cells(tmp_path / "one.pdf", 2)
    second = numbered_cells(tmp_path / "two.pdf", 2)
    out = tmp_path / "sheets.pdf"
    assert impose([first, second], A4, out) == 1


def test_a_partial_sheet_leaves_blank_cells(tmp_path: Path) -> None:
    cells = numbered_cells(tmp_path / "cells.pdf", 5)
    out = tmp_path / "sheets.pdf"
    assert impose([cells], A4, out) == 2


def test_letter_paper_gives_letter_sheets(tmp_path: Path) -> None:
    cells = numbered_cells(tmp_path / "cells.pdf", 4, paper=LETTER)
    out = tmp_path / "sheets.pdf"
    impose([cells], LETTER, out)
    with pymupdf.open(out) as document:
        rect = document[0].rect
    expected_width, _ = LETTER.sheet.as_points()
    assert rect.width == pytest.approx(expected_width, abs=1.0)


def test_imposing_nothing_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nothing to impose"):
        impose([], A4, tmp_path / "sheets.pdf")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_impose.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shabbat_print.impose'`

- [ ] **Step 3: Implement the imposer**

Create `src/shabbat_print/impose.py`:

```python
"""Tile cell-sized pages four to a sheet side.

Because a cell is exactly a quarter of a sheet, the cells are placed at 100%
scale: nothing is resampled and no margin is lost to an aspect mismatch.

Duplex needs no special ordering here. Output page 1 is the front of sheet 1
and carries cells 1-4; output page 2 is its back and carries cells 5-8. The
printer's long-edge duplex does the rest.
"""

from collections.abc import Sequence
from pathlib import Path

from pypdf import PageObject, PdfReader, PdfWriter, Transformation

from .geometry import Paper

CELLS_PER_SIDE = 4


def impose(cell_pdfs: Sequence[Path], paper: Paper, out: Path) -> int:
    """Write the imposed document and return how many sheet sides it has."""
    readers = [PdfReader(str(path)) for path in cell_pdfs]
    cells = [page for reader in readers for page in reader.pages]
    if not cells:
        raise ValueError("nothing to impose")

    cell_width, cell_height = paper.cell.as_points()
    sheet_width, sheet_height = paper.sheet.as_points()

    # The PDF origin is bottom-left, so the top row sits one cell height up.
    # Reading order: top-left, top-right, bottom-left, bottom-right.
    offsets = (
        (0.0, cell_height),
        (cell_width, cell_height),
        (0.0, 0.0),
        (cell_width, 0.0),
    )

    writer = PdfWriter()
    for start in range(0, len(cells), CELLS_PER_SIDE):
        side = PageObject.create_blank_page(width=sheet_width, height=sheet_height)
        for cell, (dx, dy) in zip(cells[start : start + CELLS_PER_SIDE], offsets):
            side.merge_transformed_page(cell, Transformation().translate(dx, dy))
        writer.add_page(side)

    with out.open("wb") as handle:
        writer.write(handle)
    return len(writer.pages)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_impose.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Format, lint, commit, and push**

```bash
cd ~/Consulting/shabbat-print
make lint
git add -A
git commit -m "Impose four cells to a sheet side"
git push origin main
```

---

### Task 8: Spool to the printer

**Files:**
- Create: `src/shabbat_print/printer.py`
- Create: `tests/test_printer.py`

**Interfaces:**
- Consumes: `Config` (Task 1).
- Produces: `build_command(pdf, config) -> list[str]`; `spool(pdf, config, runner=subprocess.run) -> str` returning the CUPS job id; `PrintError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_printer.py`:

```python
import subprocess
from pathlib import Path

import pytest

from shabbat_print.config import load_config
from shabbat_print.printer import PrintError, build_command, spool


@pytest.fixture
def config(tmp_path: Path):
    return load_config(tmp_path / "absent.toml")


CONFIGURED = '[print]\nprinter = "Some_Printer"\n'


def test_command_carries_paper_and_duplex(config, tmp_path: Path) -> None:
    command = build_command(tmp_path / "sheets.pdf", config)
    assert command[0] == "lp"
    assert "media=A4" in command
    assert "sides=two-sided-long-edge" in command
    assert "print-scaling=none" in command
    assert command[-1] == str(tmp_path / "sheets.pdf")


def test_an_unset_printer_uses_the_system_default(config, tmp_path: Path) -> None:
    """No -d at all, so CUPS picks its own default destination."""
    assert "-d" not in build_command(tmp_path / "sheets.pdf", config)


def test_a_configured_printer_is_named(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(CONFIGURED)
    command = build_command(tmp_path / "sheets.pdf", load_config(path))
    assert command[:3] == ["lp", "-d", "Some_Printer"]


def test_letter_override_reaches_the_command(tmp_path: Path) -> None:
    config = load_config(tmp_path / "absent.toml", paper_override="letter")
    assert "media=Letter" in build_command(tmp_path / "sheets.pdf", config)


def test_spool_returns_the_job_id(config, tmp_path: Path) -> None:
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="request id is Some_Printer-137 (1 file(s))\n",
            stderr="",
        )

    assert spool(tmp_path / "sheets.pdf", config, runner=runner) == "Some_Printer-137"


def test_spool_raises_on_failure(config, tmp_path: Path) -> None:
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="lp: no such printer")

    with pytest.raises(PrintError, match="no such printer"):
        spool(tmp_path / "sheets.pdf", config, runner=runner)


def test_spool_raises_when_the_job_id_is_missing(config, tmp_path: Path) -> None:
    """A zero exit with unparseable output must not be reported as success."""
    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="something else\n", stderr="")

    with pytest.raises(PrintError, match="job id"):
        spool(tmp_path / "sheets.pdf", config, runner=runner)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_printer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shabbat_print.printer'`

- [ ] **Step 3: Implement the printer**

Create `src/shabbat_print/printer.py`:

```python
"""Hand a finished document to CUPS.

The PDF arrives already imposed, so `lp` is asked only to select the tray and
the duplex mode. `print-scaling=none` matters: the document is laid out at
exact size and any driver-side "fit to page" would undo that.
"""

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from .config import Config

_JOB_ID = re.compile(r"request id is (\S+)")

Runner = Callable[..., subprocess.CompletedProcess]


class PrintError(Exception):
    """The job did not reach the print queue."""


def build_command(pdf: Path, config: Config) -> list[str]:
    command = ["lp"]
    if config.printing.printer:
        command += ["-d", config.printing.printer]
    # With no -d, CUPS sends the job to its own default destination, which is
    # the right behaviour for anyone who has not named a printer.
    command += [
        "-o",
        f"media={config.printing.paper.name}",
        "-o",
        f"sides={config.printing.duplex}",
        "-o",
        "print-scaling=none",
        str(pdf),
    ]
    return command


def spool(pdf: Path, config: Config, runner: Runner = subprocess.run) -> str:
    command = build_command(pdf, config)
    result = runner(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise PrintError(result.stderr.strip() or f"lp exited {result.returncode}")
    match = _JOB_ID.search(result.stdout)
    if match is None:
        raise PrintError(f"lp reported no job id; output was: {result.stdout.strip()!r}")
    return match.group(1)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_printer.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Format, lint, commit, and push**

```bash
cd ~/Consulting/shabbat-print
make lint
git add -A
git commit -m "Spool an imposed document to CUPS"
git push origin main
```

---

### Task 9: Read from IMAP

**Files:**
- Create: `src/shabbat_print/mail.py`
- Create: `tests/test_mail.py`

**Interfaces:**
- Consumes: `MailConfig` (Task 1).
- Produces: `password_for(host, user) -> str`; `MailError`; `Mailbox(host, user, password, folder, imap_factory=imaplib.IMAP4_SSL)` as a context manager with `.search_flagged() -> list[int]`, `.search_unflagged_since(since: date) -> list[int]`, `.fetch(uid: int) -> bytes`, `.trash_folder() -> str`.

- [ ] **Step 1: Add the keyring dependency**

```bash
cd ~/Consulting/shabbat-print
uv add keyring
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_mail.py`:

```python
from datetime import date

import pytest

from shabbat_print.mail import Mailbox, MailError, password_for

RAW = b"From: someone@example.com\r\nSubject: Hello\r\n\r\nBody.\r\n"


class FakeIMAP:
    """Records every command, so tests can assert on the exact conversation."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.calls: list[tuple] = []
        self.selected: tuple[str, bool] | None = None
        self.search_results: dict[str, bytes] = {}
        self.list_response = [
            b'(\\HasNoChildren) "/" "INBOX/toprint"',
            b'(\\HasNoChildren \\Trash) "/" "INBOX/Trash"',
        ]

    def login(self, user: str, password: str):
        self.calls.append(("login", user, password))
        return ("OK", [b"logged in"])

    def select(self, folder: str, readonly: bool = False):
        self.calls.append(("select", folder, readonly))
        self.selected = (folder, readonly)
        return ("OK", [b"2226"])

    def uid(self, command: str, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            criteria = " ".join(str(a) for a in args if a is not None)
            return ("OK", [self.search_results.get(criteria, b"")])
        if command == "FETCH":
            return ("OK", [(b"1 (RFC822 {58}", RAW), b")"])
        return ("OK", [b""])

    def list(self):
        self.calls.append(("list",))
        return ("OK", self.list_response)

    def logout(self):
        self.calls.append(("logout",))
        return ("BYE", [b"bye"])


def mailbox(fake: FakeIMAP) -> Mailbox:
    return Mailbox(
        host="imap.example.com",
        user="someone@example.com",
        password="secret",
        folder="INBOX/toprint",
        imap_factory=lambda host: fake,
    )


def test_opens_the_folder_read_only() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake):
        pass
    assert fake.selected == ("INBOX/toprint", True)
    assert ("login", "someone@example.com", "secret") in fake.calls
    assert ("logout",) in fake.calls


def test_search_flagged_returns_uids() -> None:
    fake = FakeIMAP("imap.example.com")
    fake.search_results["FLAGGED"] = b"3 17 204"
    with mailbox(fake) as box:
        assert box.search_flagged() == [3, 17, 204]


def test_search_flagged_handles_an_empty_folder() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        assert box.search_flagged() == []


def test_unflagged_since_formats_the_date_for_imap() -> None:
    fake = FakeIMAP("imap.example.com")
    fake.search_results["UNFLAGGED SINCE 01-Sep-2026"] = b"9 11"
    with mailbox(fake) as box:
        assert box.search_unflagged_since(date(2026, 9, 1)) == [9, 11]


def test_fetch_returns_the_raw_message() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        assert box.fetch(3) == RAW


def test_trash_folder_is_discovered_from_special_use() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        assert box.trash_folder() == "INBOX/Trash"


def test_trash_folder_raises_when_absent() -> None:
    fake = FakeIMAP("imap.example.com")
    fake.list_response = [b'(\\HasNoChildren) "/" "INBOX/toprint"']
    with mailbox(fake) as box:
        with pytest.raises(MailError, match="Trash"):
            box.trash_folder()


def test_password_for_reads_the_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shabbat_print.mail.keyring.get_password", lambda host, user: "from-keychain"
    )
    assert password_for("imap.example.com", "someone@example.com") == "from-keychain"


def test_password_for_explains_how_to_store_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shabbat_print.mail.keyring.get_password", lambda host, user: None)
    with pytest.raises(MailError, match="keyring set"):
        password_for("imap.example.com", "someone@example.com")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_mail.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shabbat_print.mail'`

- [ ] **Step 4: Implement the read side**

Create `src/shabbat_print/mail.py`:

```python
"""IMAP access to the print queue.

The folder is opened read-only. Everything that can crash - HTML parsing,
rendering, imposition - runs while this connection has no power to modify
mail; the read-write connection is opened later, by retire(), and only after a
job has reached the print queue.
"""

import imaplib
import re
from collections.abc import Callable
from datetime import date
from types import TracebackType

import keyring

IMAPFactory = Callable[[str], imaplib.IMAP4]

_TRASH_LINE = re.compile(rb'\\Trash\b[^"]*"[^"]*"\s+"?([^"]+?)"?\s*$')


class MailError(Exception):
    """Something went wrong talking to the mail server."""


def password_for(host: str, user: str) -> str:
    secret = keyring.get_password(host, user)
    if not secret:
        raise MailError(
            f"no password in the keychain for {user} at {host}. "
            f"Store it once with:  keyring set {host} {user}"
        )
    return secret


class Mailbox:
    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        folder: str,
        imap_factory: IMAPFactory = imaplib.IMAP4_SSL,
    ) -> None:
        self._host = host
        self._user = user
        self._password = password
        self._folder = folder
        self._factory = imap_factory
        self._imap: imaplib.IMAP4 | None = None

    def __enter__(self) -> "Mailbox":
        self._imap = self._factory(self._host)
        self._imap.login(self._user, self._password)
        self._imap.select(self._folder, readonly=True)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            finally:
                self._imap = None

    @property
    def _connection(self) -> imaplib.IMAP4:
        if self._imap is None:
            raise MailError("mailbox is not open; use it as a context manager")
        return self._imap

    def _search(self, criteria: str) -> list[int]:
        status, data = self._connection.uid("SEARCH", None, criteria)
        if status != "OK":
            raise MailError(f"search failed for {criteria!r}: {status}")
        payload = data[0] or b""
        return [int(uid) for uid in payload.split()]

    def search_flagged(self) -> list[int]:
        return self._search("FLAGGED")

    def search_unflagged_since(self, since: date) -> list[int]:
        # IMAP wants 01-Sep-2026, and the day is not zero-padded on every
        # server, but a padded day is always accepted.
        return self._search(f"UNFLAGGED SINCE {since.strftime('%d-%b-%Y')}")

    def fetch(self, uid: int) -> bytes:
        status, data = self._connection.uid("FETCH", str(uid), "(RFC822)")
        if status != "OK":
            raise MailError(f"fetch failed for uid {uid}: {status}")
        for item in data:
            if isinstance(item, tuple) and len(item) > 1:
                return item[1]
        raise MailError(f"fetch returned no message body for uid {uid}")

    def trash_folder(self) -> str:
        status, lines = self._connection.list()
        if status != "OK":
            raise MailError(f"LIST failed: {status}")
        for line in lines:
            match = _TRASH_LINE.search(line)
            if match:
                return match.group(1).decode()
        raise MailError("no folder advertises the \\Trash special-use attribute")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mail.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 6: Format, lint, commit, and push**

```bash
cd ~/Consulting/shabbat-print
make lint
git add -A
git commit -m "Read the print queue from IMAP"
git push origin main
```

---

### Task 10: Retirement and the run log

The only code in the project that modifies mail. It runs last, on a separate
read-write connection, and writes its log before the first mutation.

**Files:**
- Create: `src/shabbat_print/runlog.py`
- Modify: `src/shabbat_print/mail.py` (append `RetireResult` and `Mailbox.retire`)
- Create: `tests/test_runlog.py`
- Modify: `tests/test_mail.py` (append retirement tests)

**Interfaces:**
- Consumes: `Mailbox` (Task 9).
- Produces: `RetireResult(retired: tuple[int, ...], failed: tuple[int, ...])`; `Mailbox.retire(uids, trash_folder) -> RetireResult`; `runlog.record(entry: dict, state_dir=None) -> Path`; `runlog.last_successful_run(state_dir=None) -> datetime | None`.

- [ ] **Step 1: Write the failing run-log tests**

Create `tests/test_runlog.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

from shabbat_print import runlog


def test_record_writes_a_readable_file(tmp_path: Path) -> None:
    path = runlog.record({"outcome": "printed", "documents": []}, state_dir=tmp_path)
    assert path.exists()
    assert path.suffix == ".json"


def test_last_successful_run_is_none_when_empty(tmp_path: Path) -> None:
    assert runlog.last_successful_run(state_dir=tmp_path) is None


def test_last_successful_run_finds_the_newest_printed_run(tmp_path: Path) -> None:
    runlog.record(
        {"outcome": "printed", "at": "2026-08-28T15:00:00+00:00"}, state_dir=tmp_path
    )
    runlog.record(
        {"outcome": "printed", "at": "2026-09-04T15:00:00+00:00"}, state_dir=tmp_path
    )
    found = runlog.last_successful_run(state_dir=tmp_path)
    assert found == datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)


def test_unprinted_runs_are_ignored(tmp_path: Path) -> None:
    runlog.record(
        {"outcome": "cancelled", "at": "2026-09-04T15:00:00+00:00"}, state_dir=tmp_path
    )
    assert runlog.last_successful_run(state_dir=tmp_path) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_runlog.py -v`
Expected: FAIL — `ImportError: cannot import name 'runlog'`

- [ ] **Step 3: Implement the run log**

Create `src/shabbat_print/runlog.py`:

```python
"""A record of every run, written before any mail is modified.

The log is what makes retirement reversible: it names the Message-IDs that were
marked read, unstarred, and moved to Trash, so a bad run can be undone by hand.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "shabbat-print" / "runs"


def record(entry: dict[str, Any], state_dir: Path | None = None) -> Path:
    directory = state_dir if state_dir is not None else DEFAULT_STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    entry.setdefault("at", datetime.now(timezone.utc).isoformat())
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S-%f")
    path = directory / f"{stamp}.json"
    path.write_text(json.dumps(entry, indent=2, default=str))
    return path


def last_successful_run(state_dir: Path | None = None) -> datetime | None:
    directory = state_dir if state_dir is not None else DEFAULT_STATE_DIR
    if not directory.exists():
        return None
    stamps: list[datetime] = []
    for path in directory.glob("*.json"):
        try:
            entry = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if entry.get("outcome") != "printed":
            continue
        try:
            stamps.append(datetime.fromisoformat(entry["at"]))
        except (KeyError, ValueError):
            continue
    return max(stamps) if stamps else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_runlog.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Write the failing retirement tests**

Append to `tests/test_mail.py`:

```python
def test_retire_marks_read_unstars_then_moves() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        result = box.retire([7], "INBOX/Trash")
    assert result.retired == (7,)
    assert result.failed == ()

    uid_calls = [call for call in fake.calls if call[0] == "uid"]
    assert uid_calls == [
        ("uid", "STORE", "7", "+FLAGS", "(\\Seen)"),
        ("uid", "STORE", "7", "-FLAGS", "(\\Flagged)"),
        ("uid", "MOVE", "7", "INBOX/Trash"),
    ]


def test_retire_reopens_the_folder_writable() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        box.retire([7], "INBOX/Trash")
    assert fake.selected == ("INBOX/toprint", False)


def test_retire_reports_failures_without_stopping() -> None:
    fake = FakeIMAP("imap.example.com")

    original = fake.uid

    def failing(command, *args):
        if command == "MOVE" and args[0] == "8":
            fake.calls.append(("uid", command, *args))
            return ("NO", [b"mailbox full"])
        return original(command, *args)

    fake.uid = failing
    with mailbox(fake) as box:
        result = box.retire([7, 8, 9], "INBOX/Trash")
    assert result.retired == (7, 9)
    assert result.failed == (8,)


def test_retire_with_no_uids_touches_nothing() -> None:
    fake = FakeIMAP("imap.example.com")
    with mailbox(fake) as box:
        result = box.retire([], "INBOX/Trash")
    assert result.retired == ()
    assert result.failed == ()
    assert not [call for call in fake.calls if call[0] == "uid"]

    # The folder was never reopened writable, so nothing could have changed.
    assert fake.selected == ("INBOX/toprint", True)
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/test_mail.py -v`
Expected: FAIL — `AttributeError: 'Mailbox' object has no attribute 'retire'`

- [ ] **Step 7: Implement retirement**

Append to `src/shabbat_print/mail.py`:

```python
@dataclass(frozen=True, slots=True)
class RetireResult:
    retired: tuple[int, ...]
    failed: tuple[int, ...]
```

Add `from dataclasses import dataclass` to the imports, and add this method to
`Mailbox`:

```python
    def retire(self, uids: Sequence[int], trash_folder: str) -> RetireResult:
        """Mark read, unstar, and move to Trash.

        Called only after a job has reached the print queue, and only for the
        messages whose content actually reached it.
        """
        if not uids:
            return RetireResult(retired=(), failed=())

        connection = self._connection
        connection.select(self._folder, readonly=False)

        retired: list[int] = []
        failed: list[int] = []
        for uid in uids:
            identifier = str(uid)
            steps = (
                ("STORE", identifier, "+FLAGS", "(\\Seen)"),
                ("STORE", identifier, "-FLAGS", "(\\Flagged)"),
                ("MOVE", identifier, trash_folder),
            )
            for step in steps:
                status, _ = connection.uid(*step)
                if status != "OK":
                    failed.append(uid)
                    break
            else:
                retired.append(uid)
        return RetireResult(retired=tuple(retired), failed=tuple(failed))
```

Add `from collections.abc import Callable, Sequence` to the imports.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mail.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 9: Format, lint, commit, and push**

```bash
cd ~/Consulting/shabbat-print
make lint
git add -A
git commit -m "Retire printed mail, and record every run"
git push origin main
```

---

### Task 11: Pipeline orchestration and the command line

**Files:**
- Create: `src/shabbat_print/pipeline.py`
- Create: `src/shabbat_print/cli.py`
- Create: `tests/test_pipeline.py`
- Create: `tests/test_cli.py`
- Modify: `pyproject.toml` (entry point)

**Interfaces:**
- Consumes: everything above.
- Produces: `Built(document, pdf, cells, verdict)`; `Failure(document, error)`; `build(documents, config, out_dir) -> tuple[list[Built], list[Failure]]`; the `shabbat-print` command.

- [ ] **Step 1: Add click**

```bash
cd ~/Consulting/shabbat-print
uv add click
```

- [ ] **Step 2: Write the failing pipeline tests**

Create `tests/test_pipeline.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

import pytest

from shabbat_print.config import load_config
from shabbat_print.models import Document, Origin, Verdict
from shabbat_print.pipeline import build

PROSE = (
    "<p>The Federal Reserve declined to move rates this month, which surprised "
    "almost nobody who had been paying attention to the minutes.</p>"
)


@pytest.fixture
def config(tmp_path: Path):
    return load_config(tmp_path / "absent.toml")


def document(html: str, identifier: str = "<a@example.com>") -> Document:
    return Document(
        origin=Origin(kind="email", identifier=identifier, uid=1),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=timezone.utc),
        html=html,
    )


def test_builds_a_pdf_per_document(config, tmp_path: Path) -> None:
    built, failed = build(
        [document(PROSE, "<a@x>"), document(PROSE, "<b@x>")], config, tmp_path
    )
    assert len(built) == 2
    assert failed == []
    assert all(item.pdf.exists() for item in built)
    assert all(item.cells >= 1 for item in built)


def test_cells_matches_the_pdf_it_reports(config, tmp_path: Path) -> None:
    """Built.cells is what the sheet count is computed from, so it must equal
    the page count of the PDF actually handed on."""
    from shabbat_print.pdfutil import page_count

    built, _ = build([document(PROSE * 40)], config, tmp_path)
    assert built[0].cells == page_count(built[0].pdf)
    assert built[0].cells > 1
    assert isinstance(built[0].verdict, Verdict)


def test_a_failing_document_is_reported_not_raised(config, tmp_path: Path) -> None:
    broken = document(PROSE, "<c@x>")
    built, failed = build([broken], config, tmp_path, render_fn=_explode)
    assert built == []
    assert len(failed) == 1
    assert failed[0].document is broken
    assert "boom" in str(failed[0].error)


def _explode(*args, **kwargs):
    raise RuntimeError("boom")


def test_an_empty_document_is_a_failure_not_a_blank_page(config, tmp_path: Path) -> None:
    """A newsletter that cleans down to nothing must not print an empty cell."""
    built, failed = build([document("")], config, tmp_path)
    assert built == []
    assert len(failed) == 1
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shabbat_print.pipeline'`

- [ ] **Step 4: Implement the pipeline**

Create `src/shabbat_print/pipeline.py`:

```python
"""Turn cleaned documents into cell PDFs, one document at a time.

A document that fails is reported and skipped, never allowed to abort the run
and never silently dropped: it stays starred, so it comes back next week.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .clean import clean_document
from .config import Config
from .models import Document, Verdict
from .pdfutil import page_count
from .render import render
from .trim import fit


@dataclass(frozen=True, slots=True)
class Built:
    document: Document
    pdf: Path
    cells: int
    verdict: Verdict


@dataclass(frozen=True, slots=True)
class Failure:
    document: Document
    error: Exception


class EmptyDocumentError(Exception):
    """Cleaning left nothing to print."""


def build(
    documents: Sequence[Document],
    config: Config,
    out_dir: Path,
    render_fn: Callable[..., Path] = render,
) -> tuple[list[Built], list[Failure]]:
    built: list[Built] = []
    failed: list[Failure] = []

    for document in documents:
        try:
            cleaned = clean_document(document)
            if not cleaned.html.strip():
                raise EmptyDocumentError(
                    f"{document.publication}: nothing left after cleaning"
                )
            pdf = render_fn(cleaned, config, out_dir=out_dir)
            fitted, verdict = fit(
                pdf,
                config.printing.paper,
                config.layout,
                rerender=lambda compression: render_fn(
                    cleaned, config, compression=compression, out_dir=out_dir
                ),
            )
            built.append(
                Built(
                    document=cleaned,
                    pdf=fitted,
                    cells=page_count(fitted),
                    verdict=verdict,
                )
            )
        except Exception as error:  # noqa: BLE001 - one bad newsletter must not stop the run
            failed.append(Failure(document=document, error=error))

    return built, failed
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 6: Write the failing CLI test**

Create `tests/test_cli.py`:

```python
from pathlib import Path

from click.testing import CliRunner

from shabbat_print.cli import main


def test_help_names_the_paper_switch() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "--paper" in result.output


def test_an_empty_queue_says_so(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("shabbat_print.cli.fetch_queue", lambda config: ([], None))
    result = CliRunner().invoke(main, ["--config", str(tmp_path / "absent.toml")])
    assert result.exit_code == 0
    assert "Nothing starred" in result.output


def test_dry_run_never_prints_and_never_retires(monkeypatch, tmp_path: Path) -> None:
    """--dry-run must build a real PDF and then stop, leaving mail untouched.

    The queue must be non-empty, or the command returns early and the test
    passes without ever reaching the --dry-run branch.
    """
    from datetime import datetime, timezone

    from shabbat_print.models import Document, Origin

    document = Document(
        origin=Origin(kind="email", identifier="<a@example.com>", uid=1),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=timezone.utc),
        html="<div><p>The Federal Reserve declined to move rates this "
        "month, which surprised almost nobody.</p></div>",
    )

    spooled: list[Path] = []
    retired: list[list[int]] = []
    monkeypatch.setattr(
        "shabbat_print.cli.fetch_queue", lambda config: ([document], "INBOX/Trash")
    )
    monkeypatch.setattr(
        "shabbat_print.cli.spool", lambda pdf, config: spooled.append(pdf)
    )
    monkeypatch.setattr(
        "shabbat_print.cli.retire_printed",
        lambda config, uids, trash: retired.append(uids),
    )

    result = CliRunner().invoke(
        main,
        ["--dry-run", "--no-preview", "--config", str(tmp_path / "absent.toml")],
    )
    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert "1 cells" in result.output or "cells" in result.output
    assert spooled == []
    assert retired == []


def test_declining_the_prompt_prints_nothing(monkeypatch, tmp_path: Path) -> None:
    """Answering no at the confirmation must leave mail untouched."""
    from datetime import datetime, timezone

    from shabbat_print.models import Document, Origin

    document = Document(
        origin=Origin(kind="email", identifier="<b@example.com>", uid=2),
        publication="Test Weekly",
        title="Another Issue",
        date=datetime(2026, 9, 5, tzinfo=timezone.utc),
        html="<div><p>The Federal Reserve declined to move rates this "
        "month, which surprised almost nobody.</p></div>",
    )
    spooled: list[Path] = []
    monkeypatch.setattr(
        "shabbat_print.cli.fetch_queue", lambda config: ([document], "INBOX/Trash")
    )
    monkeypatch.setattr(
        "shabbat_print.cli.spool", lambda pdf, config: spooled.append(pdf)
    )
    monkeypatch.setattr("shabbat_print.runlog.record", lambda entry, **kw: tmp_path / "x")

    result = CliRunner().invoke(
        main,
        ["--no-preview", "--config", str(tmp_path / "absent.toml")],
        input="n\n",
    )
    assert result.exit_code == 0
    assert "Mail untouched" in result.output
    assert spooled == []
```

- [ ] **Step 7: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shabbat_print.cli'`

- [ ] **Step 8: Implement the CLI**

Create `src/shabbat_print/cli.py`:

```python
"""The Friday afternoon command.

The order of operations enforces the design's one invariant: mail is modified
only after a job has reached the print queue, and only for the messages whose
content actually reached it.
"""

import subprocess
import tempfile
from pathlib import Path

import click

from . import runlog
from .config import (
    DEFAULT_CONFIG_PATH,
    Config,
    ConfigError,
    load_config,
    load_publication_names,
)
from .extract import extract
from .impose import impose
from .mail import Mailbox, MailError, password_for
from .models import Document, Verdict
from .pipeline import build
from .printer import PrintError, spool


def fetch_queue(config: Config) -> tuple[list[Document], str | None]:
    """Fetch the starred messages, read-only, and discover the Trash folder."""
    config.require_mail()
    password = password_for(config.mail.host, config.mail.user)
    names = load_publication_names()
    with Mailbox(
        host=config.mail.host,
        user=config.mail.user,
        password=password,
        folder=config.mail.folder,
    ) as box:
        uids = box.search_flagged()
        documents = [extract(box.fetch(uid), uid=uid, names=names) for uid in uids]
        trash = box.trash_folder() if uids else None
    return documents, trash


def retire_printed(config: Config, uids: list[int], trash: str) -> None:
    password = password_for(config.mail.host, config.mail.user)
    with Mailbox(
        host=config.mail.host,
        user=config.mail.user,
        password=password,
        folder=config.mail.folder,
    ) as box:
        result = box.retire(uids, trash)
    if result.failed:
        click.echo(
            f"  could not retire {len(result.failed)} message(s): {result.failed}",
            err=True,
        )


@click.command()
@click.option(
    "--paper",
    type=click.Choice(["a4", "letter"], case_sensitive=False),
    default=None,
    help="Paper size. Defaults to A4; use letter when printing in the US.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=DEFAULT_CONFIG_PATH,
    help="Path to config.toml.",
)
@click.option("--dry-run", is_flag=True, help="Build the PDF but do not print or retire.")
@click.option("--no-preview", is_flag=True, help="Skip opening the PDF in Preview.")
def main(
    paper: str | None, config_path: Path, dry_run: bool, no_preview: bool
) -> None:
    """Print this week's starred newsletters, four to a side, duplex."""
    config = load_config(config_path, paper_override=paper)

    try:
        documents, trash = fetch_queue(config)
    except (MailError, ConfigError) as error:
        raise click.ClickException(str(error)) from error

    if not documents:
        click.echo("Nothing starred in the queue.")
        return

    click.echo(f"Building {len(documents)} newsletters:")
    work_dir = Path(tempfile.mkdtemp(prefix="shabbat-print-"))
    built, failed = build(documents, config, work_dir)

    for item in built:
        note = {
            Verdict.FILLER: "  (filler cell dropped)",
            Verdict.WIDOW: "  (squeezed to fit)",
            Verdict.FULL: "",
        }[item.verdict]
        click.echo(f"  {item.document.publication}: {item.cells} cells{note}")
    for failure in failed:
        click.echo(f"  SKIPPED {failure.document.publication}: {failure.error}", err=True)

    if not built:
        click.echo("Nothing could be built.", err=True)
        return

    sheets_pdf = work_dir / "sheets.pdf"
    sides = impose([item.pdf for item in built], config.printing.paper, sheets_pdf)
    cells = sum(item.cells for item in built)
    click.echo(f"\n  {cells} cells - {sides} sheet sides on {config.printing.paper.name}")
    click.echo(f"  {sheets_pdf}")

    if not no_preview:
        subprocess.run(["open", "-a", "Preview", str(sheets_pdf)], check=False)

    if dry_run:
        click.echo("\nDry run: nothing printed, nothing retired.")
        return

    destination = config.printing.printer or "the default printer"
    if not click.confirm(f"\nPrint to {destination}?", default=False):
        runlog.record({"outcome": "cancelled", "documents": len(built)})
        click.echo("Not printed. Mail untouched.")
        return

    uids = [item.document.origin.uid for item in built if item.document.origin.uid]
    runlog.record(
        {
            "outcome": "printing",
            "documents": [item.document.origin.identifier for item in built],
            "uids": uids,
        }
    )

    try:
        job = spool(sheets_pdf, config)
    except PrintError as error:
        runlog.record({"outcome": "print-failed", "error": str(error)})
        raise click.ClickException(f"{error}\nMail untouched; PDF kept at {sheets_pdf}") from error

    click.echo(f"Spooled as {job}.")
    runlog.record(
        {
            "outcome": "printed",
            "job": job,
            "documents": [item.document.origin.identifier for item in built],
            "uids": uids,
        }
    )

    if trash and uids:
        retire_printed(config, uids, trash)
        click.echo(f"Retired {len(uids)} message(s) to {trash}.")
```

- [ ] **Step 8b: Cover the printing, failure, and retirement paths**

The plan requires 100% coverage, and the four tests above never reach the
branch that spools a job or the branch that retires mail — the most
safety-critical code in the project. Append to `tests/test_cli.py`:

```python
def _queued(identifier: str = "<d@example.com>", uid: int = 4):
    from datetime import datetime, timezone

    from shabbat_print.models import Document, Origin

    return Document(
        origin=Origin(kind="email", identifier=identifier, uid=uid),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=timezone.utc),
        html="<div><p>The Federal Reserve declined to move rates this "
        "month, which surprised almost nobody.</p></div>",
    )


def test_accepting_prints_then_retires_in_that_order(monkeypatch, tmp_path: Path) -> None:
    """The invariant: mail is modified only after a job reaches the queue."""
    events: list[str] = []
    monkeypatch.setattr(
        "shabbat_print.cli.fetch_queue", lambda config: ([_queued()], "INBOX/Trash")
    )
    monkeypatch.setattr(
        "shabbat_print.cli.spool",
        lambda pdf, config: (events.append("spool"), "Printer-1")[1],
    )
    monkeypatch.setattr(
        "shabbat_print.cli.retire_printed",
        lambda config, uids, trash: events.append(f"retire:{uids}"),
    )
    monkeypatch.setattr("shabbat_print.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code == 0
    assert events == ["spool", "retire:[4]"]


def test_a_print_failure_leaves_mail_untouched(monkeypatch, tmp_path: Path) -> None:
    from shabbat_print.printer import PrintError

    retired: list[list[int]] = []

    def explode(pdf, config):
        raise PrintError("lp: no such printer")

    monkeypatch.setattr(
        "shabbat_print.cli.fetch_queue", lambda config: ([_queued()], "INBOX/Trash")
    )
    monkeypatch.setattr("shabbat_print.cli.spool", explode)
    monkeypatch.setattr(
        "shabbat_print.cli.retire_printed",
        lambda config, uids, trash: retired.append(uids),
    )
    monkeypatch.setattr("shabbat_print.runlog.record", lambda entry, **kw: tmp_path / "r")

    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")], input="y\n"
    )
    assert result.exit_code != 0
    assert "no such printer" in result.output
    assert retired == []


def test_an_unconfigured_account_says_what_to_set(monkeypatch, tmp_path: Path) -> None:
    """With no config file at all, the error names the missing keys."""
    result = CliRunner().invoke(main, ["--config", str(tmp_path / "absent.toml")])
    assert result.exit_code != 0
    assert "mail.host and mail.user" in result.output


def test_a_document_that_cannot_be_built_is_reported(monkeypatch, tmp_path: Path) -> None:
    """A newsletter that cleans down to nothing is named, not silently lost."""
    monkeypatch.setattr(
        "shabbat_print.cli.fetch_queue", lambda config: ([_empty()], None)
    )
    result = CliRunner().invoke(
        main, ["--no-preview", "--config", str(tmp_path / "absent.toml")]
    )
    assert result.exit_code == 0
    assert "SKIPPED" in result.output
    assert "Empty Weekly" in result.output
    assert "Nothing could be built" in result.output


def _empty():
    from datetime import datetime, timezone

    from shabbat_print.models import Document, Origin

    return Document(
        origin=Origin(kind="email", identifier="<e@example.com>", uid=5),
        publication="Empty Weekly",
        title="Nothing",
        date=datetime(2026, 9, 5, tzinfo=timezone.utc),
        html="<div><p>Unsubscribe</p></div>",
    )
```

Note: `test_an_unconfigured_account_says_what_to_set` needs `fetch_queue`
unmocked so `require_mail` runs; do not add a `fetch_queue` monkeypatch to it.

- [ ] **Step 9: Point the entry point at the CLI**

In `pyproject.toml`, change:

```toml
[project.scripts]
shabbat-print = "shabbat_print.cli:main"
```

- [ ] **Step 10: Run the whole suite with coverage**

Run: `make test`
Expected: every test passes; `src/shabbat_print` at 100% coverage. Add tests
for any uncovered line before continuing.

- [ ] **Step 11: Format, lint, commit, and push**

```bash
cd ~/Consulting/shabbat-print
make lint
git add -A
git commit -m "Add pipeline orchestration and the command line"
git push origin main
```

- [ ] **Step 12: First real run, in dry-run mode**

```bash
cd ~/Consulting/shabbat-print
# Once, interactively - use the host and user from your own config.toml:
keyring set "$(python -c 'import tomllib,pathlib;print(tomllib.loads(pathlib.Path.home().joinpath(".config/shabbat-print/config.toml").read_text())["mail"]["host"])')" \
            "$(python -c 'import tomllib,pathlib;print(tomllib.loads(pathlib.Path.home().joinpath(".config/shabbat-print/config.toml").read_text())["mail"]["user"])')
uv run shabbat-print --dry-run
```

Check by eye in Preview: four cells to a side, text at a readable size, no
unsubscribe footers, nothing scaled or cropped. Then verify the two open
questions from the spec by printing one sheet:

1. Does the Brother driver honour `print-scaling=none`? Measure a printed cell
   against 105 × 148.5 mm.
2. Is 9pt on a 1.35 line-height comfortable to read at A6? Adjust
   `layout.font_size_pt` in the config if not.
