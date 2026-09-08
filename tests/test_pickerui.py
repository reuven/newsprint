"""Unit tests for the thin questionary wrapper.

pickerui.py is the only module that touches the questionary library -
picker.py's own module docstring explains why the split exists. These
tests never touch a real terminal: `checkbox` is injected exactly like
printer.spool's `runner` or mail.Mailbox's `imap_factory`, so the whole
wrapper (building the right Separator/Choice list, returning what the
"user" picked, treating a cancelled prompt as nothing selected) is
covered without prompt_toolkit's own event loop ever running.
"""

from datetime import UTC, datetime

import questionary

from shabbat_print.models import Document, Origin
from shabbat_print.picker import build_picklist
from shabbat_print.pickerui import questionary_prompt


def _doc(publication: str, title: str, day: str, uid: int = 1) -> Document:
    return Document(
        origin=Origin(kind="email", identifier=f"<{uid}@example.com>", uid=uid),
        publication=publication,
        title=title,
        date=datetime.fromisoformat(day).replace(tzinfo=UTC),
        html="<p>x</p>",
    )


class _FakeQuestion:
    """Stands in for questionary.Question - all Mailbox.ask() needs to
    return is whatever value the fake checkbox factory was told to
    hand back."""

    def __init__(self, result: list[Document] | None) -> None:
        self._result = result

    def ask(self) -> list[Document] | None:
        return self._result


def _fake_checkbox(calls: list[tuple], result: list[Document] | None):
    def factory(message: str, choices):
        calls.append((message, choices))
        return _FakeQuestion(result)

    return factory


def test_builds_one_separator_per_group_and_one_choice_per_row() -> None:
    candidates = [
        _doc("Money Stuff", "Issue A", "2026-09-02", uid=1),
        _doc("The Economist", "Issue B", "2026-09-03", uid=2),
    ]
    picklist = build_picklist(candidates, sizes={1: 20_000, 2: 200_000})

    calls: list[tuple] = []
    questionary_prompt(picklist, checkbox=_fake_checkbox(calls, result=[]))

    assert len(calls) == 1
    _message, choices = calls[0]
    kinds = [type(choice).__name__ for choice in choices]
    assert kinds == ["Separator", "Choice", "Separator", "Choice"]
    assert choices[0].line == "Money Stuff"
    assert "Issue A" in choices[1].title
    assert "short" in choices[1].title
    assert choices[2].line == "The Economist"
    assert "Issue B" in choices[3].title
    assert "long" in choices[3].title
    # The Choice's value is the real Document - checkbox() itself, not
    # index math, is what tells the caller which documents were picked.
    assert choices[1].value is picklist.groups[0].rows[0].document


def test_returns_exactly_what_the_checkbox_returned() -> None:
    candidates = [_doc("Money Stuff", "Issue A", "2026-09-02", uid=1)]
    picklist = build_picklist(candidates, sizes={1: 20_000})
    picked = [picklist.groups[0].rows[0].document]

    result = questionary_prompt(picklist, checkbox=_fake_checkbox([], result=picked))
    assert result == picked


def test_cancelling_returns_none() -> None:
    """questionary's own .ask() returns None on Ctrl-C - "a way to
    cancel that selects nothing" - and this wrapper must pass that
    through rather than coercing it to an empty list, so a caller can
    still tell "cancelled" apart from "confirmed with nothing checked"
    if it ever needs to."""
    candidates = [_doc("Money Stuff", "Issue A", "2026-09-02", uid=1)]
    picklist = build_picklist(candidates, sizes={1: 20_000})

    result = questionary_prompt(picklist, checkbox=_fake_checkbox([], result=None))
    assert result is None


def test_skips_the_library_entirely_when_there_is_nothing_to_offer() -> None:
    def explode(message, choices):
        raise AssertionError("checkbox must not be called with nothing to offer")

    picklist = build_picklist([], sizes={})
    assert questionary_prompt(picklist, checkbox=explode) == []


def test_the_default_checkbox_factory_is_questionarys_own() -> None:
    """The one line that is not exercised through the fake - proof the
    injectable default really is the real library, not a stand-in that
    quietly does nothing in production."""
    import inspect

    from shabbat_print.pickerui import questionary_prompt as prompt_function

    default = inspect.signature(prompt_function).parameters["checkbox"].default
    assert default is questionary.checkbox
