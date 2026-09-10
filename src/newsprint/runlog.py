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


def last_successful_run(state_dir: Path | None = None) -> datetime | None:
    directory = state_dir if state_dir is not None else DEFAULT_STATE_DIR
    if not directory.exists():
        return None
    stamps: list[datetime] = []
    for path in directory.glob("*.json"):
        try:
            entry = json.loads(path.read_text())
            if not isinstance(entry, dict):
                continue
            if entry.get("outcome") != "printed":
                continue
            # ValueError covers json.JSONDecodeError (malformed JSON text)
            # and UnicodeDecodeError (non-UTF-8 bytes in the file) as well as
            # datetime.fromisoformat() rejecting a malformed timestamp
            # string; TypeError covers fromisoformat() being handed a
            # non-string "at" value; KeyError covers "at" being absent.
            stamps.append(datetime.fromisoformat(entry["at"]))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return max(stamps) if stamps else None
