"""The app-owned, persistent Chromium profile (phase8-urls.md).

Two decisions made during brainstorming, not revisited here: the tool
keeps its own Chromium profile rather than touching the user's everyday
Chrome, and URLs are pasted at print time rather than captured ahead of
it (see cli.py's own prompt). `login()` opens the profile visibly so the
user can sign into their subscription sites once; `fetch_html()` reuses
the same profile headlessly on Friday's run.

Both take `sync_playwright_fn` as their injection point - the same "pass
anything with the right shape" contract printer.spool's `runner` and
mail.Mailbox's `imap_factory` already use. No test in this project may
launch a real browser, so every test drives these functions through a
fake standing in for playwright.sync_api.sync_playwright() itself; the
real default is never invoked except when a user actually runs `shabbat-
print login` or lets a real print run fetch a pasted URL.
"""

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

# Where the profile lives, relative to the config directory (config.py's
# DEFAULT_CONFIG_PATH.parent) - never in the repository, and never named
# after the user's own subscriptions (see this module's docstring and the
# spec's "nothing personal in any tracked file").
PROFILE_DIRNAME = "browser-profile"

# A per-URL ceiling on how long a fetch may block, the same idea as
# render.py's own _IMAGE_FETCH_TIMEOUT_S for a kept chart: a slow or dead
# site must not stall the whole run over one pasted URL.
DEFAULT_TIMEOUT_S = 30.0

# playwright.sync_api.sync_playwright itself: a zero-argument callable
# returning a context manager that yields a Playwright driver object with
# a `.chromium` BrowserType. Typed loosely (Any) rather than with a
# hand-rolled Protocol, matching render.py's own ImageFetcher = Callable[
# [str], object] - the contract is "has the right shape", not a specific
# class.
SyncPlaywrightFactory = Callable[[], AbstractContextManager[Any]]


class FetchError(Exception):
    """A page could not be fetched - a timeout, a dead host, or similar."""


def profile_dir(config_dir: Path) -> Path:
    """Where the persistent profile lives for a given config directory."""
    return config_dir / PROFILE_DIRNAME


def login(
    profile: Path, sync_playwright_fn: SyncPlaywrightFactory = sync_playwright
) -> None:
    """Open the persistent profile visibly; return once the user closes it.

    Closing every window in a launch_persistent_context() browser closes
    the context itself (there is no separate top-level Browser object to
    watch, unlike browser_type.launch()) - so waiting for the context's
    own "close" event, rather than polling its page list, is what lets
    this return exactly when the user is done, with no timeout of its own
    to tune.
    """
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright_fn() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile), headless=False
        )
        closed = threading.Event()
        context.on("close", lambda: closed.set())
        closed.wait()


def fetch_html(
    url: str,
    profile: Path,
    timeout: float = DEFAULT_TIMEOUT_S,
    sync_playwright_fn: SyncPlaywrightFactory = sync_playwright,
) -> str:
    """Fetch `url`'s rendered HTML through the persistent profile, headlessly.

    `timeout` is a per-URL ceiling in seconds (Playwright's own API wants
    milliseconds - converted here so every caller of this module works in
    the same unit the rest of the project already uses, e.g. render.py's
    own _IMAGE_FETCH_TIMEOUT_S). A navigation failure - a timeout, a dead
    host, a certificate error - is reported as FetchError naming the URL,
    so a caller looping over several pasted URLs (cli.py's own prompt) can
    report one bad URL and continue with the rest, never abort the whole
    packet over it (phase8-urls.md's second risk).
    """
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright_fn() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile), headless=True
        )
        try:
            page = context.new_page()
            try:
                page.goto(url, timeout=timeout * 1000)
            except Exception as error:
                raise FetchError(f"could not fetch {url}: {error}") from error
            return page.content()
        finally:
            context.close()
