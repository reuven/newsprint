"""The only module that touches questionary.

picker.py builds the candidates into groups and rows - all of it plain
data, no library import, fully unit-tested there. This module's one job
is turning that data into questionary's own Separator/Choice objects and
running the checkbox prompt; `checkbox` is injectable exactly the way
printer.spool takes a `runner` and mail.Mailbox takes an `imap_factory`,
so tests drive this module's logic (which choices get built, what a
selection or a cancel returns) with a fake checkbox factory and never
touch a real terminal or prompt_toolkit's own event loop. Only the
manual pty-driven "Verify" step in tui-picker.md exercises the real
default.
"""

from collections.abc import Callable, Sequence

import questionary

from .models import Document
from .picker import Picklist

CheckboxFactory = Callable[
    [str, Sequence[questionary.Separator | questionary.Choice]], questionary.Question
]

# questionary's checkbox binds only Ctrl-C (and Ctrl-Q) to abort - Escape
# is not bound at all (verified against questionary 2.1.1's own key
# bindings in prompts/checkbox.py) - so the hint says ctrl-c, not esc.
_MESSAGE = (
    "Add any to the packet? (space to toggle, enter to confirm, ctrl-c to cancel)"
)


def _choices(
    picklist: Picklist,
) -> list[questionary.Separator | questionary.Choice]:
    choices: list[questionary.Separator | questionary.Choice] = []
    for group in picklist.groups:
        choices.append(questionary.Separator(group.publication))
        for row in group.rows:
            title = f"{row.document.title}  ({row.when})  [{row.length}]"
            choices.append(questionary.Choice(title=title, value=row.document))
    return choices


def questionary_prompt(
    picklist: Picklist,
    checkbox: CheckboxFactory = questionary.checkbox,
) -> list[Document] | None:
    """Show the scrollable checkbox and return what got picked.

    None means the user cancelled (questionary's own .ask() returns None
    on Ctrl-C) - "a way to cancel that selects nothing" from the design
    spec. An empty list means either there was nothing to offer, or the
    user confirmed with nothing checked; the caller treats both the same
    way, but the distinction from a genuine cancel is preserved here in
    case it ever matters.
    """
    choices = _choices(picklist)
    if not choices:
        return []
    return checkbox(_MESSAGE, choices).ask()
