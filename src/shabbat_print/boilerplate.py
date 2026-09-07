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
)

_URL_ONLY = re.compile(r"^(https?://\S+|www\.\S+)$", re.IGNORECASE)
_SENTENCE_END = (".", "!", "?", '"', "'", ")", ":", "”", "’")

# Below this, a line without sentence-ending punctuation reads as a
# link-roundup item or a navigation label rather than as prose.
SHORT_LINE = 40


def is_definite_chrome_line(line: str) -> bool:
    """True only for a confident signal: a known phrase, or a bare URL.

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
    return bool(_URL_ONLY.match(stripped))


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
