# shabbat-print — design

**Date:** 2026-09-07
**Status:** approved design, not yet implemented

## Purpose

Every Friday afternoon, produce one PDF containing the week's newsletters and
saved web articles, imposed four to a side and duplexed, and send it to the
laser printer — replacing a manual pass through Thunderbird that is slow,
boring, and wasteful of paper.

The manual process fails in five specific ways. The design addresses each:

| Problem | Cause | Fix |
|---|---|---|
| Tedious and boring | Manual selection, one print dialog per message | One command |
| Sometimes prints full size | The CUPS dialog's 4-up setting doesn't always take | The tool imposes 4-up itself; CUPS receives a finished page |
| Money Stuff must print alone | Thunderbird's multi-message print path | Nothing goes through Thunderbird |
| One footer line costs a whole sheet | Ads, link blocks, and unsubscribe boilerplate | Never rendered; plus a last-cell content check |
| Non-starred issues are hard to include | No way to review what arrived | A numbered list of the week's unstarred mail |

## Non-goals

- Managing the ~2,200-message backlog in `toprint`. The tool looks at a
  seven-day window and ignores everything older. Pruning the backlog is a
  separate job.
- Reading mail, replying, or any Thunderbird integration. The tool talks to
  the IMAP server directly and never touches the local profile.
- Circumventing hard paywalls. Sites where the user holds a subscription are
  fetched with that subscription's session; sites where they do not are
  fetched anonymously and may yield nothing.
- Colour. The target printer is a monochrome laser.

## Decisions

Each of these was settled during design; the rationale matters because it
constrains implementation.

**Source of truth is IMAP, not the local mbox.** Thunderbird's cached
`INBOX.sbd/toprint` carries `X-Mozilla-Status: 0001` on all 2,226 messages —
"read", and nothing else. For IMAP folders the star lives in the `.msf` Mork
index and on the server, never in the cached mbox. Any local computation of
what is starred returns zero. The tool therefore searches `FLAGGED` over IMAP.

**Newsletters are packed one per cell, not one per sheet.** Today each
newsletter is its own print job and so begins on a fresh sheet; at four-up
duplex a sheet holds eight logical pages, so a three-page newsletter wastes
five cells. Packing per cell takes roughly fifteen sheets down to six or
seven. Every cell still belongs to exactly one newsletter and carries its name,
so the page stays readable; the cost is that a single newsletter can no longer
be torn off the stack on its own.

**Reader mode, keeping content figures.** A four-up cell is about 4.25in by
5.5in — paperback size. Newsletter HTML targets a 600px phone column, so
rendering it faithfully and shrinking to fit lands body text near 5pt. The
tool extracts the article text and re-typesets it. Images that look
substantive are kept and converted to grayscale; tracking pixels, logos,
banners, and social buttons are dropped.

**Printed mail is retired: marked read, unstarred, moved to Trash.** The star
means "queued to print", so the queue drains on its own and Thunderbird stays
the place the queue is visible. Retirement happens only after `lp` accepts the
job, only for messages that actually reached the PDF, and every affected
Message-ID is written to a run log first.

**Web articles are fetched through an app-owned browser profile.** The tool
keeps its own Chromium profile which the user logs into once per site. Friday's
fetch reuses those sessions headlessly. The user's everyday Chrome is never
touched and never needs a debugging port open.

**A4 is the default paper; Letter is a switch.** The printer lives in Israel
and reports `*A4` as its default. `--paper letter` covers printing while
travelling in the US. Cell size, imposition, and the CSS page box are all
derived from this one setting, so they cannot drift apart.

**URLs are supplied by pasting at print time.** No capture daemon, no
bookmarklet listener, no `add` subcommand. The run ends with a prompt.

## Architecture

Nine modules. Three perform I/O — the two sources at the front and the printer
at the back — and `cli.py` orchestrates. The five in between are pure functions
over data, which is what makes the whole middle testable offline against real
newsletters.

