"""The only module that touches questionary.

picker.py builds the candidates into groups, rows, and fully laid-out
lines (layout_picklist: blank spacers, ruled headings, column-aligned
rows) - all of it plain data, no library import, fully unit-tested
there. This module's one job is turning that data into questionary's
own Separator/Choice objects and running the checkbox prompt; `checkbox`
is injectable exactly the way printer.spool takes a `runner` and
mail.Mailbox takes an `imap_factory`, so tests drive this module's logic
(which choices get built, what a selection or a cancel returns) with a
fake checkbox factory and never touch a real terminal or prompt_toolkit's
own event loop. `terminal_size` is injected the same way, so a test can
drive any width without a real terminal either. Only the manual
pty-driven "Verify" step in picker-layout.md exercises the real
defaults.
"""

import os
import shutil
from collections.abc import Callable, Sequence
from typing import Protocol, cast

import questionary
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import ScrollOffsets, Window
from questionary.prompts.common import InquirerControl

from .models import Document
from .picker import Picklist, layout_picklist


class CheckboxFactory(Protocol):
    """questionary.checkbox, or a test's stand-in for it.

    A Protocol rather than a Callable alias because the prompt is
    configured with keyword options (see questionary_prompt), and a
    Callable alias cannot describe those.
    """

    def __call__(
        self,
        message: str,
        choices: Sequence[questionary.Separator | questionary.Choice],
        *,
        use_search_filter: bool = ...,
        use_jk_keys: bool = ...,
    ) -> questionary.Question: ...


TerminalSizeFactory = Callable[[], os.terminal_size]

# questionary's checkbox binds only Ctrl-C (and Ctrl-Q) to abort - Escape
# is not bound at all (verified against questionary 2.1.1's own key
# bindings in prompts/checkbox.py) - so the hint says ctrl-c, not esc.
# questionary prints a hint of its own, and it does mention filtering -
# but only while nothing is selected; the moment something is, it is
# replaced by "(N selections)". That is exactly when a reader starts
# wondering how to get back to the whole list, so the way out has to be
# here, in the message, which stays on screen throughout. Escape is that
# way out - bound by this module, not by questionary, which binds no
# Escape at all (see the note below on ctrl-c).
_MESSAGE = (
    "Add any to the packet? (type to filter, esc to clear; "
    "space to toggle, enter to confirm, ctrl-c to cancel)"
)


def _choices(
    picklist: Picklist, width: int
) -> list[questionary.Separator | questionary.Choice]:
    """Map picker.layout_picklist's plain-data lines onto questionary's
    own objects - no formatting decision of its own: a "blank" or
    "heading" line becomes a Separator (see layout_picklist's own
    docstring for why both do - questionary's checkbox already skips
    every Separator, blank or not, so neither is ever selectable or
    reachable by the cursor); a "row" line becomes a Choice carrying the
    real Document as its value.
    """
    choices: list[questionary.Separator | questionary.Choice] = []
    for line in layout_picklist(picklist, width):
        if line.kind == "row":
            assert line.document is not None  # guaranteed by layout_picklist
            choices.append(questionary.Choice(title=line.text, value=line.document))
        else:
            choices.append(questionary.Separator(line.text))
    return choices


def _matching_choices(
    choices: Sequence[questionary.Separator | questionary.Choice], needle: str
) -> list[questionary.Separator | questionary.Choice]:
    """The choices a filter of `needle` should leave on screen, by group.

    questionary matches each line's own text, which is the wrong unit
    here: a row's text is the article's subject, and the publication it
    belongs to is named once, in the heading above it. Filtering on
    "axios" that way keeps the AXIOS MACRO and AXIOS MARKETS headings -
    their own text matches - and drops every row beneath them, because
    those publications do not repeat their name in every subject. The
    result is a heading with nothing under it, which reads as a
    newsletter you cannot reach.

    So the group is the unit. A group whose heading matches keeps all of
    its rows; otherwise it keeps the rows that match themselves, and its
    heading comes with them for context. A group with nothing left is
    dropped entirely, heading and all.
    """
    kept: list[questionary.Separator | questionary.Choice] = []
    separators: list[questionary.Separator | questionary.Choice] = []
    rows: list[questionary.Separator | questionary.Choice] = []
    heading_matched = False

    def flush() -> None:
        nonlocal heading_matched
        wanted = (
            list(rows)
            if heading_matched
            else [row for row in rows if needle in str(row.title).lower()]
        )
        if wanted:
            kept.extend(separators)
            kept.extend(wanted)
        separators.clear()
        rows.clear()
        heading_matched = False

    for choice in choices:
        if isinstance(choice, questionary.Separator):
            if rows:
                flush()
            separators.append(choice)
            heading_matched = heading_matched or needle in str(choice.title).lower()
        else:
            rows.append(choice)
    flush()
    return kept


