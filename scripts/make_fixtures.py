"""Sample the oldest and newest newsletter per sender into tests/fixtures/.

One message per sender optimises for the wrong variable: what breaks an HTML
cleaner is *template* variety, and templates come from sending platforms, not
from senders - a handful of platforms (Substack chief among them) account
for most of the corpus by message count. Publications also change their HTML
over time, and one arbitrary sample per sender never sees that drift. So for
each sender we keep the oldest and the newest message we have, targeting
format drift directly. (A sender with only one message on file gets one
fixture, not a duplicate.)

The fixtures are real personal mail and are gitignored. Regenerate them with
`make fixtures`.
"""

import email
import re
import sys
from collections import Counter
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path

from shabbat_print.mbox import find_thunderbird_mbox, split_mbox

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
UNSAFE = re.compile(r"[^a-z0-9]+")

# Matched, in order, against the lowercased text of a handful of headers that
# tend to carry the sending platform's fingerprint even when the visible
# "From" address is a vanity domain. First match wins; unmatched senders are
# bucketed as "other" - which is most publisher in-house sends: the New York
# Times, the Economist, Wired, Axios, the New Yorker's own infra, etc., the
# hand-built templates this expansion exists to sample more of.
PLATFORM_HEADERS = (
    "X-Mailer",
    "List-Unsubscribe",
    "Message-ID",
    "X-Feedback-ID",
    "Sender",
)
PLATFORM_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("substack", ("substack",)),
    ("beehiiv", ("beehiiv",)),
    ("mailchimp", ("mailchimp", "mcsv")),
    ("sendgrid", ("sendgrid",)),
    ("mailgun", ("mailgun",)),
    ("convertkit", ("convertkit",)),
    ("ghost", ("ghost",)),
    ("buttondown", ("buttondown",)),
    ("klaviyo", ("klaviyo",)),
    ("campaignmonitor", ("campaignmonitor",)),
    ("constantcontact", ("constantcontact",)),
    ("amazonses", ("amazonses",)),
    ("postmark", ("postmark",)),
    ("braze", ("braze",)),
    ("iterable", ("iterable",)),
    ("customer.io", ("customer.io",)),
    ("sailthru", ("sailthru",)),
]


def sender_of(message: Message) -> str:
    _, address = email.utils.parseaddr(message.get("From", ""))
    return address.lower()


def platform_of(message: Message) -> str:
    """Guess the sending platform from headers, falling back to "other".

    Word-boundary matching matters: a sender's own name can accidentally
    contain a platform's name (e.g. "Forrest Brazeal" contains "braze"), and
    an unqualified substring match would misclassify it.
    """
    text = " ".join(
        str(message.get(header, "") or "") for header in PLATFORM_HEADERS
    ).lower()
    for platform, keywords in PLATFORM_PATTERNS:
        if any(re.search(rf"\b{re.escape(keyword)}\b", text) for keyword in keywords):
            return platform
    return "other"


def date_of(message: Message) -> float:
    """A sortable timestamp for a message, oldest-first.

    Falls back to negative infinity - i.e. "oldest possible" - for the rare
    message with a missing or unparsable Date header, so a bad date can only
    ever lose an "oldest" comparison, never silently win a "newest" one.
    """
    try:
        return parsedate_to_datetime(message.get("Date", "")).timestamp()
    except TypeError, ValueError:
        return float("-inf")


def print_histogram(platforms: Counter[str]) -> None:
    width = max(len(name) for name in platforms) if platforms else 0
    for platform, count in platforms.most_common():
        print(f"  {count:4d}  {platform.ljust(width)}")


def main() -> int:
    mbox = Path(sys.argv[1]) if len(sys.argv) > 1 else find_thunderbird_mbox()
    if mbox is None or not mbox.exists():
        print(
            "no local Thunderbird mbox found; pass the path as an argument",
            file=sys.stderr,
        )
        return 1
    print(f"reading {mbox}")
    FIXTURES.mkdir(parents=True, exist_ok=True)

    oldest: dict[str, tuple[float, bytes]] = {}
    newest: dict[str, tuple[float, bytes]] = {}
    for raw in split_mbox(mbox):
        message = email.message_from_bytes(raw)
        sender = sender_of(message)
        if not sender:
            continue
        when = date_of(message)
        if sender not in oldest or when < oldest[sender][0]:
            oldest[sender] = (when, raw)
        if sender not in newest or when >= newest[sender][0]:
            newest[sender] = (when, raw)

    written = 0
    platforms: Counter[str] = Counter()
    for sender in sorted(oldest):
        name = UNSAFE.sub("-", sender).strip("-")
        _, old_raw = oldest[sender]
        _, new_raw = newest[sender]
        samples = (
            [("", old_raw)]
            if old_raw == new_raw
            else [("-oldest", old_raw), ("-newest", new_raw)]
        )
        for suffix, raw in samples:
            (FIXTURES / f"{name}{suffix}.eml").write_bytes(raw)
            platforms[platform_of(email.message_from_bytes(raw))] += 1
            written += 1

    print(f"wrote {written} fixtures ({len(oldest)} senders) to {FIXTURES}")
    print("platform histogram:")
    print_histogram(platforms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
