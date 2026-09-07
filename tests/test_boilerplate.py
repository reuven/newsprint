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
    signal - it is exactly the mechanism that once let clean.py destroy
    real headlines, subtitles, and mastheads that happened to be short.

    "Watch now" was requested as a phrase (it survives as a bare CTA
    button in the user's real output), tried, and reverted: see
    test_watch_now_was_tried_and_reverted for the measured counter-
    example that ruled it out."""
    assert is_definite_chrome_line(line) is False


# G1: real footer prose observed in the user's own printed packet. Modern
# footers read as conversational sentences ending in periods, which defeats
# is_boilerplate_line's short-line fallback entirely - these must be caught
# by phrase or shape, not length.
@pytest.mark.parametrize(
    "line",
    [
        "Forwarded this email? Subscribe here for more",
        "Share / Like / Comment / Restack",
        "A MESSAGE FROM OUR SPONSOR",
        "Puck is published by Heat Media LLC.",
        "Update your newsletter preferences anytime via your personal page.",
        "Review our FAQ page or contact us for assistance.",
        "Give the gift of a subscription this year.",
        "Check out my masterclass on negotiation.",
        "You received this email because you signed up on our website.",
        "To stop receiving this newsletter, click here.",
        "Manage all your email preferences from your account page.",
        "Read in app",
    ],
)
def test_new_footer_phrases_are_definite_chrome(line: str) -> None:
    assert is_definite_chrome_line(line) is True


@pytest.mark.parametrize(
    "line",
    [
        "1255 22nd St NW #18958, Washington, DC 20037",
        "548 Market Street PMB 72296, San Francisco, CA 94104",
        "107 Greenwich St., New York, NY 10006",
        "PMB 72296, San Francisco, CA 94104",
        "Suite 200, Chicago, IL 60601",
    ],
)
def test_postal_addresses_are_definite_chrome(line: str) -> None:
    assert is_definite_chrome_line(line) is True


def test_an_address_quoted_inside_a_real_sentence_is_not_chrome() -> None:
    """The adversarial false-positive case: a genuine sentence that happens
    to quote a full postal address is not, itself, chrome. The address
    pattern is anchored to the start of the line so this must not match."""
    line = (
        "She used to live at 1600 Pennsylvania Ave NW, Washington, DC 20500 "
        "before the campaign ended."
    )
    assert is_definite_chrome_line(line) is False


def test_need_help_brand_partnerships_and_watch_now_were_tried_and_reverted() -> None:
    """All three were requested (need help?/brand partnerships from the
    spec's own suggestions and a second read of the user's real output;
    watch now from the same second read), added, and measured against all
    102 fixtures. All three were reverted on concrete evidence, not a
    hypothetical:

    - "watch now" removed a genuine editorial sentence from
      pete-aidailybrief-io.eml: "AI moved fast while I was away; and this
      new chapter of AI Daily Brief begins with the trends, tools, and
      opportunities creators should watch now." A phrase match, unlike
      the short-line fallback, does not care about length or position.

    - "need help?" and "brand partnerships" together made a real chrome
      block newly chrome in jon-puck-news.eml - Puck's FAQ/brand-
      partnerships footer paragraph - which had been the trailing run's
      stopping point. With it reclassified, the walk continued one leaf
      further back and removed a genuine two-line sign-off: "Have a
      great weekend, / Jon". See the report for the full trace.

    None of the three are in PHRASES; is_definite_chrome_line must still
    say False for all of them."""
    assert is_definite_chrome_line("Need help? Here is what to do next.") is False
    assert (
        is_definite_chrome_line(
            "Brand partnerships now account for a third of the site's revenue."
        )
        is False
    )
    assert (
        is_definite_chrome_line(
            "AI moved fast while I was away, so this chapter begins with the "
            "trends creators should watch now."
        )
        is False
    )
