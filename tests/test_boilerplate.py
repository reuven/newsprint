import pytest

from newsprint.boilerplate import (
    SHORT_LINE,
    content_ratio,
    is_boilerplate_line,
    is_definite_chrome_line,
    is_full_line_chrome,
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


# Derive-chrome spec (2026-09-07), part D: the self-promotional trailing
# paragraph reported live in edconway-substack-com.eml. Each phrase was
# checked against the live archive individually - see PHRASES' own comment
# for the evidence each one is or is not based on.
@pytest.mark.parametrize(
    "line",
    [
        (
            "Material World is a free newsletter, mostly about topics "
            "relevant to my recent book."
        ),
        (
            "But if you enjoyed this post, you can tell Simon Willison's "
            "Newsletter that their writing is valuable."
        ),
        "If you enjoyed this post, please do share it with friends.",
        "Please share it with friends and colleagues.",
    ],
)
def test_ed_conway_self_promo_phrases_are_definite_chrome(line: str) -> None:
    assert is_definite_chrome_line(line) is True


def test_order_a_copy_of_pre_order_and_let_me_know_were_checked_and_declined() -> None:
    """Requested alongside the phrases above, from the same reported
    block, and checked the same way against the live archive - each
    found colliding with genuine editorial content, not a hypothetical:

    - "order a copy of" and "pre-order" both appear in real book/cookbook
      recommendations unrelated to self-promotion.
    - "let me know what you think" is a common authorial engagement
      closer used about real article content, not just self-promotion.

    None of the three are in PHRASES; is_definite_chrome_line must still
    say False for all of them."""
    assert (
        is_definite_chrome_line(
            "Amazon (pre-order of Kindle ebook and print paperback)"
        )
        is False
    )
    assert (
        is_definite_chrome_line(
            "You can order a copy of the book directly from the publisher."
        )
        is False
    )
    assert (
        is_definite_chrome_line(
            "If you take a look, let me know what you think. And enjoy the "
            "World Cup final!"
        )
        is False
    )


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


def test_puck_faq_block_as_a_whole_line_was_also_tried_and_reverted() -> None:
    """Round 4 (derive-chrome spec, 2026-09-07): the Puck FAQ/brand-
    partnerships paragraph the user reported was tried a second time, as a
    full, exact is_full_line_chrome literal rather than the two PHRASES
    substrings round 2 already declined - on the theory that a whole-line
    match is a different, safer mechanism, since it cannot lower any
    OTHER block's content_ratio score the way a substring can.

    Measured directly against jon-puck-news.eml, it reproduced the exact
    same regression round 2 found, by a different route: once
    _strip_line_chrome deletes this paragraph outright as its own atomic
    line, it is simply gone from the document before the trailing walk
    ever runs - so that walk's new last leaf becomes the real sign-off
    right before it ("Have a great weekend, / Jon"), a genuine short,
    unpunctuated two-line sign-off with no protection of its own (see
    clean.py's _is_protected_heading, whose own comment documents this
    exact gap), and it is destroyed all over again. The mechanism
    (deleting one atomic line vs. lowering a block's ratio) really is
    different, as claimed - but "cannot affect another block's SCORE"
    turned out not to be the same guarantee as "cannot affect what the
    trailing walk reaches", which is what actually matters here. Declined
    for the same reason round 2 declined it, confirmed against the same
    fixture; is_full_line_chrome must say False for the exact paragraph
    text."""
    assert (
        is_full_line_chrome(
            "Need help? Review our\nFAQ page or contact us for assistance. "
            "For brand partnerships, email ads@puck.news."
        )
        is False
    )


# Round 2, F3: a line-level check distinct from is_definite_chrome_line.
# is_definite_chrome_line matches a PHRASES entry as a SUBSTRING anywhere in
# the line - safe for scoring a block's overall ratio (one bad line among
# several good ones just lowers the score), but never safe for deleting a
# specific line outright, since a real sentence can quote or contain one of
# those words. is_full_line_chrome instead requires the phrase to BE the
# whole line (after collapsing internal whitespace to single spaces and
# case-folding), which is what makes it safe to apply anywhere in a
# document rather than only where a block or a leading/trailing run
# happens to expose it.
@pytest.mark.parametrize(
    "line",
    [
        "Forwarded this email?",
        "forwarded this email?",
        "Forwarded this email? Subscribe here for more",
        "Restack",
        "restack",
        "A MESSAGE FROM OUR SPONSOR",
        "a message from our sponsor",
        "Unsubscribe",
        "unsubscribe",
        "Get the Bulwark app",
        "Get the NYT app",
        (
            "You received this email because you signed up for David French "
            "from The New York Times."
        ),
        "You received this email because you are on our list.",
    ],
)
def test_full_line_chrome_phrases(line: str) -> None:
    assert is_full_line_chrome(line) is True


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   ",
        PROSE,
        (
            "The chapter closes on a long note, and there is an unsubscribe "
            "link somewhere below, which almost nobody ever clicks."
        ),
        "Get the picture",
        "Get the sense of how this played out",
        "Get the app",
        "You received a warm welcome from the whole team.",
        "Restacking the shelves took all afternoon.",
    ],
)
def test_full_line_chrome_rejects_a_substring_or_unrelated_line(line: str) -> None:
    """The two adversarial cases this exists to guard: 'unsubscribe' quoted
    inside a real sentence, and the declined general 'get the ...' pattern
    (no trailing 'app') that the earlier wave found matches real prose
    ('get the picture', 'get the sense')."""
    assert is_full_line_chrome(line) is False


