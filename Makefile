.PHONY: fixtures test lint audit latest

# WeasyPrint needs Pango/Cairo/gdk-pixbuf, installed via Homebrew on macOS.
# Homebrew's lib directory isn't on the default dlopen search path, so we
# point dyld at it here. This is now belt-and-braces: newsprint._libpath
# sets DYLD_FALLBACK_LIBRARY_PATH from Python before weasyprint is imported
# (ctypes.util.find_library reads os.environ live, unlike dyld itself), so
# `uv run newsprint` works without this export. We keep it here so
# `make test` still works even if that module is ever broken.
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
	uv run pytest --cov=newsprint --cov-report=term-missing --cov-branch

lint:
	uv run ruff format src tests scripts assets
	uv run ruff check src tests scripts assets

# What a new user actually installs. `make test` runs against uv.lock,
# which is right for reproducing a result and useless for noticing that a
# dependency has moved - a user installing from PyPI never sees the lock
# file. That gap shipped a crash once: WeasyPrint 70 changed the
# url_fetcher contract while the lock held 69, so the suite passed and a
# fresh install died on any image that failed to fetch.
#
# Leaves uv.lock upgraded on purpose. If the suite passes, commit it; the
# lock should not sit behind what people are being given.
latest:
	uv lock --upgrade
	uv sync
	uv run pytest -q --cov=newsprint --cov-branch --cov-fail-under=100
	git --no-pager diff --stat uv.lock

# Processes the user's full local "toprint" archive (roughly 2,231
# messages) through extract and clean_document and reports what got
# removed. Read-only, and slow over the full archive; pass --limit N to
# scripts/audit.py directly for a quick run.
audit:
	uv run python scripts/audit.py
