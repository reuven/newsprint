.PHONY: fixtures test lint

# WeasyPrint needs Pango/Cairo/gdk-pixbuf, installed via Homebrew on macOS.
# Homebrew's lib directory isn't on the default dlopen search path, so we
# point dyld at it here. dyld only reads DYLD_* at process start, so this
# has to happen before `uv run` launches, not from inside a conftest.py.
# On a machine without Homebrew (e.g. Linux, or macOS with the libraries
# installed some other way), `brew --prefix` fails and BREW_PREFIX is
# empty, so we leave the environment untouched rather than exporting a
# bogus "/lib".
BREW_PREFIX := $(shell brew --prefix 2>/dev/null)
ifneq ($(BREW_PREFIX),)
export DYLD_FALLBACK_LIBRARY_PATH := $(BREW_PREFIX)/lib
endif

fixtures:
	uv run python scripts/make_fixtures.py

test:
	uv run pytest --cov=shabbat_print --cov-report=term-missing --cov-branch

lint:
	uv run ruff format src tests scripts
	uv run ruff check src tests scripts
