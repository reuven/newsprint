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
`make test` and `make lint` do this automatically by exporting
`DYLD_FALLBACK_LIBRARY_PATH` from `brew --prefix`. If you invoke `pytest`
directly instead of through `make test`, export it yourself first:

```
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"
uv run pytest
```
