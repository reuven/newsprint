"""Unit tests for the thin questionary wrapper.

pickerui.py is the only module that touches the questionary library -
picker.py's own module docstring explains why the split exists. These
tests never touch a real terminal: `checkbox` is injected exactly like
printer.spool's `runner` or mail.Mailbox's `imap_factory`, so the whole
wrapper (building the right Separator/Choice list, returning what the
"user" picked, treating a cancelled prompt as nothing selected) is
covered without prompt_toolkit's own event loop ever running.
`terminal_size` is injected the same way, for the same reason - a real
terminal's width is not something a test should depend on (see
picker.py's own tests for the actual column-width/truncation logic;
this module has no formatting decisions of its own left to make).
"""

import os
from collections.abc import Callable
from datetime import UTC, datetime

import questionary

from newsprint.models import Document, Origin
from newsprint.picker import build_picklist
from newsprint.pickerui import questionary_prompt


def _doc(publication: str, title: str, day: str, uid: int = 1) -> Document:
    return Document(
        origin=Origin(kind="email", identifier=f"<{uid}@example.com>", uid=uid),
        publication=publication,
        title=title,
        date=datetime.fromisoformat(day).replace(tzinfo=UTC),
        html="<p>x</p>",
    )


def _fixed_width(columns: int) -> Callable[[], os.terminal_size]:
    def factory() -> os.terminal_size:
        return os.terminal_size((columns, 24))

    return factory


class _FakeQuestion:
    """Stands in for questionary.Question - all Mailbox.ask() needs to
    return is whatever value the fake checkbox factory was told to
    hand back."""

    def __init__(self, result: list[Document] | None) -> None:
        self._result = result

    def ask(self) -> list[Document] | None:
        return self._result


def _fake_checkbox(calls: list[tuple], result: list[Document] | None):
    def factory(message: str, choices, **options):
        calls.append((message, choices, options))
        return _FakeQuestion(result)

    return factory


def test_builds_a_blank_and_a_heading_separator_before_each_groups_rows() -> None:
    candidates = [
        _doc("Money Stuff", "Issue A", "2026-09-02", uid=1),
        _doc("The Economist", "Issue B", "2026-09-03", uid=2),
    ]
    picklist = build_picklist(candidates, sizes={1: 20_000, 2: 200_000})

    calls: list[tuple] = []
    questionary_prompt(
        picklist,
        checkbox=_fake_checkbox(calls, result=[]),
        terminal_size=_fixed_width(80),
    )

    assert len(calls) == 1
    _message, choices, _options = calls[0]
    kinds = [type(choice).__name__ for choice in choices]
    # blank, heading, row per group - see picker.py's layout_picklist.
    assert kinds == [
        "Separator",
        "Separator",
        "Choice",
        "Separator",
        "Separator",
        "Choice",
    ]
    assert choices[0].line == " "
    assert "MONEY STUFF" in choices[1].line
    assert "Issue A" in choices[2].title
    assert "short" in choices[2].title
    assert choices[3].line == " "
    assert "THE ECONOMIST" in choices[4].line
    assert "Issue B" in choices[5].title
    assert "long" in choices[5].title
    # The Choice's value is the real Document - checkbox() itself, not
    # index math, is what tells the caller which documents were picked.
    assert choices[2].value is picklist.groups[0].rows[0].document


def test_a_blank_separator_line_is_a_single_space_not_the_library_default() -> None:
    """questionary.Separator("") falls back to its own 15-dash default
    line (a falsy-string check in Separator.__init__ - verified by
    reading questionary 2.1.1's own source, not assumed) - a real blank
    line needs a non-empty-but-invisible string instead."""
    candidates = [_doc("Money Stuff", "Issue A", "2026-09-02", uid=1)]
    picklist = build_picklist(candidates, sizes={1: 20_000})

    calls: list[tuple] = []
    questionary_prompt(
        picklist,
        checkbox=_fake_checkbox(calls, result=[]),
        terminal_size=_fixed_width(80),
    )
    _message, choices, _options = calls[0]
    assert choices[0].line != questionary.Separator.default_separator


def test_returns_exactly_what_the_checkbox_returned() -> None:
    candidates = [_doc("Money Stuff", "Issue A", "2026-09-02", uid=1)]
    picklist = build_picklist(candidates, sizes={1: 20_000})
    picked = [picklist.groups[0].rows[0].document]

    result = questionary_prompt(
        picklist,
        checkbox=_fake_checkbox([], result=picked),
        terminal_size=_fixed_width(80),
    )
    assert result == picked


def test_cancelling_returns_none() -> None:
    """questionary's own .ask() returns None on Ctrl-C - "a way to
    cancel that selects nothing" - and this wrapper must pass that
    through rather than coercing it to an empty list, so a caller can
    still tell "cancelled" apart from "confirmed with nothing checked"
    if it ever needs to."""
    candidates = [_doc("Money Stuff", "Issue A", "2026-09-02", uid=1)]
    picklist = build_picklist(candidates, sizes={1: 20_000})

    result = questionary_prompt(
        picklist,
        checkbox=_fake_checkbox([], result=None),
        terminal_size=_fixed_width(80),
    )
    assert result is None