```
  mail.py ─┐                                                              ┌─ printer.py ─▶ lp
  (IMAP)   │                                                              │   (spool)
           ├─▶ extract.py ─▶ clean.py ─▶ render.py ─▶ trim.py ─▶ impose.py ┘
  web.py  ─┘    MIME/DOM      reader      HTML→PDF     fit the    4 cells
  (browser)     → Document     mode       per cell     last cell  → sheet
                                              ▲            │
                                              └────────────┘
                                          re-render callback
```

`trim.py` is the one stage that can reach backwards. It receives a
`rerender(compression: float) -> Path` callback from `render.py` rather than
importing it, so the dependency is injected and tests can pass a fake.

| Module | Responsibility | Key dependency |
|---|---|---|
| `mail.py` | IMAP: search, fetch, and the post-print retirement | `imaplib` (stdlib) |
| `web.py` | Fetch a URL through the logged-in Chromium profile | `playwright` |
| `extract.py` | MIME message or HTML page → `Document` | `email` (stdlib), `trafilatura` |
| `clean.py` | Strip chrome, ads, footers; judge images | `lxml`, `beautifulsoup4` |
| `render.py` | `Document` + house stylesheet → per-cell PDF | `weasyprint` |
| `trim.py` | Classify the trailing cell; drop it, or squeeze to lose it | `pymupdf` |
| `impose.py` | Tile four cells onto a sheet of the selected paper | `pypdf` |
| `printer.py` | Build and run the `lp` command | `subprocess` (stdlib) |
| `cli.py` | Orchestration, selection prompt, preview, confirmation | `click` |

### The quarter-page identity

A 2×2 grid halves both dimensions at once, so a cell's aspect ratio equals the
sheet's — for any rectangle, not just for these two papers. A cell is therefore
never a distorted version of a page, and no paper-specific reasoning is needed.

(The A series' √2 ratio is what makes *2-up* work, where only one dimension is
halved: A4 folds to A5 folds to A6 with the shape preserved. At 4-up that
property does no work. It is still a happy accident that quartered A4 lands on
**A6, 105×148.5mm**, a named size the target printer lists among its `PageSize`
options.)

The consequence is what matters. `render.py` typesets each document directly
onto a cell-sized page, and `impose.py` places four of them at **100% scale**.
Nothing is resampled, no margin is lost to an aspect mismatch, and a font
specified at 9pt measures 9pt on the paper. It also means every page count in
the system is a count of real cells rather than of logical pages awaiting an
unknown scale factor, which keeps the trimming arithmetic honest.

| Paper | Sheet | Cell | Text block at 9mm margins |
|---|---|---|---|
| **A4** (default) | 210 × 297 mm | A6, 105 × 148.5 mm | 87 × 130.5 mm |
| Letter (`--paper letter`) | 8.5 × 11 in | 4.25 × 5.5 in | 3.55 × 4.8 in |

Cell size is *derived* from the paper, never configured independently, so the
two can never drift out of agreement.

## Data model

```python
@dataclass(frozen=True)
class Document:
    """One thing to be printed, cleaned and ready to render."""
    origin: Origin              # where it came from, and how to retire it
    publication: str            # "Money Stuff", "The Economist"
    title: str                  # subject line, or article headline
    author: str | None
    date: datetime
    html: str                   # cleaned; the only thing render.py reads
    images_kept: int
    images_dropped: list[DroppedImage]

@dataclass(frozen=True)
class Origin:
    kind: Literal["email", "url"]
    identifier: str             # Message-ID, or the URL
    uid: int | None             # IMAP UID; None for URLs

@dataclass(frozen=True)
class DroppedImage:
    src: str
    reason: str                 # "tracking-pixel", "banner-aspect", "too-small", ...
```

`Document` is the seam. Everything upstream of it deals in MIME parts, DOM
trees, and HTTP; everything downstream deals only in cleaned HTML and PDF
pages. Adding a third source type later means writing one function that
returns a `Document`, and changing nothing else.

