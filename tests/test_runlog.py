import json
from datetime import UTC, datetime
from pathlib import Path

from newsprint import runlog


def test_record_writes_a_readable_file(tmp_path: Path) -> None:
    path = runlog.record({"outcome": "printed", "documents": []}, state_dir=tmp_path)
    assert path.exists()
    assert path.suffix == ".json"


def test_last_successful_run_is_none_when_empty(tmp_path: Path) -> None:
    assert runlog.last_successful_run(state_dir=tmp_path) is None


def test_last_successful_run_is_none_when_the_state_dir_does_not_exist(
    tmp_path: Path,
) -> None:
    assert runlog.last_successful_run(state_dir=tmp_path / "never-created") is None


def test_last_successful_run_finds_the_newest_printed_run(tmp_path: Path) -> None:
    runlog.record(
        {"outcome": "printed", "at": "2026-08-28T15:00:00+00:00"}, state_dir=tmp_path
    )
    runlog.record(
        {"outcome": "printed", "at": "2026-09-04T15:00:00+00:00"}, state_dir=tmp_path
    )
    found = runlog.last_successful_run(state_dir=tmp_path)
    assert found == datetime(2026, 9, 4, 15, 0, tzinfo=UTC)


def test_unprinted_runs_are_ignored(tmp_path: Path) -> None:
    runlog.record(
        {"outcome": "cancelled", "at": "2026-09-04T15:00:00+00:00"}, state_dir=tmp_path
    )
    assert runlog.last_successful_run(state_dir=tmp_path) is None


def test_printed_kept_does_not_count_as_a_successful_run(tmp_path: Path) -> None:
    """A --no-retire rehearsal ('printed-kept') must not advance the
    picker's review window - see picker.window_since - so it must not
    register as a successful run any more than a cancelled one does, even
    though real printing genuinely happened."""
    runlog.record(
        {"outcome": "printed-kept", "at": "2026-09-04T15:00:00+00:00"},
        state_dir=tmp_path,
    )
    assert runlog.last_successful_run(state_dir=tmp_path) is None


# The tests below are not in the brief; they exist to prove the two points the
# task flagged for extra care: record() must not mutate the caller's dict, and
# last_successful_run() must survive files that are corrupt in ways a real
# damaged or partially-written file can actually be corrupt.


def test_record_does_not_mutate_the_callers_dict(tmp_path: Path) -> None:
    entry = {"outcome": "printed"}
    runlog.record(entry, state_dir=tmp_path)
    assert entry == {"outcome": "printed"}


def test_last_successful_run_survives_invalid_json_text(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("not valid json {")
    runlog.record(
        {"outcome": "printed", "at": "2026-09-04T15:00:00+00:00"}, state_dir=tmp_path
    )
    found = runlog.last_successful_run(state_dir=tmp_path)
    assert found == datetime(2026, 9, 4, 15, 0, tzinfo=UTC)


def test_last_successful_run_survives_non_utf8_bytes(tmp_path: Path) -> None:
    (tmp_path / "binary.json").write_bytes(b"\xff\xfe\x00\x01invalid utf8 \x80\x81")
    runlog.record(
        {"outcome": "printed", "at": "2026-09-04T15:00:00+00:00"}, state_dir=tmp_path
    )
    found = runlog.last_successful_run(state_dir=tmp_path)
    assert found == datetime(2026, 9, 4, 15, 0, tzinfo=UTC)


def test_last_successful_run_survives_json_that_is_not_an_object(
    tmp_path: Path,
) -> None:
    (tmp_path / "array.json").write_text("[1, 2, 3]")
    runlog.record(
        {"outcome": "printed", "at": "2026-09-04T15:00:00+00:00"}, state_dir=tmp_path
    )
    found = runlog.last_successful_run(state_dir=tmp_path)
    assert found == datetime(2026, 9, 4, 15, 0, tzinfo=UTC)


def test_last_successful_run_survives_a_non_string_at_field(tmp_path: Path) -> None:
    (tmp_path / "bad-at.json").write_text(json.dumps({"outcome": "printed", "at": 5}))
    runlog.record(
        {"outcome": "printed", "at": "2026-09-04T15:00:00+00:00"}, state_dir=tmp_path
    )
    found = runlog.last_successful_run(state_dir=tmp_path)
    assert found == datetime(2026, 9, 4, 15, 0, tzinfo=UTC)


def test_last_retirement_is_none_without_a_state_directory(tmp_path: Path) -> None:
    assert runlog.last_retirement(tmp_path / "never-created") is None


def test_last_retirement_ignores_other_outcomes_and_bad_files(tmp_path: Path) -> None:
    """A run log accumulates every outcome, and a half-written file is a
    real possibility; neither may stop --unretire finding the retirement."""
    import json

    (tmp_path / "a.json").write_text(
        json.dumps({"outcome": "printed", "at": "2026-09-10T10:00:00+00:00"})
    )
    (tmp_path / "b.json").write_text("{ not json at all")
    (tmp_path / "c.json").write_text(json.dumps([1, 2, 3]))
    (tmp_path / "d.json").write_text(json.dumps({"outcome": "retired"}))
    assert runlog.last_retirement(tmp_path) is None


def test_last_retirement_takes_the_most_recent_one(tmp_path: Path) -> None:
    import json

    for name, when, trash in (
        ("old.json", "2026-09-01T10:00:00+00:00", "OLD"),
        ("new.json", "2026-09-10T10:00:00+00:00", "NEW"),
        ("older.json", "2026-08-01T10:00:00+00:00", "OLDER"),
    ):
        (tmp_path / name).write_text(
            json.dumps({"outcome": "retired", "at": when, "trash": trash})
        )
    entry = runlog.last_retirement(tmp_path)
    assert entry is not None and entry["trash"] == "NEW"


def test_a_recorded_entry_is_stamped_and_found_again(tmp_path: Path) -> None:
    """record() and last_retirement() are two halves of --unretire: the
    entry is written with the time it happened under "at", and read back
    by that same key. A stamp filed under any other name leaves the run
    log full of retirements that --unretire cannot see."""
    runlog.record({"outcome": "retired", "messages": ["<a@x>"]}, state_dir=tmp_path)
    found = runlog.last_retirement(tmp_path)
    assert found is not None
    assert found["messages"] == ["<a@x>"]
    assert datetime.fromisoformat(found["at"]).tzinfo is not None


def test_two_entries_in_the_same_run_do_not_collide(tmp_path: Path) -> None:
    """A run records more than once - printed, then retired - and the
    file name is the only thing keeping the second from overwriting the
    first."""
    first = runlog.record({"outcome": "printed"}, state_dir=tmp_path)
    second = runlog.record({"outcome": "retired"}, state_dir=tmp_path)
    assert first != second
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_an_entry_may_carry_things_json_cannot_hold_by_itself(
    tmp_path: Path,
) -> None:
    """What is recorded includes paths and dates, which json refuses
    outright. They are written as text rather than costing the caller a
    conversion at every call site - and a run that failed to log is a run
    that cannot be undone."""
    path = runlog.record(
        {"outcome": "printed", "pdf": tmp_path / "sheets.pdf"}, state_dir=tmp_path
    )
    written = json.loads(path.read_text())
    assert written["pdf"] == str(tmp_path / "sheets.pdf")


def test_the_state_directory_is_made_including_its_parents(tmp_path: Path) -> None:
    """The state directory sits under a working directory that may not
    exist yet on the first run of the day."""
    nested = tmp_path / "work" / "state"
    assert runlog.record({"outcome": "printed"}, state_dir=nested).exists()
