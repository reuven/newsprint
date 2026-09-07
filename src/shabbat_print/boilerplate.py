"""Deciding what is newsletter chrome and what is newsletter content.

The rule is about what the text *is*, never how much of it there is: 1,200
characters of pure link roundup is chrome, and 200 characters of a real
closing paragraph is content.
"""

import re

PHRASES: tuple[str, ...] = (
    "unsubscribe",
    "manage your preferences",
    "manage preferences",
    "email preferences",
    "update your profile",
    "view in browser",
    "view this email",
    "you are receiving this",
    "you're receiving this",
    "you received this",
    "was this forwarded",
    "forwarded this email",
    "forward to a friend",
    "add us to your address book",
    "mailing address",
    "all rights reserved",
    "privacy policy",
    "terms of service",
    "sponsored by",
    "advertisement",
    "presented by",
    "follow us",
    "share this",
    "thanks for reading",
    "see you next week",
    "until next time",
    "©",
    # G1: added from real footer prose in the user's own printed packet.
    # Modern footers read as conversational sentences ending in periods,
    # which defeats the short-line fallback below entirely - these are
    # caught by phrase instead. "get the bulwark app" was left out as too
    # specific to one publication, and no safe general "get the ... app"
    # pattern was found that would not also match ordinary prose ("get the
    # sense", "get the picture"); the trailing-run removal in clean.py is
    # the safety net for that one gap.
    "subscribe here",
    "like comment restack",
    "restack",
    "a message from our sponsor",
    "is published by",
    "newsletter preferences",
    "review our faq",
    "give the gift of",
    "check out my masterclass",
    # Requested after a second read of the user's real printed output.
    # "need help?", "brand partnerships", and "watch now" were also
    # requested but are deliberately NOT here - all three were tried and
    # measured against the full 102-fixture corpus, and reverted on
    # concrete evidence of real content loss, not a hypothetical:
    #
    # - "watch now" removed a genuine editorial sentence in
    #   pete-aidailybrief-io.eml: "AI moved fast while I was away; and
    #   this new chapter of AI Daily Brief begins with the trends, tools,
    #   and opportunities creators should watch now." A phrase match,
    #   unlike the short-line fallback, does not care about length or
    #   position, so it strikes a real sentence that happens to end in
    #   those two words exactly as readily as a bare CTA button.
    #
    # - "need help?" and "brand partnerships" are individually narrow, but
    #   together they made a real chrome block (Puck's FAQ/brand-
    #   partnerships footer paragraph) newly chrome in jon-puck-news.eml -
    #   and that paragraph had been the trailing run's stopping point,
    #   protecting everything before it. With it reclassified as chrome,
    #   the walk continued one leaf further back and removed a genuine,
    #   two-line authorial sign-off: "Have a great weekend, / Jon". That
    #   sign-off was never independently protected - the heading guard's
    #   single-line exception only covers a block whose *entire* text is
    #   one line, and this one is two ("Have a great weekend," then
    #   "Jon"), each short and unpunctuated enough to score as chrome on
    #   its own. This is a real, structural gap - short multi-line
    #   sign-offs are not protected the way a single-line subtitle is -
    #   worth its own careful look in a future change; it is not fixed
    #   here, and these three phrases are the reason it surfaced at all.
    #
    # See the report for the full trace of both findings.
    "you received this email because",
    "to stop receiving",
    "manage all your email preferences",
    "read in app",
    # Round 3, section B: a third pass at footer prose, from a real printed
    # packet. "thanks for being a bulwark+ member" was requested by name,
    # but the fixture corpus shows the shape generalizes cleanly across
    # publications with no collision found: "Thanks for being a valued
    # subscriber" (pete-aidailybrief-io.eml) and "Thanks for being a part
    # of the club!" (serioustrouble-substack-com.eml) are both the same
    # kind of footer sign-off, immediately followed by Like/Comment/Restack
    # or a Restack button in both cases - so the general "thanks for being
    # a" is used instead of the publication-specific phrase.
    "thanks for being a",
    # "update your newsletter preferences" and "update your email
    # preferences" are subsumed by the pre-existing "newsletter
    # preferences" and "email preferences" entries above - any line
    # containing the longer phrase already contains the shorter one, so
    # these change nothing in this corpus. Kept anyway since they were
    # explicitly requested and are harmless, same treatment G1 gave
    # "you received this email because" / "manage all your email
    # preferences".
    "update your newsletter preferences",
    "update your email preferences",
    "visit our help center",
    "set up your personal rss feed",
    "is exclusively for members of",
)

_URL_ONLY = re.compile(r"^(https?://\S+|www\.\S+)$", re.IGNORECASE)
_SENTENCE_END = (".", "!", "?", '"', "'", ")", ":", "”", "’")

# A line that IS a postal address - a street address, or a PMB/Suite/Apt/#
# box - ending in a two-letter state and five-digit ZIP. Anchored to the
# start of the line (via fullmatch) deliberately: without that anchor this
# would also match a genuine sentence that merely quotes an address, since
# real addresses often appear mid-sentence in real prose.
_ADDRESS_LINE = re.compile(
    r"(?:\d+\s+\S.*|(?:PMB|Suite|Ste\.?|Apt\.?|#)\s*\S.*)"
    r",\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\.?",
    re.IGNORECASE,
)

# Below this, a line without sentence-ending punctuation reads as a
# link-roundup item or a navigation label rather than as prose.
SHORT_LINE = 40


