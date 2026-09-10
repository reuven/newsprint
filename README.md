# newsprint

Turn a week of email newsletters into a small stack of paper.

newsprint fetches the messages you starred, strips each one down to its
article content, typesets it onto quarter-sheet cells, imposes four to a
sheet side, and prints it duplex — then unstars the mail and files it away,
so your inbox is clear and your reading is somewhere a screen isn't.

Star what you want during the week; run it once when you want the paper. It
was written for reading over Shabbat, away from screens, but nothing in it
is specific to that: it suits a flight, a commute, a weekend, or anyone who
would rather read long things on paper than on a phone.

## Requirements

- Python 3.12 or later
- [uv](https://docs.astral.sh/uv/)
- An IMAP account (including Gmail), and a folder you star newsletters into
- Pango, Cairo and gdk-pixbuf, for [WeasyPrint](https://weasyprint.org/) —
  see [Install](#install)
- A printer reachable via CUPS (`lp`), if you want newsprint to do the
  printing. With `--no-print` it just hands you a PDF, so macOS and Linux
  both work without one — and so does anything else that can run Python and
  open a PDF.

## Install

Install it as a tool, which puts `newsprint` on your PATH so you never type
`uv run`:

```
uv tool install newsprint
```

Or, to try it without installing anything:

```
uvx newsprint --help
```

To install from a clone, run `uv tool install .` in the checkout. For
working *on* newsprint rather than with it, see
[Development](#development).

WeasyPrint needs Pango, Cairo and gdk-pixbuf, which are system libraries
rather than Python packages — install them first:

```
brew install pango                      # macOS
sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b   # Debian/Ubuntu
```

## Setup

Copy the example config and fill in your mail account and printer:

```
cp config.example.toml ~/.config/newsprint/config.toml
$EDITOR ~/.config/newsprint/config.toml
```

The IMAP password is never stored in the config file. Put it in your system
keychain instead:

```
keyring set <mail.host> <mail.user>
```

for example:

```
keyring set imap.gmail.com you@gmail.com
```

See `config.example.toml` for the full set of options, including a note on
what Gmail specifically needs (an app password, and a folder path that isn't
`INBOX/toprint`).

## Choosing what to print

Star the messages you want, then run the tool. That is the whole workflow,
and it is not specific to any mail client: the tool never talks to your mail
program at all. It connects to your IMAP server and asks it for

```
UID SEARCH FLAGGED
```

`\Flagged` is a system flag defined by IMAP itself (RFC 3501), so whatever
you use to star a message — Thunderbird's star, Apple Mail's flag, Outlook's
follow-up flag, Gmail's star, your phone — is setting the same server-side
bit that this reads back. Star from anywhere; run this from your desktop.

Two things select a message: the **folder** narrows, the **star** picks. The
tool selects one mailbox (`mail.folder`) and searches for flagged messages
inside it.

How messages get into that folder, and how you decide what to star, is
entirely up to you — the tool only reads the result. The author's setup, as
one example: mail rules file every newsletter subscription into a `toprint`
folder as it arrives, and then during the week he stars the subset he
actually wants to read on paper. Everything unstarred stays in the folder
and is offered at run time. You could equally star straight from your phone
as things arrive, or star nothing during the week and pick entirely from the
checklist.

Anything in that folder you did *not* star is offered to you at run time, in
a checklist, so a newsletter you forgot to star is one keypress away rather
than a lost cause.

### Gmail

Gmail has no folders, only labels — but a label is exactly what IMAP shows
as a mailbox, so the workflow maps over cleanly:

| Other clients | Gmail |
| --- | --- |
| A rule moves newsletters into a `toprint` folder | A filter applies a `toprint` label |
| `folder = "INBOX/toprint"` | `folder = "toprint"` — a label is a top-level mailbox, not nested under INBOX |
| Star the ones to print | Star the ones to print |

You can also skip the label and set `folder = "INBOX"`, relying on stars
alone. That is less setup, but the "what else arrived this week" checklist
then offers your whole inbox rather than just newsletters.

Gmail also needs an app password rather than your account password; see
`config.example.toml`, which covers both points.

Two Gmail details worth checking on your first run, with `--no-retire` so
nothing is modified:

- **Superstars.** Gmail can show several star colors. They are widely
  reported to all map to the one `\Flagged` bit over IMAP, so "only red
  stars" is unlikely to survive the protocol — confirm before relying on it.
- **Retiring.** Messages are unstarred, marked read, and moved to the
  server's `\Trash` mailbox, which on Gmail is `[Gmail]/Trash`. That
  discovery is automatic, but worth watching once.

## Usage

```
newsprint
```

This fetches the starred messages, builds the imposed PDF, opens it in
Preview for a look, and asks before printing. Useful flags:

- `--dry-run` — build and preview the PDF, but print nothing and retire
  nothing. Exactly equivalent to passing `--no-print --no-retire` together;
  it exists as one flag because "show me what I would get" is a thing you
  want often enough to have a name.
- `--no-retire` — print for real, but leave mail untouched (the messages
  stay starred and will be reprinted next run). Useful for checking that a
  real printout looks right without consuming the print queue.
- `--output PATH` — write the finished PDF somewhere you can find it
  instead of a temp directory. An existing directory gets a dated file
  inside it (`newsprint-2026-09-11.pdf`).
- `--no-print` — build the PDF but do not send it to a printer; print it
  yourself from the file. Still offers to retire the mail, after asking
  whether the printing actually worked. `--no-print --output ~/reading/` is
  a complete workflow on a machine with no printer configured at all.
- `--unretire` — undo the last retirement: move those messages back out of
  the trash and re-star them. Builds nothing and prints nothing. See
  [Undoing a retirement](#undoing-a-retirement).
- `--no-preview` — skip opening the PDF in Preview.
- `--paper a4` / `--paper letter` — override the configured paper size for
  one run.
- `--config PATH` — use a config file other than the default.

Run `newsprint --help` for the full list, and `newsprint --version` for the
release, project page and author.

## Summary pages (optional)

newsprint can put one or two extra pages at the front of the packet, written
by Claude from the cleaned text of everything in it:

- **This Week's Topics** — what actually recurs across the week's reading.
  Not a list of subjects, which the contents page already gives you, but
  what connects them.
- **A page of your own** — only if you ask for one, by adding a
  `[summary.personal]` section. You describe in your own words what you are
  watching for in your reading, and give the page a heading; it flags things
  worth chasing. This was hardcoded to the author's own newsletter until
  newsprint became something other people install, which is why it is free
  text in your config rather than a setting.

**This is off by default, and stays off unless you turn it on.** With no
API key configured, nothing is sent anywhere and the packet prints exactly
as it otherwise would, with one line saying the summary was skipped and
why. There is no degraded mode and no silent failure: a missing key, no
network, an API error, or a timeout all end the same way — the packet
still prints, just without these pages.

To turn it on you need an [Anthropic API key](https://console.anthropic.com/).
The key never goes in `config.toml`. Point the config at a file that holds
it instead:

```toml
[summary]
enabled = true
```

That is the whole of it. Every other key has a default, so set one only to
change it:

| key | default |
| --- | --- |
| `api_key_file` | `~/.env` — a dotenv-format file |
| `api_key_var` | `ANTHROPIC_API_KEY` |
| `model` | `claude-opus-5` |
| `timeout_seconds` | `120.0` |

Optionally, add a page of your own. Leave this section out entirely and you
get only the topics page — its presence is the switch, so there is no flag
to set:

```toml
[summary.personal]
title = "Bamboo Weekly Candidates"
looking_for = """
I write a newsletter of pandas exercises built on real public datasets.
Flag things with public data plausibly behind them.
"""
```

Only `api_key_var` is read from that file, and the key is never written to
the run log or echoed on failure.

**What it costs, and what leaves your machine.** One API call per run,
carrying the cleaned text of every newsletter in the packet — on a typical
week that is roughly 40k input tokens and a few hundred out. That text goes
to Anthropic. If that is not something you want for your mail, leave the
feature off; everything else works without it.

## Images

Most images in a newsletter are decoration — a masthead, a social icon, a
tracking pixel — and on a quarter-sheet page they cost space the article
needs. So newsprint drops images by default, and keeps only the ones the
author treated as part of their argument.

An image is kept when it is at least 300px wide **and** the surrounding
text says it matters:

- the line before it ends in a colon, or reads like "here's the chart";
- the line after it opens a caption — `Source:`, `Chart:`, `Figure`,
  `Credit:`, `Data:`;
- the block after it is nothing but a parenthesised link, which is how
  Platformer cites the screenshots in "Those good posts".

A kept image is fetched at print time, converted to grayscale, and resized
down to the cell's text column — 87mm at 200dpi, so 685px. Nothing else is
ever fetched: a dropped image's URL is recorded for the run report and
never requested, which is also why tracking pixels never phone home.

Among the images that are *not* kept, one carrying substantial `alt` text
can leave a text placeholder in the flow instead, so the sentence around it
still makes sense:

```
[figure: US-China trade balances as a percent of GDP, 2010-2026]
```

In practice that is rare — most newsletter images ship with `alt=""`.

If an image cannot be fetched — a dead URL, a login wall, a timeout — the
run says so and prints the packet without it.

Measured across a 268-newsletter archive: **0.97 images kept per
newsletter**, about 16 dropped, and one text placeholder in the entire
corpus. Roughly one figure per newsletter is about what a reader would
point at and call a chart, and the 16 are mastheads, icons and spacers.

## Undoing a retirement

Retiring is the one irreversible thing newsprint does: printed messages are
marked read, unstarred, and moved to your trash. If a run retires something
it should not have, `newsprint --unretire` puts the last batch back.

```
newsprint --unretire
```

It reads the most recent retirement from the run log, finds those messages
in the trash **by Message-ID** — the uids the run recorded name nothing once
a message has moved — moves them back to the folder they came from, and
re-stars them. It asks before touching anything.

Two limits worth knowing:

- **It cannot outlive your trash.** Once the mail host empties it, the
  messages are gone and no run log will bring them back. Unretire soon or
  not at all.
- **Messages come back starred but read.** Retirement never records whether
  a message had already been read before the run, so that cannot be undone
  honestly, and is not guessed at.

Only the most recent retirement can be undone. If you need an older one, its
run log entry is in `~/.local/state/newsprint/runs/` and lists the
Message-IDs, which your mail client can search for.

## Filtering

`clean.py` removes only what it can confidently identify as chrome (an
"Unsubscribe" line, a "view in browser" bar, and the like); everything else
passes through. So an unsubscribe line, a mailing address, or a similar bit
of boilerplate can still appear on a printout if it doesn't clearly match
that check — `trim.py`'s final-cell judgment is the actual backstop against
a filler page, not `clean.py`'s removal.

## Development

The test suite runs on a clone with no extra setup, and reaches 100%
statement and branch coverage there:

```
uv sync
uv run pytest --cov=newsprint --cov-branch --cov-fail-under=100
```

A few tests need `tests/fixtures/` — a local archive of real newsletters,
built by `make fixtures` from the author's own mail. That directory is
gitignored, because it is other people's copyrighted writing and not the
author's to redistribute. Those tests assert corpus-wide properties ("no
real newsletter loses a headline to this rule") and skip cleanly when the
archive is absent; every line and branch they cover also has a unit test,
so the coverage guarantee does not depend on having them.


Rendering newsletters to PDF uses [WeasyPrint](https://weasyprint.org/), which
depends on Pango, Cairo, and gdk-pixbuf. On macOS, install them with Homebrew:

```
brew install pango cairo gdk-pixbuf
```

Homebrew's `lib` directory isn't on the default `dlopen` search path, so
WeasyPrint can fail to import with an error like `cannot load library
'libgobject-2.0-0'` unless something points the dynamic linker at it.
`newsprint._libpath` handles this automatically: before `render.py`
imports WeasyPrint, it sets `DYLD_FALLBACK_LIBRARY_PATH` to whichever of
`/opt/homebrew/lib` or `/usr/local/lib` has the library, if the variable
isn't already set. So running the tool, `newsprint`, or the test
suite all work out of the box on macOS with a Homebrew install in one of
those two locations — no manual export needed.

If your Homebrew libraries live somewhere else, set
`DYLD_FALLBACK_LIBRARY_PATH` yourself before running; an explicit value
always takes priority:

```
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"
uv run pytest
```

Run the tests and linter with:

```
make test
make lint
```

## License

MIT. See [LICENSE](LICENSE).
