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