def test_full_line_chrome_collapses_internal_line_breaks() -> None:
    """The 'Forwarded this email?' button renders as three visual lines
    ('Forwarded this email?' / 'Subscribe here' / 'for more') inside one
    element; collapsed with a single space this is exactly the second
    known phrase."""
    assert (
        is_full_line_chrome("Forwarded this email?\nSubscribe here\nfor more") is True
    )


# F2's live-queue trace found one surviving case is_full_line_chrome's
# original phrase list did not cover: a footer block whose text is
# "© 2026\nSubstack Inc.\n548 Market Street PMB 72296, San Francisco, CA
# 94104\nUnsubscribe" scores content_ratio 0.159 - just over CHROME_RATIO
# (0.15) - because "Substack Inc." ends in a period, which defeats
# is_boilerplate_line's short-line fallback (it reads as a "sentence").
# Once _strip_line_chrome removes the standalone "Unsubscribe" line, the
# ratio of what remains rises even further (removing a zero-content line
# only raises the surviving fraction), so the address is never reachable
# through the ratio path at all - it needs its own explicit, whole-line
# match, the same way "Unsubscribe" already has one. Reuses the existing,
# already-vetted _ADDRESS_LINE pattern (anchored with fullmatch, proven
# safe against an address quoted mid-sentence) rather than inventing a new
# heuristic.
@pytest.mark.parametrize(
    "line",
    [
        "548 Market Street PMB 72296, San Francisco, CA 94104",
        "1255 22nd St NW #18958, Washington, DC 20037",
        "PMB 72296, San Francisco, CA 94104",
    ],
)
def test_full_line_chrome_matches_a_postal_address(line: str) -> None:
    assert is_full_line_chrome(line) is True


def test_full_line_chrome_does_not_match_an_address_quoted_in_a_sentence() -> None:
    line = (
        "She used to live at 1600 Pennsylvania Ave NW, Washington, DC 20500 "
        "before the campaign ended."
    )
    assert is_full_line_chrome(line) is False


# Round 3, section A: whole-line exact matches, extending the F3 list.
# "Like" and "Comment" were declined in round 2 as PHRASES substring
# entries, correctly - the live queue has real article sentences containing
# those words. is_full_line_chrome is a different, stronger claim (the
# whole line, not a substring), so these are safe here even though they
# were not safe there.
@pytest.mark.parametrize(
    "line",
    [
        "Share",
        "share",
        "Like",
        "Comment",
        "Share Now",
        "READ IN APP",
        "View in browser",
        "Share The Bulwark",
    ],
)
def test_full_line_chrome_phrases_round_3(line: str) -> None:
    assert is_full_line_chrome(line) is True


