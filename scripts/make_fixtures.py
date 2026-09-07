"""Sample one newsletter per sender into tests/fixtures/.

The fixtures are real personal mail and are gitignored. Regenerate them with
`make fixtures`.
"""

import email
import re
import sys
from email.message import Message
from pathlib import Path

from shabbat_print.mbox import find_thunderbird_mbox, split_mbox

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
UNSAFE = re.compile(r"[^a-z0-9]+")


def sender_of(message: Message) -> str:
    _, address = email.utils.parseaddr(message.get("From", ""))
    return address.lower()


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
    seen: set[str] = set()
    written = 0
    for raw in split_mbox(mbox):
        message = email.message_from_bytes(raw)
        sender = sender_of(message)
        if not sender or sender in seen:
            continue
        seen.add(sender)
        name = UNSAFE.sub("-", sender).strip("-")
        (FIXTURES / f"{name}.eml").write_bytes(raw)
        written += 1
    print(f"wrote {written} fixtures to {FIXTURES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
