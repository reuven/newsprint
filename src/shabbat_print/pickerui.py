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

import questionary

from .models import Document
from .picker import Picklist, layout_picklist

CheckboxFactory = Callable[
    [str, Sequence[questionary.Separator | questionary.Choice]], questionary.Question
]
TerminalSizeFactory = Callable[[], os.terminal_size]

# questionary's checkbox binds only Ctrl-C (and Ctrl-Q) to abort - Escape
# is not bound at all (verified against questionary 2.1.1's own key
# bindings in prompts/checkbox.py) - so the hint says ctrl-c, not esc.
_MESSAGE = (
    "Add any to the packet? (space to toggle, enter to confirm, ctrl-c to cancel)"
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
    return checkbox(_MESSAGE, choices).ask()