def test_skips_the_library_entirely_when_there_is_nothing_to_offer() -> None:
    def explode(message, choices):
        raise AssertionError("checkbox must not be called with nothing to offer")

    picklist = build_picklist([], sizes={})
    assert (
        questionary_prompt(picklist, checkbox=explode, terminal_size=_fixed_width(80))
        == []
    )


def test_the_default_checkbox_factory_is_questionarys_own() -> None:
    """The one line that is not exercised through the fake - proof the
    injectable default really is the real library, not a stand-in that
    quietly does nothing in production."""
    import inspect

    from newsprint.pickerui import questionary_prompt as prompt_function

    default = inspect.signature(prompt_function).parameters["checkbox"].default
    assert default is questionary.checkbox


def test_the_default_terminal_size_factory_is_shutils_own() -> None:
    """Same proof as the checkbox default, for the other injected
    side-effecting call: production really reads the real terminal
    width via shutil.get_terminal_size, not a hardcoded guess."""
    import inspect
    import shutil

    from newsprint.pickerui import questionary_prompt as prompt_function

    default = inspect.signature(prompt_function).parameters["terminal_size"].default
    assert default is shutil.get_terminal_size


def test_a_narrow_terminal_still_produces_the_same_number_of_choices() -> None:
    """Just proof the injected width actually reaches the layout - the
    real column-width degradation logic is picker.py's own layout_
    picklist, exercised there against many widths without any of this
    module's machinery."""
    candidates = [_doc("Money Stuff", "Issue A", "2026-09-02", uid=1)]
    picklist = build_picklist(candidates, sizes={1: 20_000})

    calls: list[tuple] = []
    questionary_prompt(
        picklist,
        checkbox=_fake_checkbox(calls, result=[]),
        terminal_size=_fixed_width(20),
    )
    _message, choices, _options = calls[0]
    assert len(choices) == 3


class _RealLayoutQuestion:
    """A stub that carries a *real* questionary Question's layout.

    The heading-visibility fix reaches into prompt_toolkit's own object
    graph, so a hand-rolled fake would prove nothing about it - this
    builds the genuine application questionary would have built, while
    keeping .ask() a stub so no event loop or terminal is involved.
    """

    def __init__(self, message: str, choices) -> None:
        self.application = questionary.checkbox(message, choices).application

    def ask(self) -> list[Document]:
        return []


def _choice_list_window(question: _RealLayoutQuestion):
    from prompt_toolkit.layout.containers import Window
    from questionary.prompts.common import InquirerControl

    windows = [
        container
        for container in question.application.layout.walk()
        if isinstance(container, Window)
        and isinstance(container.content, InquirerControl)
    ]
    assert len(windows) == 1, f"expected one choice-list window, found {len(windows)}"
    return windows[0]


def test_the_group_heading_stays_on_screen_when_the_cursor_reaches_a_first_row() -> (
    None
):
    """Pressing up from the top row and back down used to leave the
    cursor correct but its publication heading scrolled off, because
    prompt_toolkit only keeps the cursor line itself visible."""
    built: list[_RealLayoutQuestion] = []

    def factory(message: str, choices, **options):
        built.append(_RealLayoutQuestion(message, choices))
        return built[-1]

    picklist = build_picklist(
        [_doc("Axios Macro", "New trade stakes", "2026-09-08")], sizes={1: 4096}
    )
    questionary_prompt(picklist, checkbox=factory, terminal_size=_fixed_width(100))

    offsets = _choice_list_window(built[0]).scroll_offsets
    assert offsets.top >= 1, "no context kept above the cursor - heading can scroll off"


def test_a_checkbox_that_is_not_questionarys_own_is_left_alone() -> None:
    """The injected fakes elsewhere in this file are bare stubs with no
    .application at all; adjusting the layout must not require one."""
    calls: list[tuple] = []
    picklist = build_picklist(
        [_doc("Axios Macro", "New trade stakes", "2026-09-08")], sizes={1: 4096}
    )
    assert (
        questionary_prompt(
            picklist,
            checkbox=_fake_checkbox(calls, result=[]),
            terminal_size=_fixed_width(100),
        )
        == []
    )


def test_the_list_can_be_filtered_by_typing() -> None:
    """--since can open the window to weeks, and a window that wide runs
    to a hundred candidates - too many to find one publication by
    scrolling. questionary filters as you type, but only if asked, and
    only with j/k navigation turned off: it refuses both at once, since
    j and k are letters a filter needs to receive."""
    picklist = build_picklist(
        [_doc("Money Stuff", "Issue A", "2026-09-02", uid=1)], sizes={1: 20_000}
    )

    calls: list[tuple] = []
    questionary_prompt(
        picklist,
        checkbox=_fake_checkbox(calls, result=[]),
        terminal_size=_fixed_width(80),
    )

    message, _choices, options = calls[0]
    assert options["use_search_filter"] is True
    assert options["use_jk_keys"] is False
    assert "filter" in message, "the hint has to say the filter is there"
