"""A record of every run, written before any mail is modified.

The log is what makes retirement reversible: it names the Message-IDs queued
for a run before mutation, then, after retire() has run, which were actually
marked read and unstarred (in that order) and moved to Trash - and which were
not, because a message whose move fails has its star restored rather than
left silently missing from the queue, so a bad or partial run can still be
recovered by hand.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "newsprint" / "runs"


def record(entry: dict[str, Any], state_dir: Path | None = None) -> Path:
    directory = state_dir if state_dir is not None else DEFAULT_STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    # Copy rather than mutate: entry.setdefault() on the caller's own dict
    # would silently stamp it with "at" the first time it is logged and then
    # leave that stamp stale on every later call with the same dict, which is
    # a real risk if a caller builds one template entry and reuses it run
    # after run.
    payload = dict(entry)
    payload.setdefault("at", datetime.now(UTC).isoformat())
    stamp = datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S-%f")
    path = directory / f"{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def _identifiers(value: Any) -> set[str]:
    """Message-IDs out of a log field, or nothing if that is not what it holds.

    A field can be the wrong shape for honest reasons: a cancelled run
    records `documents` as a count rather than a list, since it never had
    mail to name. That is not damage, so it is not reported - it just
    yields no identifiers.
    """
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}


def _entries(directory: Path) -> list[tuple[datetime, dict[str, Any]]]:
    """Every readable, dated entry in the log, oldest first.

    The one place that knows how to read this directory, because every
    question asked of the log has the same two answers for a file it
    cannot read: skip it, and do not let it change the verdict. An entry
    that is unreadable, is not an object, or carries no usable timestamp
    is dropped here rather than at each call site.

    ValueError covers json.JSONDecodeError (malformed JSON text) and
    UnicodeDecodeError (non-UTF-8 bytes) as well as fromisoformat()
    rejecting a malformed timestamp; TypeError covers fromisoformat()
    being handed a non-string "at"; KeyError covers "at" being absent.
    """
    found: list[tuple[datetime, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            entry = json.loads(path.read_text())
            if not isinstance(entry, dict):
                continue
            when = datetime.fromisoformat(entry["at"])
        except (OSError, ValueError, TypeError, KeyError):
            continue
        found.append((when, entry))
    found.sort(key=lambda dated: dated[0])
    return found


def pending_mail(state_dir: Path | None = None) -> list[dict[str, Any]]:
    """Runs that recorded mail and never retired it, newest first.

    This is what lets a reprint finish the job a failed run started: the
    mail is still starred, and the entry written before the packet was
    spooled is the only record of which messages it was.

    Nothing in an entry says whether its own mail was retired - the
    retirement is a separate entry, written later, naming Message-IDs
    rather than a run. So the log is walked in time order, adding on each
    retirement and removing again on each --unretire, and a run counts as
    pending while any of its messages are outside that set. That is also
    what settles the stranded runs: a run that stopped partway leaves its
    mail starred, the following week's run collects the same messages and
    retires them, and the stranded entry still says "printing" forever.
    Only the Message-IDs know.

    An unreadable entry is skipped, which fails open in the safe
    direction: a retirement that cannot be read leaves its run pending,
    so a person is asked about mail that may already be gone rather than
    mail being dropped silently out of the record that could recover it.
    """
    directory = state_dir if state_dir is not None else DEFAULT_STATE_DIR
    if not directory.exists():
        return []
    retired: set[str] = set()
    queued: list[dict[str, Any]] = []
    for _, entry in _entries(directory):
        outcome = entry.get("outcome")
        if outcome == "retired":
            retired |= _identifiers(entry.get("retired_ids"))
        elif outcome == "unretired":
            retired -= _identifiers(entry.get("restored"))
        elif _identifiers(entry.get("documents")) and entry.get("uids"):
            queued.append(entry)
    return [
        entry
        for entry in reversed(queued)
        if _identifiers(entry.get("documents")) - retired
    ]


def last_retirement(state_dir: Path | None = None) -> dict[str, Any] | None:
    """The most recent run that actually retired mail, or None.

    This is what makes --unretire possible. The uids in that entry are of
    no use afterwards - a moved message has a different uid in its new
    folder - but "documents" holds each message's Message-ID, which is
    the same wherever the message goes, and is how the messages are found
    again in the trash.
    """
    directory = state_dir if state_dir is not None else DEFAULT_STATE_DIR
    if not directory.exists():
        return None
    retirements = [
        entry for _, entry in _entries(directory) if entry.get("outcome") == "retired"
    ]
    return retirements[-1] if retirements else None


def last_successful_run(state_dir: Path | None = None) -> datetime | None:
    directory = state_dir if state_dir is not None else DEFAULT_STATE_DIR
    if not directory.exists():
        return None
    stamps = [
        when for when, entry in _entries(directory) if entry.get("outcome") == "printed"
    ]
    return stamps[-1] if stamps else None