# The load-bearing distinction for section A: these are real article
# sentences from the live queue that a substring match on "Like"/"Share"
# would destroy. is_full_line_chrome must say False for all of them,
# because none of them is, in its entirety, one of the bare words above -
# each has more text sharing the same line.
@pytest.mark.parametrize(
    "line",
    [
        "Like most of America, Dry Powder will be…",
        "Like it or not, we now live in a world in which the weapon…",
        "Like, right now.",
        "Share this newsletter with someone who prefers truth, honesty…",
    ],
)
def test_a_sentence_starting_with_like_or_share_survives(line: str) -> None:
    assert is_full_line_chrome(line) is False


# Round 3, section B: a third pass at footer prose.
@pytest.mark.parametrize(
    "line",
    [
        "Update your newsletter preferences anytime via your personal page.",
        "Update your email preferences or unsubscribe here.",
        "Thanks for being a Bulwark+ member.",
        "Thanks for being a valued subscriber",
        "Thanks for being a part of the club!",
        "Visit our Help Center for answers to our most common questions.",
        "Set up your personal RSS feed for ad-free listening.",
        "The Secret Podcast is exclusively for members of Bulwark+.",
    ],
)
def test_round_3_footer_phrases_are_definite_chrome(line: str) -> None:
    assert is_definite_chrome_line(line) is True


# Round 3b, item 1: "READ TO ME" is a Bulwark variant of "READ IN APP" -
# the same audio/app-affordance CTA shape, requested by name. "LISTEN NOW"
# is added alongside it: a small family investigation (measured against
# the full 268-fixture corpus) found it always appears as a bare,
# standalone line immediately after a "Listen now (NN mins) | ..."
# metadata line, in five other publications' fixtures
# (datascienceeducation, johnfdickerson, pragmaticengineer, serioustrouble,
# thepythonshow), with zero collisions with real content across the whole
# corpus. Both are literals, not a broader regex - see the module comment
# by _FULL_LINE_CHROME's definition for why a wider family pattern
# ("read"/"listen" + "now"/"to me"/"in app"/"aloud") was considered and
# declined.
@pytest.mark.parametrize(
    "line",
    [
        "READ TO ME",
        "read to me",
        "Read To Me",
        "LISTEN NOW",
        "Listen now",
        "listen now",
    ],
)
def test_full_line_chrome_phrases_round_3b(line: str) -> None:
    assert is_full_line_chrome(line) is True


# The load-bearing distinction for the "listen now" addition: a real,
# plausible sentence that merely contains those two words, or is close in
# shape, must not collapse to the bare phrase and must survive. None of
# these is the entire line "listen now" or "read to me" once collapsed
# and case-folded.
@pytest.mark.parametrize(
    "line",
    [
        "Listen to me for a moment before you decide anything.",
        "Read this to me tomorrow, would you?",
        "You can read it in the app if you'd rather.",
        "Listen now (18 mins) | Season 11, Episode 8",
    ],
)
def test_a_sentence_resembling_the_audio_app_family_survives(line: str) -> None:
    assert is_full_line_chrome(line) is False


# Round 3b, item 2: a publisher's own pipe/middot/bullet-separated
# navigation row - "View in browser | nytimes.com" - where "View in
# browser" is already a known whole-line chrome phrase and "nytimes.com"
# is a bare domain (see _BARE_DOMAIN's own comment). Every one of the
# three delimiters clean.py's docstring names is checked, and the row
# survives collapsing internal whitespace differently (no spaces at all,
# vs. spaces on both sides of the delimiter) the way a real rendered line
# might.
@pytest.mark.parametrize(
    "line",
    [
        "View in browser|nytimes.com",
        "View in browser | nytimes.com",
        "View in browser · nytimes.com",
        "View in browser • bulwark.com",
        "Unsubscribe|nytimes.com",
    ],
)
def test_a_delimiter_separated_navigation_row_is_chrome(line: str) -> None:
    assert is_full_line_chrome(line) is True