## The run, end to end

1. **Connect read-only.** `mail.py` opens IMAP to `imap.emailsrvr.com`,
   authenticating with a password read from the macOS Keychain via `keyring`,
   and selects `INBOX/toprint` with `readonly=True`.

2. **Two searches.** `FLAGGED` gives the print queue. `UNFLAGGED SINCE <date>`
   gives the review list, where the date is the last successful run or, absent
   one, seven days ago.

3. **Build the queue.** Each flagged message runs extract → clean → render →
   trim, producing a PDF of whole cells and a cell count. Failures are
   reported and skipped, never silently dropped.

4. **Review the rest.** The unflagged set is extracted only far enough to
   yield publication and subject, then shown as a numbered list grouped by
   publication. The user selects by number and range; selections join the
   queue and go through the same pipeline.

5. **Paste URLs.** The user is prompted for URLs. Each is fetched through the
   Chromium profile, extracted with `trafilatura`, and enters the pipeline at
   the same point a newsletter does.

6. **Impose.** Every document's pages are concatenated, each padded to a whole
   cell boundary so the next document starts at the top of a fresh cell, then
   tiled four to a sheet in reading order — top-left, top-right,
   bottom-left, bottom-right. Output page 1 is the front of sheet 1 and holds
   cells 1–4; output page 2 is its back and holds cells 5–8.

7. **Preview and confirm.** The PDF opens in Preview.app. The terminal reports
   cells, sheets, trimmed pages, and image counts, then asks to print.

8. **Print.** `lp -d Brother_MFC_L2700DW_series -o media=A4
   -o sides=two-sided-long-edge -o print-scaling=none`. The media follows
   `--paper`, defaulting to A4. The printer's own defaults are already `*A4`
   and `*DuplexNoTumble` (long-edge), so the flags agree with the hardware
   rather than fighting it.

9. **Retire.** Only now does `mail.py` open a second, read-write connection.
   For each email document that reached the PDF: `+FLAGS \Seen`,
   `-FLAGS \Flagged`, then `MOVE` to the Trash folder. The run log is written
   before the first mutation.

The read-only-then-reopen split is deliberate: every stage that can crash —
HTML parsing, rendering, imposition — runs on a connection that has no power
to modify mail. The mutating connection is opened last, performs three small
operations per message, and closes.

## Heuristics

### Filler removal

Handled in two places, with different jobs.

**Structural, in `clean.py`.** The reported symptom — a printout spilling onto
another sheet because of "footers, advertisements, links" — is fixed by never
rendering those elements in the first place. `clean.py` emits only what it
judges to be article content, so in the common case a filler page cannot come
into existence: there is nothing to put on it.