def is_definite_chrome_line(line: str) -> bool:
    """True only for a confident signal: a known phrase, a bare URL, or a
    postal address.

    Unlike is_boilerplate_line, this leaves out the short-line fallback -
    "under 40 characters without sentence punctuation" - which is a weak
    heuristic that also matches a bare headline, subtitle, masthead, or
    dateline. Callers that need to tell a block of *definite* chrome apart
    from something merely short (clean.py's heading guard, in particular)
    should use this instead of is_boilerplate_line.
    """
    stripped = line.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if any(phrase in lowered for phrase in PHRASES):
        return True
    if _URL_ONLY.match(stripped):
        return True
    return bool(_ADDRESS_LINE.fullmatch(stripped))


def is_boilerplate_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if is_definite_chrome_line(stripped):
        return True
    return len(stripped) < SHORT_LINE and not stripped.endswith(_SENTENCE_END)


# Round 2, F3: phrases that are unambiguously chrome only when they
# constitute an ENTIRE line - as opposed to PHRASES above, which is matched
# as a substring anywhere in a line. Substring matching is safe only for
# the ratio-based scoring content_ratio does (one bad line among several
# good ones just lowers a block's score, never deletes anything); it is
# not safe for deleting a specific line outright, since a real sentence can
# quote or contain one of those words ("...an unsubscribe link somewhere
# below..."). A full-line match is a different, stronger claim - see
# clean.py's _strip_line_chrome, which is the only caller allowed to act on
# it by deleting the matching element outright, anywhere in the document.
_FULL_LINE_CHROME: frozenset[str] = frozenset(
    {
        "forwarded this email?",
        "forwarded this email? subscribe here for more",
        "restack",
        "a message from our sponsor",
        "unsubscribe",
        # Round 3, section A. "Like" and "Comment" were declined in round 2
        # as PHRASES substring entries - the live queue contains real
        # article sentences ("Like most of America, Dry Powder will be...",
        # "Like it or not, we now live in a world...", "Like, right now.",
        # "Share this newsletter with someone who prefers truth...") that a
        # substring match would destroy. This is a different, stronger
        # claim: matched only when one of these words IS the whole
        # rendered line (see is_full_line_chrome's docstring and
        # _strip_line_chrome's _is_standalone_line guard), which none of
        # those sentences ever are - a sentence beginning with "Like" or
        # "Share" always has more text on the same line. See
        # test_a_sentence_starting_with_like_or_share_survives.
        "share",
        "like",
        "comment",
        "share now",
        "read in app",
        "view in browser",
        "share the bulwark",
    }
)

# "You received this email because ..." always continues with a
# newsletter-specific reason (who you signed up for, which list you are
# on), so it cannot be a fixed phrase - but the opening words alone are
# distinctive enough of bulk-mail disclosure language that no genuine
# editorial sentence plausibly starts this way. (The shorter substring
# "you received this" is already in PHRASES above for ratio scoring; this
# is deliberately the longer, more specific opening, since a prefix match
# is a stronger commitment than a substring match and this is the one
# used to delete a whole line.)
_FULL_LINE_CHROME_PREFIXES: tuple[str, ...] = ("you received this email because",)

# "Get the Bulwark app" - the user's own report names one publication, but
# the shape "Get the <name> app" is a general CTA-button pattern, not
# something a real sentence would independently produce as its own whole
# line. A broader "get the ..." pattern (no required "app" ending) was
# tried in the previous wave and found real false positives in the fixture
# corpus ("get the picture", "get the sense"); anchoring on the "... app"
# ending keeps the generalization past the one named publication without
# reintroducing that risk.
_GET_THE_APP_LINE = re.compile(r"^get the [\w' .-]+ app$", re.IGNORECASE)


def _normalize_full_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def is_full_line_chrome(text: str) -> bool:
    """True when `text`, taken as one whole rendered line (its internal
    line breaks collapsed to single spaces), is unambiguously chrome
    regardless of where in the document it sits.

    Unlike is_definite_chrome_line, which matches a PHRASES entry as a
    substring anywhere in the line, this requires the phrase to BE the
    whole line: "Unsubscribe" alone is chrome, but "...there is an
    unsubscribe link somewhere below..." is not, and this function says
    False for the latter - the exact adversarial case this project's
    history warns about.

    Also matches a bare postal address (reusing the same _ADDRESS_LINE
    pattern is_definite_chrome_line already uses, anchored with fullmatch
    and already proven safe against an address quoted mid-sentence). This
    closes a gap the F2 trace found live: a footer block reading "© 2026 /
    Substack Inc. / <address> / Unsubscribe" scores content_ratio just
    over CHROME_RATIO because "Substack Inc." ends in a period and so
    reads as a "sentence" under the short-line fallback - and removing the
    standalone "Unsubscribe" line only raises that ratio further, so the
    address is never reachable through the ratio path no matter what else
    this pass removes from around it. It needs the same kind of
    independent, explicit match "Unsubscribe" already has.
    """
    collapsed = _normalize_full_line(text)
    if not collapsed:
        return False
    normalized = collapsed.casefold()
    if normalized in _FULL_LINE_CHROME:
        return True
    if any(normalized.startswith(prefix) for prefix in _FULL_LINE_CHROME_PREFIXES):
        return True
    if _GET_THE_APP_LINE.match(normalized):
        return True
    return bool(_ADDRESS_LINE.fullmatch(collapsed))


def content_ratio(text: str) -> float:
    """Fraction of non-blank characters that look like real content."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    total = sum(len(line) for line in lines)
    content = sum(len(line) for line in lines if not is_boilerplate_line(line))
    return content / total