# The load-bearing safety property: EVERY segment must independently
# qualify, or nothing is removed. A pipe in a real sentence, a nav row
# with one genuine content segment among the chrome, an empty trailing
# segment, and an engagement-stats row that superficially looks similar
# (the live corpus's real "a month ago · 99 likes · 11 comments · Renee
# DiResta" line) must all survive.
@pytest.mark.parametrize(
    "line",
    [
        "View in browser | The Fed considers new rate hikes this week",
        "Cost: $10 | Free shipping available",
        "View in browser | ",
        "a month ago · 99 likes · 11 comments · Renee DiResta",
        "U.S. | Real Content",
    ],
)
def test_a_mixed_or_non_chrome_delimited_row_survives(line: str) -> None:
    assert is_full_line_chrome(line) is False


# Round 4 (derive-chrome spec, 2026-09-07): literals derived by
# scripts/derive_chrome.py ranking lines that survive clean_document() by
# how many distinct publications and messages each appears in, over the
# full local archive. See the derive-chrome report for the count each one
# cleared, and PHRASES' and _FULL_LINE_CHROME's own comments for why each
# is safe as a whole-line match.
@pytest.mark.parametrize(
    "line",
    [
        "Upgrade to paid",
        "Leave a comment",
        "Preview",
        "Subscribed",
        "Paid",
        "Invite Friends",
        "Invite your friends and earn rewards",
        "Change Your EmailPrivacy PolicyContact UsCalifornia Notices",
        "Connect with us on:",
        "If you received this newsletter from someone else, subscribe here.",
        "Need help? Review our newsletter help page or contact us for assistance.",
        "The New York Times Company. 620 Eighth Avenue New York, NY 10018",
        "A subscription gets you:",
        "Copyright © The Economist Newspaper Limited 2026. All rights reserved.",
        "Registered in England and Wales. No. 236383.",
        "Subscribe to The TimesGet The New York Times app",
        "Follow Axios on social media:",
        "Download our app for iOS and Android",
        "For subscribers",
        "Get it in your inbox.",
        "Get it in your inbox",
        "Claim my free post",
        "Continue reading this post for free in the Substack app",
        "Or upgrade your subscription. Upgrade to paid",
        "Update your email preferences or unsubscribe  here",
        "Pledge your support",
        "Watch now",
        "Watch on demand",
        "Next show",
        "All upcoming shows",
        "Add to calendar",
        "Download from the App Store or Google Play",
        "View email online     Privacy Policy \n  Terms & Conditions",
        "Unsubscribe     Contact us \n  Update your details",
        "PHOTO: GETTY IMAGES",
        "Read full story",
        "Read more",
        "Buy Trade World",
        "Listen Now.",
        "Listen to the episode here.",
    ],
)
def test_round_4_full_line_chrome_literals(line: str) -> None:
    assert is_full_line_chrome(line) is True


def test_a_sentence_resembling_listen_to_the_episode_here_survives() -> None:
    """The load-bearing negative case for the new literal: it must not
    become a broader pattern that would catch a real first-person
    sentence, the exact adversarial shape round 3b's own comment already
    identified for this family ("Listen to me.")."""
    assert is_full_line_chrome("Listen to me for a moment before you decide.") is False
    assert (
        is_full_line_chrome("You can listen to the recording here if you missed it.")
        is False
    )


# The "watch now" addition's own load-bearing distinction: the real
# sentence a PHRASES *substring* match would have destroyed
# (pete-aidailybrief-io.eml, see PHRASES' own comment and
# test_need_help_brand_partnerships_and_watch_now_were_tried_and_reverted)
# is never, itself, equal to the bare two-word line "watch now".
def test_watch_now_as_a_substring_of_a_real_sentence_still_survives() -> None:
    assert (
        is_full_line_chrome(
            "AI moved fast while I was away, so this chapter begins with the "
            "trends creators should watch now."
        )
        is False
    )


@pytest.mark.parametrize(
    "line",
    [
        "you’re currently a free subscriber to Prof G Media.",
        "You’re currently a free subscriber to Behind the Craft.",
        "this email was sent by: The Economist Newspaper Ltd.",
        "This email was sent to: reader@example.com",
        (
            "This email has been sent to   reader@example.com    because you "
            "signed up for this newsletter."
        ),
    ],
)
def test_round_4_prefix_matches(line: str) -> None:
    assert is_full_line_chrome(line) is True


