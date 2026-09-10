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

- Python 3.14 or later
- [uv](https://docs.astral.sh/uv/)
- An IMAP account, and a folder you star newsletters into
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
inside it. A mail rule that files newsletters into that folder as they
arrive, and a star on the ones you actually want this week, is the intended
shape.

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
  nothing.
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
- `--no-preview` — skip opening the PDF in Preview.
- `--paper a4` / `--paper letter` — override the configured paper size for
  one run.
- `--config PATH` — use a config file other than the default.

Run `newsprint --help` for the full list.

## Filtering

`clean.py` removes only what it can confidently identify as chrome (an
"Unsubscribe" line, a "view in browser" bar, and the like); everything else
passes through. So an unsubscribe line, a mailing address, or a similar bit
of boilerplate can still appear on a printout if it doesn't clearly match
that check — `trim.py`'s final-cell judgment is the actual backstop against
a filler page, not `clean.py`'s removal.

## Development

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
