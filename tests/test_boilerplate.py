import pytest

from shabbat_print.boilerplate import (
    content_ratio,
    is_boilerplate_line,
    is_definite_chrome_line,
)

PROSE = "Private credit is having a moment, and not entirely a good one."


@pytest.mark.parametrize(
    "line",
    [
        "Unsubscribe",
        "You are receiving this because you signed up.",
        "View this email in your browser",
        "Manage your preferences",
        "© 2026 Bloomberg L.P. All rights reserved.",
        "https://example.com/some/tracking/link",
        "www.example.com",
        "Read more",
        "Sponsored by Acme",
    ],
)
def test_boilerplate_lines(line: str) -> None:
    assert is_boilerplate_line(line) is True


@pytest.mark.parametrize(
    "line",
    [
        PROSE,
        "The Federal Reserve declined to move rates this month, again.",
        "She wrote that the deal was, in her words, entirely unremarkable.",
    ],
)
def test_content_lines(line: str) -> None:
    assert is_boilerplate_line(line) is False


def test_blank_lines_are_not_boilerplate() -> None:
    assert is_boilerplate_line("   ") is False


def test_content_ratio_of_pure_footer_is_zero() -> None:
    footer = "Unsubscribe\nManage your preferences\nhttps://example.com/x\n"
    assert content_ratio(footer) == pytest.approx(0.0)


def test_content_ratio_of_pure_prose_is_one() -> None:
    assert content_ratio(PROSE) == pytest.approx(1.0)


def test_content_ratio_is_by_characters_not_lines() -> None:
    """A long ad page must score low even though it is long."""
    text = PROSE + "\n" + "\n".join(["Sponsored by Acme"] * 30)
    assert content_ratio(text) < 0.15


def test_content_ratio_of_empty_text_is_zero() -> None:
    assert content_ratio("\n \n") == pytest.approx(0.0)


@pytest.mark.parametrize(
    "line",
    [
        "Unsubscribe",
        "View this email in your browser",
        "© 2026 Bloomberg L.P. All rights reserved.",
        "https://example.com/some/tracking/link",
        "www.example.com",
    ],
)
def test_definite_chrome_lines_match_a_known_phrase_or_a_bare_url(
    line: str,
) -> None:
    assert is_definite_chrome_line(line) is True


def test_definite_chrome_line_of_a_blank_line_is_false() -> None:
    assert is_definite_chrome_line("   ") is False


@pytest.mark.parametrize(
    "line",
    [
        "So far, so good",
        "End of an era",
        "Read more",
        "Watch now",
        "The Reframe · By A.R. Moxon • 25 Apr",
    ],
)
def test_definite_chrome_lines_do_not_match_a_short_heading(line: str) -> None:
    """These score as boilerplate under is_boilerplate_line's short-line
    fallback, but that fallback is a weak heuristic, not a confident
    signal - it is exactly the mechanism that let clean.py destroy real
    headlines, subtitles, and mastheads (see I1 in the final review)."""
    assert is_definite_chrome_line(line) is False