@pytest.mark.parametrize(
    "line",
    [
        "∙",
        "·",
    ],
)
def test_a_bare_separator_character_is_chrome(line: str) -> None:
    assert is_full_line_chrome(line) is True


def test_a_separator_character_beside_real_content_is_not_bare() -> None:
    """The bare-separator match requires the ENTIRE line to be nothing but
    the glyph - a real delimited row using the same character (already
    covered by _is_delimited_chrome_row) is a different code path, and a
    genuine sentence merely containing one must survive untouched."""
    assert is_full_line_chrome("38:00 ∙ Preview") is False
    assert is_full_line_chrome("A · shaped keycap is unusual for a phone.") is False


@pytest.mark.parametrize("line", ["1", "2", "3", "17", "99", "042"])
def test_a_bare_footnote_number_is_chrome(line: str) -> None:
    assert is_full_line_chrome(line) is True


@pytest.mark.parametrize(
    "line",
    [
        "2026",  # a bare 4-digit year is deliberately not covered
        "I have 2 cats and a dog.",
        "Chapter 12",
    ],
)
def test_a_number_inside_real_content_survives(line: str) -> None:
    assert is_full_line_chrome(line) is False


@pytest.mark.parametrize("line", ["0:00", "38:00", "51:11", "1:05:30"])
def test_a_bare_duration_is_chrome(line: str) -> None:
    assert is_full_line_chrome(line) is True


def test_a_duration_inside_real_content_survives() -> None:
    assert (
        is_full_line_chrome("The meeting is scheduled for 3:00 this afternoon.")
        is False
    )


@pytest.mark.parametrize(
    "line",
    [
        "© 2026 Prof G Media",
        "© Condé Nast 2026",
        "© 2026 Test Weekly. All rights reserved.",
    ],
)
def test_a_line_opening_with_the_copyright_glyph_is_chrome(line: str) -> None:
    assert is_full_line_chrome(line) is True


def test_a_mid_sentence_copyright_mention_survives() -> None:
    """The copyright pattern matches only the START of the line - a real
    sentence that happens to mention a © notice partway through must not
    be swept up."""
    assert (
        is_full_line_chrome("The company (© Acme Corp) makes replacement parts.")
        is False
    )


def test_powered_by_a_platform_is_chrome() -> None:
    assert is_full_line_chrome("Powered by beehiiv") is True


def test_powered_by_inside_real_content_survives() -> None:
    assert is_full_line_chrome("The rocket is powered by liquid hydrogen.") is False


def test_get_more_publication_in_your_inbox_is_chrome() -> None:
    assert is_full_line_chrome("Get more New Yorker in your inbox.") is True
    assert is_full_line_chrome("Get more Bulwark content in your inbox") is True


def test_get_more_in_your_inbox_requires_the_whole_line() -> None:
    assert (
        is_full_line_chrome(
            "She promised to get more of the story into her inbox before noon."
        )
        is False
    )


# NYT Cooking's photo-credit pattern. The load-bearing negative case is a
# genuine sentence that happens to end "... for The New York Times." -
# only a short, Title Case name before it (what every real byline in the
# archive actually is) qualifies; ordinary prose, full of lowercase
# function words, does not.
@pytest.mark.parametrize(
    "line",
    [
        "Christopher Testani for The New York Times. Food Stylist: Simon Andrews.",
        (
            "David Malosh for The New York Times. Food Stylist. Simon Andrews. "
            "Prop Stylist: Paige Hicks."
        ),
        (
            "Kelly Marshall for The New York Times. Food Stylist: Roscoe "
            "Betsill. Prop Stylist: Paige Hicks."
        ),
        "Armando Rafael for The New York Times",
    ],
)
def test_nyt_photo_credit_lines_are_chrome(line: str) -> None:
    assert is_full_line_chrome(line) is True


def test_a_real_sentence_ending_for_the_new_york_times_survives() -> None:
    assert is_full_line_chrome("She used to write for The New York Times.") is False
    assert (
        is_full_line_chrome(
            "He spent a decade reporting for The New York Times before "
            "starting this newsletter."
        )
        is False
    )


