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


def content_ratio(text: str) -> float:
    """Fraction of non-blank characters that look like real content."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    total = sum(len(line) for line in lines)
    content = sum(len(line) for line in lines if not is_boilerplate_line(line))
    return content / total
