"""shabbat_print.browser: the app-owned, persistent Chromium profile.

No test here launches a real browser or touches the network - every call
is driven through a fake standing in for playwright.sync_api.sync_playwright
itself, the same "pass anything with the right shape" contract
printer.spool's `runner` and mail.Mailbox's `imap_factory` already use.
"""

from pathlib import Path

import pytest

from shabbat_print.browser import FetchError, fetch_html, login, profile_dir


class _FakePage:
    def __init__(self, html: str = "<html><body>ok</body></html>") -> None:
        self.html = html
        self.goto_calls: list[tuple[str, float]] = []
        self.closed = False

    def goto(self, url: str, timeout: float) -> None:
        self.goto_calls.append((url, timeout))

    def content(self) -> str:
        return self.html

    def close(self) -> None:
        self.closed = True


class _ExplodingPage(_FakePage):
    def goto(self, url: str, timeout: float) -> None:
        raise TimeoutError(f"Timeout {timeout}ms exceeded navigating to {url}")


class _FakeContext:
    def __init__(self, page: _FakePage | None = None) -> None:
        self._page = page or _FakePage()
        self.closed = False
        self._handlers: dict[str, list] = {}

    def new_page(self) -> _FakePage:
        return self._page

    def on(self, event: str, handler) -> None:
        # Fires the handler immediately - a fake browser window that is
        # already closed by the time login() starts waiting for it, so no
        # test ever actually blocks on a real close event.
        self._handlers.setdefault(event, []).append(handler)
        handler()

    def close(self) -> None:
        self.closed = True


class _FakeBrowserType:
    def __init__(self, context: _FakeContext) -> None:
        self.context = context
        self.launch_calls: list[tuple[str, bool]] = []

    def launch_persistent_context(self, user_data_dir: str, *, headless: bool):
        self.launch_calls.append((user_data_dir, headless))
        return self.context


class _FakePlaywright:
    def __init__(self, context: _FakeContext) -> None:
        self.chromium = _FakeBrowserType(context)


class _FakePlaywrightCM:
    def __init__(self, context: _FakeContext) -> None:
        self.playwright = _FakePlaywright(context)
        self.stopped = False

    def __enter__(self):
        return self.playwright

    def __exit__(self, *exc: object) -> bool:
        self.stopped = True
        return False


def _factory(context: _FakeContext):
    cm = _FakePlaywrightCM(context)

    def sync_playwright_fn():
        return cm

    sync_playwright_fn.cm = cm  # type: ignore[attr-defined]
    return sync_playwright_fn


def test_profile_dir_lives_under_the_config_directory() -> None:
    config_dir = Path("/home/user/.config/shabbat-print")
    assert profile_dir(config_dir) == config_dir / "browser-profile"


def test_login_launches_visibly_and_returns_once_the_window_closes(
    tmp_path: Path,
) -> None:
    context = _FakeContext()
    factory = _factory(context)

    login(tmp_path / "profile", sync_playwright_fn=factory)

    launch_calls = factory.cm.playwright.chromium.launch_calls
    assert launch_calls == [(str(tmp_path / "profile"), False)]


def test_login_creates_the_profile_directory_if_missing(tmp_path: Path) -> None:
    context = _FakeContext()
    factory = _factory(context)
    target = tmp_path / "does" / "not" / "exist"

    login(target, sync_playwright_fn=factory)

    assert target.is_dir()


def test_fetch_html_launches_headlessly(tmp_path: Path) -> None:
    context = _FakeContext(_FakePage("<html><body>hello</body></html>"))
    factory = _factory(context)

    html = fetch_html(
        "https://example.com/a", tmp_path / "profile", 5.0, sync_playwright_fn=factory
    )

    assert html == "<html><body>hello</body></html>"
    assert factory.cm.playwright.chromium.launch_calls == [
        (str(tmp_path / "profile"), True)
    ]


def test_fetch_html_passes_the_timeout_in_milliseconds(tmp_path: Path) -> None:
    page = _FakePage()
    context = _FakeContext(page)
    factory = _factory(context)

    fetch_html(
        "https://example.com/a", tmp_path / "profile", 12.0, sync_playwright_fn=factory
    )

    assert page.goto_calls == [("https://example.com/a", 12000.0)]


def test_fetch_html_closes_the_context_after_a_successful_fetch(
    tmp_path: Path,
) -> None:
    context = _FakeContext()
    factory = _factory(context)

    fetch_html(
        "https://example.com/a", tmp_path / "profile", 5.0, sync_playwright_fn=factory
    )

    assert context.closed is True


def test_fetch_html_raises_fetch_error_on_a_navigation_failure(
    tmp_path: Path,
) -> None:
    context = _FakeContext(_ExplodingPage())
    factory = _factory(context)

    with pytest.raises(FetchError, match="example.com/a"):
        fetch_html(
            "https://example.com/a",
            tmp_path / "profile",
            5.0,
            sync_playwright_fn=factory,
        )


def test_fetch_html_closes_the_context_even_after_a_navigation_failure(
    tmp_path: Path,
) -> None:
    context = _FakeContext(_ExplodingPage())
    factory = _factory(context)

    with pytest.raises(FetchError):
        fetch_html(
            "https://example.com/a",
            tmp_path / "profile",
            5.0,
            sync_playwright_fn=factory,
        )

    assert context.closed is True


def test_fetch_html_creates_the_profile_directory_if_missing(tmp_path: Path) -> None:
    context = _FakeContext()
    factory = _factory(context)
    target = tmp_path / "does" / "not" / "exist"

    fetch_html("https://example.com/a", target, 5.0, sync_playwright_fn=factory)

    assert target.is_dir()