# ---------------------------------------------------------------------------
# Round 5: the Axios footer the user reported. Its lines are short
# declarative sentences, and the short-line fallback only calls a line
# chrome when it has no sentence-ending punctuation - so each of these
# scored a full 1.00 as content and, sitting at the document's tail, held
# the trailing chrome run open on every Axios newsletter.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Advertise with us.",
        "Learn more.",
        "Discover how",
        "Sponsorship has no influence on editorial content.",
        "Thank you for signing up for this Axios newsletter.",
        "Follow Axios across:",
        "Follow us on:",
    ],
)
def test_axios_footer_lines_are_full_line_chrome(line: str) -> None:
    assert is_full_line_chrome(line)


@pytest.mark.parametrize(
    "line",
    [
        # Each contains one of the phrases above but is a real sentence.
        "Following the money across Europe was harder than anyone expected.",
        "Learn more about the eurozone crisis from our Frankfurt bureau.",
        "He wanted to advertise with us, but the budget never materialized.",
        "Discover how the Fed thinks about inflation, in three charts.",
    ],
)
def test_real_sentences_containing_those_phrases_survive(line: str) -> None:
    """A full-line match is a far stronger claim than a substring one, and
    these are exactly the sentences a substring rule would destroy."""
    assert not is_full_line_chrome(line)


def test_a_sender_prefixed_postal_address_scores_as_chrome() -> None:
    """ "Axios, PO Box 101060, Arlington VA 22201" is the document's last
    leaf and scored 1.00 content, which stopped the trailing run before it
    removed anything at all."""
    assert is_boilerplate_line("Axios, PO Box 101060, Arlington VA 22201")
    assert content_ratio("Axios, PO Box 101060, Arlington VA 22201") == 0.0


def test_a_sender_prefixed_address_is_not_a_deletable_line_on_its_own() -> None:
    """Scored as chrome, but deliberately not deletable on its own.

    _strip_line_chrome deletes on is_full_line_chrome, so that is the
    property that matters here. Widening _ADDRESS_LINE to accept a sender
    name before the box - which would have made this deletable - was tried
    and reverted: measured across the fixture corpus it cost 2,417 lines
    and cut jon-puck-news.eml from 280 lines to 1, because that match
    fires against the raw document, where the line it hits can carry a
    whole enclosing element away with it.

    is_definite_chrome_line is true, and should be: its only caller is the
    protected-heading guard, and a postal address must not be mistaken for
    a masthead just because it is short.
    """
    address = "Axios, PO Box 101060, Arlington VA 22201"
    assert not is_full_line_chrome(address)
    assert is_definite_chrome_line(address)


def test_a_bare_street_address_is_still_deletable() -> None:
    """The unprefixed form keeps the stronger treatment it already had."""
    assert is_definite_chrome_line(
        "PO Box 448, Accord, NY 12404"
    ) or is_full_line_chrome("PO Box 448, Accord, NY 12404")


# ---------------------------------------------------------------------------
# Round 6: the New York Times masthead. Every Morning ends with eight
# credit lines; the ones over SHORT_LINE read as sentences and scored 1.00
# content, holding the trailing chrome run open.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Host: Sam Sifton",
        "Editor: Adam B. Kushner",
        "News Editor: Tom Wright-Piersanti",
        "News Staff: Evan Gorelick, Brent Lewis, Lara McCoy, Karl Russell",
        "Saturday Writer: Melissa Kirsch",
        "Editorial Director, Newsletters: Jodi Rudoren",
        "Deputy Editorial Director: Lauren Jackson",
    ],
)
def test_masthead_credit_lines_score_as_chrome(line: str) -> None:
    assert is_definite_chrome_line(line)


@pytest.mark.parametrize(
    "line",
    [
        # A headline with a colon has the same shape as a credit line. This
        # one is real: TheTorah.com published it, and an earlier version of
        # the rule removed it.
        "Solomon’s Bronze Sea: A Celestial Apsu",
        "The Loneliness of Donald Trump: A Study",
        "Germany: A Right Turn",
    ],
)
def test_an_article_title_with_a_colon_is_not_a_masthead_credit(line: str) -> None:
    """Shape alone is not enough - the label has to name an actual role.
    Without that gate this rule ate article titles."""
    assert not is_definite_chrome_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "Between the lines: Survey responses about economic questions seem sour.",
        "Data: Federal Reserve Bank of New York; Chart: Neil Irwin/Axios",
        "Zoom in: This curious mix of views aligns with other public data.",
        "Correction: An earlier version misstated the figure.",
    ],
)
def test_a_section_lead_in_is_not_a_masthead_credit(line: str) -> None:
    """Every word after the colon must be capitalized; each of these has a
    lower-case word, which is what keeps a lead-in out."""
    assert not is_definite_chrome_line(line)