Targets: the unsubscribe block, "view in browser" bars, sponsor and
advertisement slots, social button rows, link roundups, sign-offs ("thanks for
reading", "see you next week"), "you are receiving this because" boilerplate,
mailing addresses, copyright lines, and preference-management links. Detected
by a combination of link destination, element text against a phrase list, link
density within the block, and position in the document — trailing blocks are
far more likely to be boilerplate than leading ones.

**Post-hoc, in `trim.py`.** The safety net, for chrome the stripper failed to
recognise and therefore rendered inline.

The rule is about what the text *is*, never how much of it there is. A last
cell holding 1,200 characters of pure link roundup should go; one holding 200
characters of a real closing paragraph should stay. So `trim.py` classifies
each non-blank line on the final cell as boilerplate or content, and drops the
cell when content accounts for **less than 15% of its characters**.

A line is **boilerplate** when any of these holds:

- it matches the boilerplate phrase list (the same list `clean.py` uses)
- it is a bare URL, or link text with no surrounding prose
- it is under 40 characters and does not end in sentence punctuation — the
  shape of a link-roundup item or a navigation label

Everything else is **content**. The 15% threshold is configurable and
calibrated against the fixture corpus in phase 2.

The error is deliberately asymmetric. Keeping a junk cell wastes a quarter of
one side of one sheet; dropping a content cell silently destroys something the
user wanted to read. So the test is *"is there any real content here?"* rather
than *"is there mostly junk here?"*, and every trimmed cell is reported by name
in the run summary with its content ratio, so a wrong drop is visible rather
than invisible.

### Widow squeeze

A distinct case that must not be confused with filler: a document whose *real*
content overruns its last cell by a few lines. Deleting that page would lose
content, so the fix is to make it fit instead.

`trim.py` classifies the final cell once and acts on the verdict:

| Verdict | Test | Action |
|---|---|---|
| `FILLER` | content is under 15% of the cell's characters | drop the cell |
| `WIDOW` | not filler, but fills under 20% of the cell's height | squeeze |
| `FULL` | anything else | leave alone |

`FILLER` is tested first, and the two tests are independent: a long cell of
pure advertising is `FILLER` despite being full, and a short cell of real
prose is `WIDOW` despite being nearly empty.

Squeezing calls `rerender(0.99)` and keeps the result only if the cell count
actually drops. If it does not, it tries `rerender(0.98)`, and otherwise
reverts to the original. Bounded at two extra renders and 2% total
compression, so it cannot quietly degrade typography in pursuit of paper.

### Image judgment

Drop when any of these holds:

- intrinsic dimensions ≤ 3px in either axis (tracking pixel)
- rendered width below 100px (icons, social buttons, logos)
- aspect ratio beyond 6:1 in either direction (banner)
- `src` contains `pixel`, `beacon`, `track`, `/open`, or a known ad host
- the image is wrapped in a link to a social or unsubscribe destination
- `alt` text matches logo or sponsor patterns

Keep everything else, convert to grayscale, cap at cell width.

Because a silently wrong drop is indistinguishable from an image that was
never there, every run prints `kept N images, dropped M`; `--images-report`
lists each dropped image with its source URL and reason; and
`--keep-all-images` bypasses the judgment for one run.

### Paywalls

`trafilatura` reads the parsed DOM and ignores CSS, so soft paywalls — where
the server sends the full article and hides it behind an overlay — yield their
text without any special handling. Foreign Affairs behaves this way. Hard
paywalls, where the body is truncated server-side, require the logged-in
profile. When extraction returns implausibly little text for an article, the
tool says so and asks whether to include it anyway rather than printing a
teaser.

## Error handling

| Failure | Response |
|---|---|
| IMAP connection or auth fails | Abort before anything is built; nothing changed |
| One document fails to extract or render | Report it by name, skip it, continue; it is **not** retired, so it stays starred for next week |
| `lp` returns non-zero | No retirement at all; the PDF is kept and its path reported |
| Retirement fails partway | Log which UIDs succeeded; report the remainder for manual handling |
| A URL yields too little text | Warn with the word count and ask before including |
| Preview declined | Exit without printing and without retiring; the PDF is kept |

The invariant: **a message is retired only if its content reached a spooled
print job.** Every other path leaves mail untouched.

## Configuration and secrets

`~/.config/shabbat-print/config.toml`:

```toml
[mail]
host   = "imap.emailsrvr.com"
user   = "reuven@lerner.co.il"
folder = "INBOX/toprint"
trash  = "auto"        # discover via the \Trash special-use attribute

[print]
printer = "Brother_MFC_L2700DW_series"
paper   = "A4"          # or "Letter"; overridden per-run by --paper
duplex  = "two-sided-long-edge"

[layout]
# The cell is always a quarter of the sheet, derived from print.paper.
margin      = "9mm"
font_size   = "9pt"
line_height = 1.35

[window]
fallback_days = 7
```

The IMAP password is never stored in the config or the repository. It is read
at run time with `keyring get imap.emailsrvr.com reuven@lerner.co.il`,
matching the existing pattern for the GitHub and PyPI tokens.

A `publications.toml` maps sender addresses and `List-Id` values to display
names, so cells read "Money Stuff" rather than
`noreply@news.bloomberg.com`. Seeded from the real senders in the archive.

Run state lives in `~/.local/state/shabbat-print/runs/`, one JSON file per
run, recording the timestamp, the documents printed, the Message-IDs retired,
and the outcome. The last successful run's timestamp defines the review
window.

## Testing

Test-driven throughout, with `pytest`, targeting 100% coverage.

The 2,226-message `toprint` mbox is a corpus of precisely the input this tool
must survive — NYT, the Economist, Axios, Wired, the New Yorker, Stratechery,
and dozens of Substacks, each with its own HTML idiom. A `make fixtures` step
samples roughly one message per sender into `tests/fixtures/`, and every test
for `extract`, `clean`, `trim`, and `impose` runs against real newsletters
with no network and no IMAP.

Two guards are mandatory when splitting the mbox, both learned the hard way in
a previous archive project:

1. **Assert that parsed bytes equal file size.** Thunderbird writes a bare
   `From \r` separator with no sender or date; a splitter expecting the usual
   trailing four-digit year once read a 1.1 GB folder as two messages.
2. **Do not trust a buffered read.** A 64 MB buffer flush once silently
   dropped 60 MB from a folder containing two 95 MB messages.

Rendering is tested on page counts and extracted text, never on pixel
comparison — a golden-image test against WeasyPrint breaks on every font
update and proves nothing about correctness. `impose` is tested with a
synthetic eight-page PDF carrying known text on each page: impose it, then use
PyMuPDF text coordinates to prove cell 1 landed top-left and cell 6 landed
top-right of the second sheet. `mail.py` is tested against a fake IMAP server;
tests never touch the network.

Fixtures are real personal mail. They are kept in `tests/fixtures/` and
gitignored, regenerable with `make fixtures`, so the repository stays
publishable while the tests stay real.

## Dependencies

Added through `uv`:

`weasyprint`, `pypdf`, `pymupdf`, `beautifulsoup4`, `lxml`, `trafilatura`,
`playwright`, `keyring`, `click`; `pytest` and `pytest-cov` for development.

WeasyPrint's native dependencies — Pango, Cairo, and gdk-pixbuf — are already
installed via Homebrew. Playwright's Chromium is already cached in
`~/Library/Caches/ms-playwright`.

## Build order

Each phase ends with something that works and is committed.

| Phase | Delivers | Usable? |
|---|---|---|
| 1 | Config, `Document`, `make fixtures` from the mbox | — |
| 2 | `extract` + `clean` + `render` + `trim`: fixture → per-cell PDF | PDFs on disk |
| 3 | `impose` + `printer`: cells → sheets → `lp` | **Can print from fixtures** |
| 4 | `mail.py` read-only: real starred mail through the pipeline | **Replaces the manual job**; unstar by hand |
| 5 | Retirement: mark read, unstar, move to Trash | Queue drains itself |
| 6 | Interactive selection of the week's unstarred mail | Problem 5 solved |
| 7 | Image judgment and reporting | Charts survive |
| 8 | `web.py`: browser profile, login flow, URL paste | Web articles |

Phase 4 is the point at which the tool earns its keep. Phases 5 onward are
refinements to a working system.

## Open questions

Resolved during implementation, not now:

- The Trash folder's actual name on Rackspace. Discovered via the `\Trash`
  special-use attribute, with a config fallback.
- Whether the Brother driver honours `print-scaling=none`. Verified with one
  test sheet before trusting it.
- Font family and size. 9pt on 1.35 line-height over A6's 87mm measure gives
  roughly 55 characters per line and 30 lines per cell, which is a reasonable
  starting point; one real print run calibrates it. Quarter-Letter's 3.55in
  measure is close enough that one setting should serve both papers, but that
  assumption is worth checking on the first US print.
- Whether `trafilatura` outperforms hand-written stripping on table-based
  email HTML, or only on web pages. Measured against the fixture corpus in
  phase 2.
