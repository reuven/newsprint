"""Regenerate ~/.config/newsprint/publications.toml from the user's own
local mail archive.

Reads a Thunderbird mbox file and reports, one line per publication, the
name newsprint's own resolution order (see extract._publication) would
land on: List-Id keyed where a message carries a List-Id, address keyed
otherwise - exactly the precedence extract.py itself uses, so what this
script writes and what extract() would fall back to without any override
agree by construction. Names are MIME-decoded via the same decode_header()
+ make_header() extract.py uses for headers, so a raw RFC 2047 encoded word
never lands in the file.

Read-only against the mbox: it only reads the local file
`find_thunderbird_mbox()` finds. It never touches IMAP, the keychain, CUPS,
or Preview. Writing only happens to the publications.toml path passed on
the command line (or DEFAULT_PUBLICATIONS_PATH), and only when run as a
script - importing this module does nothing.
"""

from __future__ import annotations

import argparse
import email
import re
import sys
from collections import Counter
from email.message import Message
from pathlib import Path

from newsprint.config import DEFAULT_PUBLICATIONS_PATH
from newsprint.extract import _decode_words
from newsprint.mbox import find_thunderbird_mbox, split_mbox

# Mailchimp's own List-Id has no human label at all: the part before "<" is
# a bare campaign hash (e.g. "b98e2de85f03865f1d38de74fmc list <....mcsv.net>"
# for Benedict Evans's newsletter, confirmed against the user's own archive
# alongside Dan Oshinsky's and Git Rev News's - three different senders, same
# shape). Title-casing that hash ("B98E2De85F03865F1D38De74Fmc List") would
# be a worse name than the sender's own From display, so this pattern routes
# those labels to the display-name fallback below instead.
_OPAQUE_HASH = re.compile(r"^[0-9a-f]{16,}")


def _list_id_label(message: Message) -> str:
    raw = message.get("List-Id")
    if raw is None:
        return ""
    decoded = _decode_words(raw)
    return decoded.split("<")[0].strip().strip('"').strip()


def _sender_address(message: Message) -> str:
    _, address = email.utils.parseaddr(_decode_words(message.get("From", "")))
    return address.lower()


def _sender_display(message: Message) -> str:
    display, _ = email.utils.parseaddr(_decode_words(message.get("From", "")))
    return display


def _tidy(label: str) -> str:
    """Title-case a raw List-Id label ("the veggie" -> "The Veggie").

    A List-Id label is a machine identifier, typically all lower case
    (confirmed against the user's own archive - the New York Times sends
    List-Id values like "the morning <nn.nytimes.com>" verbatim in lower
    case), not a name anyone chose for print. str.title() is imperfect
    for a name like "DealBook" (it becomes "Dealbook"), but that is a
    starting point a user can hand-correct with a List-Id-keyed override
    in publications.toml, which is exactly the escape hatch this schema
    exists to provide - and it is a large improvement over every one of
    a sender's newsletters sharing a single first-seen name.
    """
    return label.title()


def collect(
    mbox: Path,
) -> tuple[dict[str, tuple[str, int]], dict[str, tuple[str, int]]]:
    """Return (by_list_id, by_address), each mapping a lower-cased key to
    (chosen name, message count).

    A message contributes to by_list_id when it carries a List-Id, and to
    by_address only when it does not - mirroring extract._publication's
    own resolution order, so an address bucket never shadows a List-Id
    bucket the way the old single-table, address-only file did.

    A List-Id's name is normally the tidied label itself, not the
    sender's From display: for the New York Times, the From display
    ("NYT Cooking") is reused across several distinct List-Ids (Cooking,
    The Veggie, Five Weeknight Dishes all arrive as "NYT Cooking" -
    confirmed against the user's own archive), so preferring display
    there would just recreate the one-name-per-address bug one level
    down. The exception is _OPAQUE_HASH labels (see above), where the
    label carries no information at all and the display name is the only
    real name available.
    """
    list_id_counts: dict[str, int] = Counter()
    list_id_display: dict[str, str] = {}
    list_id_senders: dict[str, Counter[str]] = {}
    address_counts: dict[str, int] = Counter()
    address_names: dict[str, Counter[str]] = {}

    for raw in split_mbox(mbox):
        message = email.message_from_bytes(raw)
        address = _sender_address(message)
        if not address:
            continue
        label = _list_id_label(message)
        display = _sender_display(message) or address
        if label:
            key = label.lower()
            list_id_counts[key] += 1
            list_id_display.setdefault(key, label)
            list_id_senders.setdefault(key, Counter())[display] += 1
        else:
            address_counts[address] += 1
            address_names.setdefault(address, Counter())[display] += 1

    by_list_id = {}
    for key, count in list_id_counts.items():
        label = list_id_display[key]
        if _OPAQUE_HASH.match(key):
            senders = list_id_senders[key]
            name = senders.most_common(1)[0][0] if senders else _tidy(label)
        else:
            name = _tidy(label)
        by_list_id[key] = (name, count)

    by_address = {
        address: (names.most_common(1)[0][0], count)
        for address, count in address_counts.items()
        if (names := address_names[address])
    }
    return by_list_id, by_address


def render(
    by_list_id: dict[str, tuple[str, int]], by_address: dict[str, tuple[str, int]]
) -> str:
    lines = ["# Sender address -> the name printed on the cell.", "[names]"]
    for address, (name, count) in sorted(
        by_address.items(), key=lambda item: (-item[1][1], item[0])
    ):
        escaped = name.replace('"', '\\"')
        lines.append(f'"{address}" = "{escaped}"   # {count} messages')

    lines.append("")
    lines.append(
        "# List-Id -> the name printed on the cell. Takes priority over "
        "[names]: one address"
    )
    lines.append(
        "# (nytdirect@nytimes.com, for instance) can send many distinct newsletters."
    )
    lines.append("[list_id_names]")
    for list_id, (name, count) in sorted(
        by_list_id.items(), key=lambda item: (-item[1][1], item[0])
    ):
        escaped_key = list_id.replace('"', '\\"')
        escaped_name = name.replace('"', '\\"')
        lines.append(f'"{escaped_key}" = "{escaped_name}"   # {count} messages')

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mbox", type=Path, default=None)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_PUBLICATIONS_PATH,
        help=f"where to write publications.toml (default: {DEFAULT_PUBLICATIONS_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the new file to stdout instead of writing it",
    )
    args = parser.parse_args(argv)

    mbox = args.mbox if args.mbox is not None else find_thunderbird_mbox()
    if mbox is None or not mbox.exists():
        print("no local Thunderbird mbox found; pass --mbox PATH", file=sys.stderr)
        return 1

    by_list_id, by_address = collect(mbox)
    text = render(by_list_id, by_address)

    if args.dry_run:
        print(text, end="")
        return 0

    if args.out.exists():
        backup = args.out.with_suffix(args.out.suffix + ".bak")
        backup.write_text(args.out.read_text())
        print(f"backed up existing file to {backup}", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    print(
        f"wrote {len(by_address)} address entries and "
        f"{len(by_list_id)} List-Id entries to {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
