# shabbat-print

Fetch this week's starred newsletters over IMAP, reduce each to its article
content, typeset it onto quarter-sheet cells, impose four to a sheet side,
print duplex, and retire the printed mail. Built for a Friday-afternoon
routine: star what you want to read over Shabbat during the week, then run
this once to get a stack of paper and a clear inbox.

## Requirements

- Python 3.14 or later
- [uv](https://docs.astral.sh/uv/)
- An IMAP account with a folder you star newsletters into
- A configured printer reachable via CUPS (`lp`)
- On macOS: Pango, Cairo, and gdk-pixbuf for [WeasyPrint](https://weasyprint.org/)
  (see [Development](#development) below)

## Setup

Install the dependencies:

```
uv sync
```

Copy the example config and fill in your mail account and printer:

```
cp config.example.toml ~/.config/shabbat-print/config.toml
$EDITOR ~/.config/shabbat-print/config.toml
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

## Usage

```
uv run shabbat-print
```

This fetches the starred messages, builds the imposed PDF, opens it in
Preview for a look, and asks before printing. Useful flags:

- `--dry-run` — build and preview the PDF, but print nothing and retire
  nothing.
- `--no-retire` — print for real, but leave mail untouched (the messages
  stay starred and will be reprinted next run). Useful for checking that a
  real printout looks right without consuming the print queue.
- `--no-preview` — skip opening the PDF in Preview.
- `--paper a4` / `--paper letter` — override the configured paper size for
  one run.
- `--config PATH` — use a config file other than the default.

Run `uv run shabbat-print --help` for the full list.

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
`shabbat_print._libpath` handles this automatically: before `render.py`
imports WeasyPrint, it sets `DYLD_FALLBACK_LIBRARY_PATH` to whichever of
`/opt/homebrew/lib` or `/usr/local/lib` has the library, if the variable
isn't already set. So running the tool, `uv run shabbat-print`, or the test
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
