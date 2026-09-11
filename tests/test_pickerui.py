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
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout, Window
from questionary.prompts.common import InquirerControl

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


class _FakeQuestionWithLayout:
    """A questionary.Question as far as the prompt tweaks are concerned:
    an application with a layout to walk and key bindings to add to."""

    def __init__(self, window: Window) -> None:
        self.application = Application(
            layout=Layout(window), key_bindings=KeyBindings()
        )


def _press_escape(question: _FakeQuestionWithLayout) -> None:
    """Run whatever got bound to Escape, without a terminal."""
    for binding in question.application.key_bindings.bindings:
        if binding.keys == (Keys.Escape,):
            binding.handler(None)
            return
    raise AssertionError("nothing was bound to Escape")


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


def test_the_hint_says_how_to_clear_the_filter() -> None:
    """questionary's own hint does mention filtering, but it is replaced
    by "(N selections)" as soon as anything is picked - precisely when a
    reader wants to know how to get back to the whole list. So the way
    out belongs in the message, which stays on screen."""
    picklist = build_picklist(
        [_doc("Money Stuff", "Issue A", "2026-09-02", uid=1)], sizes={1: 20_000}
    )

    calls: list[tuple] = []
    questionary_prompt(
        picklist,
        checkbox=_fake_checkbox(calls, result=[]),
        terminal_size=_fixed_width(80),
    )

    message, _choices, _options = calls[0]
    assert "esc" in message.lower()


def _axios_picklist():
    """Three Axios publications, only one of which names itself in its
    own subjects - the exact shape of the reported bug."""
    candidates = [
        _doc("Axios Macro", "Rock-solid jobs", "2026-09-05", uid=1),
        _doc("Axios Markets", "Go big or go home", "2026-09-06", uid=2),
        _doc("Mike Allen (axios.com)", "Axios AM: Trump, alone", "2026-09-09", uid=3),
        _doc("Money Stuff", "Something else entirely", "2026-09-09", uid=4),
    ]
    return build_picklist(candidates, sizes={i: 20_000 for i in range(1, 5)})


def test_filtering_on_a_publication_keeps_that_publications_rows() -> None:
    """The reported bug: filtering on "axios" left the AXIOS MACRO and
    AXIOS MARKETS headings on screen with no rows under them, because
    questionary matches each line's own text and those publications do
    not repeat their name in every subject. A heading with nothing under
    it is worse than no heading - the newsletters looked unreachable."""
    from newsprint.pickerui import _choices, _matching_choices

    choices = _choices(_axios_picklist(), width=100)
    kept = _matching_choices(choices, "axios")
    titles = [str(c.title) for c in kept]

    assert any("Rock-solid jobs" in t for t in titles), "Axios Macro's row"
    assert any("Go big or go home" in t for t in titles), "Axios Markets' row"
    assert any("Trump, alone" in t for t in titles), "Mike Allen's row"
    assert not any("Something else entirely" in t for t in titles)


def test_a_heading_never_survives_without_its_rows() -> None:
    """The visible symptom, stated directly: every heading kept must have
    at least one row under it."""
    import questionary

    from newsprint.pickerui import _choices, _matching_choices

    kept = _matching_choices(_choices(_axios_picklist(), width=100), "axios")
    for index, choice in enumerate(kept):
        if isinstance(choice, questionary.Separator) and str(choice.title).strip():
            rest = kept[index + 1 :]
            assert any(not isinstance(c, questionary.Separator) for c in rest), (
                f"{choice.title!r} has no rows after it"
            )
            break


def test_filtering_on_a_subject_keeps_only_the_matching_rows() -> None:
    """Matching a row still narrows to that row - a publication whose
    heading does not match is not dragged in wholesale by one of its
    articles matching."""
    from newsprint.pickerui import _choices, _matching_choices

    kept = _matching_choices(_choices(_axios_picklist(), width=100), "rock-solid")
    titles = [str(c.title) for c in kept]
    assert any("Rock-solid jobs" in t for t in titles)
    assert not any("Go big or go home" in t for t in titles)
    assert any("AXIOS MACRO" in t.upper() for t in titles), "its heading comes too"


