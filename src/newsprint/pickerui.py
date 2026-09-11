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
# here, in the message, which stays on screen throughout. Backspacing
# past the first character is that way out: Escape is not bound at all
# (see the note below on ctrl-c), and there is no other clear key.
_MESSAGE = (
    "Add any to the packet? (type to filter, backspace to clear; "
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


# How many lines of context to keep above the cursor. layout_picklist
# emits a blank spacer then a ruled heading before each group's rows, so
# two is what it takes for the publication name to still be on screen
# when the cursor sits on that group's first row.
_LINES_ABOVE_CURSOR = 2


def _keep_group_heading_visible(question: object) -> None:
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
    application = getattr(question, "application", None)
    layout = getattr(application, "layout", None)
    if layout is None:
        return
    for container in layout.walk():
        if isinstance(container, Window) and isinstance(
            container.content, InquirerControl
        ):
            container.scroll_offsets = ScrollOffsets(top=_LINES_ABOVE_CURSOR)


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
    _keep_group_heading_visible(question)
    # .ask() is untyped upstream; the cast states the contract this
    # module's own signature already promises.
    return cast("list[Document] | None", question.ask())