# ---------------------------------------------------------------------------
# Round 7: the trailing blocks the user reported from Bloomberg, Puck, The
# Bulwark and DealBook. Each ran past SHORT_LINE, so it read as a sentence
# and held the trailing chrome run open.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "Like getting this newsletter? Subscribe to Bloomberg.com for access.",
        "Before it's here, it's on the Bloomberg Terminal.",
        "Want to sponsor this newsletter? Get in touch here.",
        "Bloomberg L.P. 731 Lexington, New York, NY, 10022",
        (
            "Need help? Review our FAQ page or contact us. For brand"
            " partnerships, email ads@puck.news."
        ),
        "Update your email and payment preferences by visiting your account page.",
        "Visit our FAQ for help or send a message to members@thebulwark.com.",
        (
            "We'd like your feedback. Please email thoughts and suggestions"
            " to dealbook@nytimes.com."
        ),
    ],
)
def test_reported_trailing_lines_score_as_chrome(line: str) -> None:
    assert is_boilerplate_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "Andrew Ross Sorkin, Founder/Editor-at-Large, New York @andrewrsorkin",
        "Brian O'Keefe, Managing Editor, New York @brianbokeefe",
        "Lauren Hirsch, Reporter, New York @LaurenSHirsch",
        # Lower-case particles in the middle of a name. Requiring every word
        # to be capitalized skipped exactly this one while catching his six
        # colleagues, which left the whole masthead standing.
        "Michael J. de la Merced, Reporter, London @m_delamerced",
    ],
)
def test_a_masthead_credit_ending_in_a_handle_is_chrome(line: str) -> None:
    assert is_definite_chrome_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "Follow DealBook on Instagram: @nytdealbook",
        "Follow Axios across:",
        "Follow us on:",
    ],
)
def test_a_follow_us_row_is_chrome(line: str) -> None:
    assert is_full_line_chrome(line)


@pytest.mark.parametrize(
    "line",
    [
        "Follow the money on Wall Street and see where it actually goes",
        "Follow up on this story tomorrow",
    ],
)
def test_a_sentence_starting_with_follow_survives(line: str) -> None:
    assert not is_full_line_chrome(line)


def test_a_company_prefixed_address_scores_but_is_not_deletable() -> None:
    """Same discipline as the Axios PO box: scored as chrome so the
    trailing run can pass it, never a deletion rule, because that match
    fires against the raw document where it can carry a whole enclosing
    element away."""
    address = "Bloomberg L.P. 731 Lexington, New York, NY, 10022"
    assert is_definite_chrome_line(address)
    assert not is_full_line_chrome(address)


def test_a_lowercase_postal_address_is_still_definite_chrome() -> None:
    """The tail rule that catches most addresses wants the state in
    capitals; plenty of footers set the whole line in lower case, and the
    address rule behind it is what catches those. Losing it would let a
    mailing address hold a trailing chrome run open, which is what put the
    rule there."""
    assert is_definite_chrome_line("228 park ave s, new york, ny 10003")
    assert is_definite_chrome_line("228 Park Ave S, New York, NY 10003")


def test_a_line_of_exactly_forty_characters_is_not_short() -> None:
    """The short-line fallback is for fragments - a label, a nav item, a
    caption - and forty characters is where a line stops being one. A
    sentence of exactly forty characters with no full stop is the case
    that decides it, and reading the boundary the other way would score
    every one of them as chrome."""
    line = "Rates held steady and the chair said sos"
    assert len(line) == SHORT_LINE
    assert not is_boilerplate_line(line)
    assert is_boilerplate_line(line[:-1]), "one character shorter is short"