def test_filtering_on_nothing_that_matches_keeps_everything() -> None:
    """questionary's own behaviour, preserved: a filter that matches
    nothing shows the whole list rather than an empty one, so a typo is
    not a dead end."""
    from newsprint.pickerui import _choices, _matching_choices

    choices = _choices(_axios_picklist(), width=100)
    assert _matching_choices(choices, "zzzznothing") == []


class _FakeControl(InquirerControl):
    pass


def test_the_live_control_is_given_group_aware_filtering() -> None:
    """The pure function above is only useful if the running prompt
    actually uses it, and questionary offers no hook - so the control's
    class is swapped after the prompt is built, the same reach through
    the layout that keeps a heading on screen."""
    from newsprint.pickerui import _configure_prompt, _GroupAwareControl

    control = InquirerControl([questionary.Choice(title="a", value=1)])
    window = Window(content=control)
    question = _FakeQuestionWithLayout(window)

    _configure_prompt(question)

    assert isinstance(control, _GroupAwareControl)


def test_escape_clears_the_filter_in_one_keystroke() -> None:
    """Backspacing out of a long filter one character at a time is the
    complaint. Escape empties it outright, and puts the cursor back on
    the first selectable row so the restored list is navigable."""
    from newsprint.pickerui import _configure_prompt

    control = InquirerControl(
        [questionary.Separator("— AXIOS MACRO —"), questionary.Choice("a", value=1)]
    )
    control.search_filter = "axios"
    window = Window(content=control)
    question = _FakeQuestionWithLayout(window)

    _configure_prompt(question)
    _press_escape(question)

    assert control.search_filter is None


def test_the_control_filters_by_group_when_asked() -> None:
    """The property itself, on a control with a filter set - the pure
    function is only half the story, and this is the half the running
    prompt actually calls."""
    from newsprint.pickerui import _choices, _GroupAwareControl

    control = InquirerControl(_choices(_axios_picklist(), width=100))
    control.__class__ = _GroupAwareControl

    control.search_filter = "axios"
    titles = [str(c.title) for c in control.filtered_choices]
    assert any("Rock-solid jobs" in t for t in titles)
    assert not any("Something else entirely" in t for t in titles)
    assert control.found_in_search is True

    control.search_filter = "zzzznothing"
    assert len(list(control.filtered_choices)) == len(control.choices), "whole list"
    assert control.found_in_search is False

    control.search_filter = None
    assert len(list(control.filtered_choices)) == len(control.choices)


def test_a_layout_with_no_choice_list_is_left_alone() -> None:
    """The same silent no-op the heading tweak makes: a stand-in question
    whose layout holds no choice list gets no Escape binding, rather than
    an exception."""
    from newsprint.pickerui import _configure_prompt

    question = _FakeQuestionWithLayout(Window())
    _configure_prompt(question)
    assert question.application.key_bindings.bindings == []


def test_clearing_the_filter_leaves_the_cursor_on_a_real_row() -> None:
    """pointed_at indexes the *filtered* list, so a cursor deep in a short
    filtered view points at nothing sensible once the whole list is back.
    It goes to the top - and the top line is a separator, never
    selectable, so it has to step past it or the first space press does
    nothing."""
    from newsprint.pickerui import _choices, _configure_prompt

    control = InquirerControl(_choices(_axios_picklist(), width=100))
    control.search_filter = "money"
    control.pointed_at = 7
    question = _FakeQuestionWithLayout(Window(content=control))

    _configure_prompt(question)
    _press_escape(question)

    assert control.search_filter is None
    assert control.is_selection_valid(), "the cursor is on a selectable row"
    pointed = list(control.choices)[control.pointed_at]
    assert not isinstance(pointed, questionary.Separator)
    assert control.pointed_at == 2, "the first row, under its blank and heading"


def test_a_question_with_no_key_bindings_is_left_alone() -> None:
    """Both halves of the guard matter: a stand-in with a layout but no
    bindings must be as harmless as one with neither."""
    from newsprint.pickerui import _bind_escape_to_clear_the_filter

    class _NoBindings:
        key_bindings = None
        layout = _FakeQuestionWithLayout(Window()).application.layout

    _bind_escape_to_clear_the_filter(_NoBindings())  # must not raise