class _GroupAwareControl(InquirerControl):
    """questionary's own control, with filtering done by group.

    The class of a live control is swapped to this one after the prompt
    is built - questionary exposes no hook for the filtering rule, and
    the attributes are the base class's own, untouched. The same reach
    through the layout that _keep_group_heading_visible already makes.
    """

    @property
    def filtered_choices(
        self,
    ) -> Sequence[questionary.Separator | questionary.Choice]:
        if not self.search_filter:
            return cast("Sequence[questionary.Choice]", self.choices)
        kept = _matching_choices(self.choices, self.search_filter.lower())
        self.found_in_search = bool(kept)
        # questionary's own fallback, kept: a filter matching nothing
        # shows the whole list rather than an empty one, so a typo is
        # never a dead end.
        return kept or cast("Sequence[questionary.Choice]", self.choices)


# How many lines of context to keep above the cursor. layout_picklist
# emits a blank spacer then a ruled heading before each group's rows, so
# two is what it takes for the publication name to still be on screen
# when the cursor sits on that group's first row.
_LINES_ABOVE_CURSOR = 2


def _clear_the_filter(control: InquirerControl) -> None:
    """Empty the search filter and put the cursor somewhere navigable.

    pointed_at is an index into the *filtered* list, so a cursor deep in
    a short filtered view would land somewhere arbitrary once the whole
    list came back. Sending it to the top is the only answer that is
    right regardless of what was filtered.
    """
    control.search_filter = None
    control.pointed_at = 0
    if not control.is_selection_valid():
        control.select_next()


def _configure_prompt(question: object) -> None:
    """Apply the tweaks questionary exposes no options for.

    Silently does nothing for anything that is not a real questionary
    Question, which is what lets the injected fake checkbox factories in
    the tests stay simple stubs.
    """
    application = getattr(question, "application", None)
    _keep_group_heading_visible(application)
    _bind_escape_to_clear_the_filter(application)


def _bind_escape_to_clear_the_filter(application: object) -> None:
    """Escape empties the filter outright.

    questionary binds no Escape at all, so backspacing is otherwise the
    only way out and a long filter costs a keystroke a character. The
    binding is deliberately not eager: Escape is the first byte of every
    arrow-key sequence, and an eager binding would swallow them.
    """
    bindings = getattr(application, "key_bindings", None)
    layout = getattr(application, "layout", None)
    if bindings is None or layout is None:
        return
    controls = [
        container.content
        for container in layout.walk()
        if isinstance(container, Window)
        and isinstance(container.content, InquirerControl)
    ]
    if not controls:
        return
    control = controls[0]

    def clear(_event: object) -> None:
        _clear_the_filter(control)

    # Applied as a call rather than as decorator syntax: questionary's
    # own bindings object is untyped upstream, and mypy --strict rejects
    # an untyped decorator sitting on a typed function.
    bindings.add(Keys.Escape)(clear)


def _keep_group_heading_visible(application: object) -> None:
    """Stop a group's heading scrolling off when the cursor reaches it.

    prompt_toolkit's Window only guarantees the *cursor* line is visible.
    Scrolling back up therefore stops as soon as the cursor is on screen,
    which leaves it flush against the top edge - and the heading naming
    the publication, one line above it, off screen. Pressing up from the
    first row and then down again reproduced exactly that: the cursor
    correctly back on the first article, its "AXIOS MACRO" heading gone.

    ScrollOffsets is prompt_toolkit's own answer: a top offset makes the
    window keep that many lines above the cursor on screen, so the
    heading is carried along with the row it belongs to. questionary
    builds the window itself and exposes no option for this, so it is
    reached through the layout after construction - the one Window whose
    content is the choice list.

    Silently does nothing for anything that is not a real questionary
    Question, which is what lets the injected fake checkbox factories in
    the tests stay simple stubs.
    """
    layout = getattr(application, "layout", None)
    if layout is None:
        return
    for container in layout.walk():
        if isinstance(container, Window) and isinstance(
            container.content, InquirerControl
        ):
            container.scroll_offsets = ScrollOffsets(top=_LINES_ABOVE_CURSOR)
            # Filtering by group, which questionary offers no hook for -
            # see _GroupAwareControl. The attributes are the base
            # class's own, so nothing else about the control changes.
            container.content.__class__ = _GroupAwareControl


def questionary_prompt(
    picklist: Picklist,
    checkbox: CheckboxFactory = questionary.checkbox,
    terminal_size: TerminalSizeFactory = shutil.get_terminal_size,
) -> list[Document] | None:
    """Show the scrollable checkbox and return what got picked.

    None means the user cancelled (questionary's own .ask() returns None
    on Ctrl-C) - "a way to cancel that selects nothing" from the design
    spec. An empty list means either there was nothing to offer, or the
    user confirmed with nothing checked; the caller treats both the same
    way, but the distinction from a genuine cancel is preserved here in
    case it ever matters.
    """
    width = terminal_size().columns
    choices = _choices(picklist, width)
    if not choices:
        return []
    question = checkbox(
        _MESSAGE,
        choices,
        # --since can open the window to weeks, and a window that wide
        # runs to a hundred candidates - more than anyone wants to scroll
        # to find one publication. questionary refuses the filter and j/k
        # navigation together, reasonably: j and k are letters the filter
        # needs to receive. The arrow keys still move the cursor.
        use_search_filter=True,
        use_jk_keys=False,
    )
    _configure_prompt(question)
    # .ask() is untyped upstream; the cast states the contract this
    # module's own signature already promises.
    return cast("list[Document] | None", question.ask())