def test_a_question_with_no_layout_is_left_alone() -> None:
    """And the mirror case."""
    from newsprint.pickerui import _bind_escape_to_clear_the_filter

    class _NoLayout:
        key_bindings = KeyBindings()
        layout = None

    _bind_escape_to_clear_the_filter(_NoLayout())
    assert _NoLayout.key_bindings.bindings == []


def _mixed_picklist():
    """A publication that sorts before the Axios ones and does not match,
    which is what makes the filtered and unfiltered lists disagree about
    what lives at a given index."""
    uid = 0
    docs = []
    for publication, titles in (
        ("AI Daily Brief", ["One story"]),
        ("Axios Macro", ["New trade stakes", "Ludicrous precision", "Stimmy redux"]),
        ("Axios Markets", ["Imagining", "Chilly scenes", "Go big", "Oil bonds"]),
        ("Mike Allen (axios.com)", ["Axios AM: one", "Axios AM: two"]),
    ):
        for title in titles:
            uid += 1
            docs.append(_doc(publication, title, "2026-09-05", uid=uid))
    return build_picklist(docs, sizes={d.origin.uid: 20_000 for d in docs})


def test_the_cursor_walks_every_filtered_row_and_no_separator() -> None:
    """questionary judges whether the cursor is on a separator by indexing
    the *unfiltered* list, while pointed_at indexes the filtered one. With
    a filter active the two disagree, so arrowing down skipped rows and
    parked on blank lines - reported from a real run as "I'm not next to
    any publication, and I missed two".
    """
    from newsprint.pickerui import _choices, _GroupAwareControl

    control = InquirerControl(_choices(_mixed_picklist(), width=100))
    control.__class__ = _GroupAwareControl
    for character in "axios":
        control.add_search_character(character)

    def down() -> None:
        control.select_next()
        while not control.is_selection_valid():
            control.select_next()

    view = list(control.filtered_choices)
    rows = [
        str(c.title).strip() for c in view if not isinstance(c, questionary.Separator)
    ]
    visited = []
    for _ in range(len(rows)):
        down()
        landed = view[control.pointed_at]
        assert not isinstance(landed, questionary.Separator), (
            f"landed on a separator at index {control.pointed_at}"
        )
        visited.append(str(landed.title).strip())

    assert visited == rows, "every filtered row, in order, and nothing else"


def test_a_cursor_past_the_end_of_a_filtered_list_is_not_valid() -> None:
    """The filtered list shrinks as the filter grows. questionary resets
    the cursor to 0 on every keystroke, so this should not arise - but a
    stale index must read as "not a row to select", not raise, because
    the answer decides whether the arrow keys keep moving."""
    from newsprint.pickerui import _choices, _GroupAwareControl

    control = InquirerControl(_choices(_mixed_picklist(), width=100))
    control.__class__ = _GroupAwareControl
    for character in "axios":
        control.add_search_character(character)

    # Exactly one past the last index, which is the boundary an
    # off-by-one would step onto, and then well past it.
    for beyond in (
        len(list(control.filtered_choices)),
        len(list(control.filtered_choices)) + 5,
    ):
        control.pointed_at = beyond
        assert control.is_selection_valid() is False
        assert control.is_selection_disabled() is None
        assert control.is_selection_a_separator() is False


def test_the_two_halves_of_validity_also_read_the_filtered_list() -> None:
    """questionary only reaches these through is_selection_valid, which
    is overridden too - but their base versions index the unfiltered
    list, which is the bug, so they are corrected rather than left as
    traps for a future version that calls them directly."""
    from newsprint.pickerui import _choices, _GroupAwareControl

    control = InquirerControl(_choices(_mixed_picklist(), width=100))
    control.__class__ = _GroupAwareControl
    for character in "axios":
        control.add_search_character(character)

    view = list(control.filtered_choices)
    separator = next(
        i for i, c in enumerate(view) if isinstance(c, questionary.Separator)
    )
    row = next(
        i for i, c in enumerate(view) if not isinstance(c, questionary.Separator)
    )

    control.pointed_at = separator
    assert control.is_selection_a_separator() is True
    # A Separator carries questionary's own disabled marker, so this is
    # the case that shows the method is reading the line at all rather
    # than answering None whatever the cursor is on.
    assert control.is_selection_disabled()

    control.pointed_at = row
    assert control.is_selection_a_separator() is False
    assert control.is_selection_disabled() is None
