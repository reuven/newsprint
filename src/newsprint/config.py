"""Configuration, merged over defaults.

The password is deliberately absent: it is read from the macOS Keychain at run
time, so it never appears in a config file or in the repository.
"""

import re
import sys
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .geometry import Paper, paper_by_name

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "newsprint" / "config.toml"

# Structural defaults only. Nothing here identifies a person, a mail host, or
# a printer: those come from the user's own config file. An empty printer name
# means "whatever CUPS treats as the default destination".
DEFAULTS: dict[str, dict[str, Any]] = {
    "mail": {"host": "", "user": "", "folder": "INBOX/toprint", "trash": "auto"},
    "print": {
        "printer": "",
        "paper": "A4",
        "duplex": "two-sided-long-edge",
        "cells_per_side": 4,
    },
    "layout": {"margin_mm": 9.0, "font_size_pt": 9.0, "line_height": 1.35},
    "window": {"fallback_days": 7},
    # title empty: the contents page renders nothing above "CONTENTS ·"
    # until the user sets one in their own config file. min_words: measured
    # against a real live queue - a gap of nearly 400 words separates the
    # largest teaser (124) from the smallest real article (514), so 250
    # sits comfortably in the middle with no tuning required.
    "packet": {"title": "", "min_words": 250},
    "output": {
        "directory": "~/.local/state/newsprint/packets",
        "keep_days": 30,
    },
    # Off by default: the tool must work with no API key, no network, and no
    # configuration, exactly as it did before this section existed. The key
    # itself is never stored here - only where to find it. api_key_file and
    # api_key_var name a generic dotenv path and variable name, not any
    # particular person's file; ~ is expanded at load time in load_config().
    "summary": {
        "enabled": False,
        "api_key_file": "~/.env",
        "api_key_var": "ANTHROPIC_API_KEY",
        "model": "claude-opus-5",
        # 40k input tokens is a realistic packet size, and a non-streaming
        # request over that much input can sit idle for a while during
        # the model's own thinking before any bytes come back - measured
        # against the live queue, 60s was not always enough. summarize.py
        # calls the API streamed, which avoids the idle-timeout failure
        # mode; this is headroom for the streamed call's total wall time.
        "timeout_seconds": 120.0,
    },
}


class ConfigError(Exception):
    """The configuration is missing something the run needs."""


# The keys each section's dataclass actually accepts. Checked explicitly
# against every section, uniformly - _merged() previously let [print]'s
# unknown keys through in total silence (PrintConfig is built by picking out
# known keys by name, so a typo just vanished), while an unknown [mail] or
# [layout] key blew up with a raw, unhelpful TypeError. Both were wrong, in
# different directions; this is the one behavior applied everywhere.
_SECTION_KEYS: dict[str, frozenset[str]] = {
    "mail": frozenset({"host", "user", "folder", "trash", "folders"}),
    "print": frozenset({"printer", "paper", "duplex", "cells_per_side"}),
    "layout": frozenset({"margin_mm", "font_size_pt", "line_height"}),
    "window": frozenset({"fallback_days"}),
    "packet": frozenset({"title", "min_words"}),
    "output": frozenset({"directory", "keep_days"}),
    "summary": frozenset(
        {
            "enabled",
            "api_key_file",
            "api_key_var",
            "model",
            "timeout_seconds",
            # The optional [summary.personal] sub-table, validated
            # separately by _reject_unknown_keys since it is the one place
            # config.toml nests.
            "personal",
        }
    ),
}


# [summary.personal] is the only nested table in the file. It is optional,
# and its presence is what asks for the second summary page - so there is
# no "enabled" flag inside it and no empty-string sentinel outside it:
# describe what you want flagged, or leave the whole table out.
_PERSONAL_KEYS = frozenset({"title", "looking_for"})
_DEFAULT_PERSONAL_TITLE = "Follow-ups"


def _personal(data: dict[str, Any]) -> "PersonalSummary | None":
    raw = data.get("personal")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("[summary.personal] must be a table")
    looking_for = str(raw.get("looking_for", "")).strip()
    if not looking_for:
        raise ConfigError(
            "[summary.personal] needs looking_for: describe what you want "
            "flagged in your reading, or remove the whole section"
        )
    return PersonalSummary(
        title=str(raw.get("title", _DEFAULT_PERSONAL_TITLE)),
        looking_for=looking_for,
    )


def _reject_unknown_keys(data: dict[str, dict[str, Any]]) -> None:
    unknown_sections = sorted(set(data) - set(_SECTION_KEYS))
    if unknown_sections:
        valid = ", ".join(sorted(_SECTION_KEYS))
        raise ConfigError(
            f"unknown section(s): {', '.join(unknown_sections)}. "
            f"Valid sections are: {valid}"
        )
    for section, allowed in _SECTION_KEYS.items():
        unknown = sorted(set(data.get(section, {})) - allowed)
        if unknown:
            raise ConfigError(f"unknown key(s) in [{section}]: {', '.join(unknown)}")
    personal = data.get("summary", {}).get("personal")
    if isinstance(personal, dict):
        unknown = sorted(set(personal) - _PERSONAL_KEYS)
        if unknown:
            raise ConfigError(
                f"unknown key(s) in [summary.personal]: {', '.join(unknown)}"
            )


@dataclass(frozen=True, slots=True)
class MailConfig:
    """The account, and the folders newsletters are starred into.

    `folder` is the original spelling and is in every config that exists,
    including the one --setup writes, so it keeps working and means a
    list of one. `folders` is how you name more than one. A uid means
    nothing without the folder it came from, so everything downstream
    carries the folder alongside it rather than a bare number.
    """

    host: str
    user: str
    folder: str
    trash: str
    folders: list[str]


@dataclass(frozen=True, slots=True)
class PrintConfig:
    printer: str
    paper: Paper
    duplex: str


@dataclass(frozen=True, slots=True)
class LayoutConfig:
    margin_mm: float
    font_size_pt: float
    line_height: float


@dataclass(frozen=True, slots=True)
class PacketConfig:
    title: str
    min_words: int


@dataclass(frozen=True, slots=True)
class OutputConfig:
    """Where a packet goes when --output says nothing, and how long it
    stays there.

    A packet outlives the run that made it: spool() returning success
    means CUPS accepted the job, not that paper came out right, and by
    the time anyone knows otherwise the mail has been retired. So the
    default home is somewhere durable rather than a temp directory - and
    then it needs sweeping, or it becomes another thing to tidy.
    """

    directory: Path
    keep_days: int


@dataclass(frozen=True, slots=True)
class PersonalSummary:
    """The optional second summary page: a heading, and what to look for.

    `looking_for` is free text handed to the model, not a setting - the
    useful version of this page is specific to one reader, and was
    hardcoded to the author's own newsletter until newsprint became
    something other people install.
    """

    title: str
    looking_for: str


@dataclass(frozen=True, slots=True)
class SummaryConfig:
    """Where to find the API key, never the key itself.

    api_key_file and api_key_var say which file and which variable name to
    read the Claude API key from at run time - the key never appears in
    this dataclass, in config.toml, or anywhere else tracked. See
    summarize.read_api_key().
    """

    enabled: bool
    api_key_file: Path
    api_key_var: str
    model: str
    timeout_seconds: float
    # None when [summary.personal] is absent, which is the default and
    # means only the topics page is produced.
    personal: PersonalSummary | None = None


@dataclass(frozen=True, slots=True)
class Config:
    mail: MailConfig
    printing: PrintConfig
    layout: LayoutConfig
    fallback_days: int
    packet: PacketConfig
    output: OutputConfig
    summary: SummaryConfig
    path: Path

    def require_mail(self) -> None:
        """Fail early, and say exactly what to put where.

        Checked at the point of use rather than at load time, so that the
        parts of the tool that never touch mail stay usable without a
        configured account.
        """
        missing = [name for name in ("host", "user") if not getattr(self.mail, name)]
        if missing:
            keys = " and ".join(f"mail.{name}" for name in missing)
            raise ConfigError(
                f"{self.path}: {keys} must be set. "
                f"Copy config.example.toml to {self.path} and fill it in."
            )


def _folders(mail: dict[str, Any], written: dict[str, Any], path: Path) -> list[str]:
    """The folders to read.

    `folders` is the setting, and takes either a name or a list of them -
    most people read from one, and making them wrap it in brackets to say
    so is a tax on the common case.

    `folder` is what every config written before this says, including the
    one --setup used to write. It means the same thing and keeps working.
    Naming both is a config half-edited, and the plural wins.
    But it is said out loud, because the likely way to end up here is
    adding `folders` to a working config meaning "also read this one" -
    and then the folder the tool has been reading all along disappears
    from the packet with nothing to explain it.
    """
    named = mail.get("folders")
    if named is None:
        if "folder" in written:
            print(
                f"{path}: [mail] folder is deprecated; rename it to folders, "
                "which takes one name as readily as a list.",
                file=sys.stderr,
            )
        return [mail["folder"]]
    if isinstance(named, str):
        named = [named]
    if not isinstance(named, list) or not all(isinstance(x, str) for x in named):
        raise ConfigError(
            f"{path}: mail.folders must be a folder name or a list of them"
        )
    if "folder" in written:
        print(
            f"{path}: [mail] names both folder and folders; folder is "
            "deprecated and is being ignored. Reading "
            f"{', '.join(named) if isinstance(named, list) else named} and "
            f"ignoring folder = {written['folder']!r}. "
            "Put it in the folders list to read it too.",
            file=sys.stderr,
        )
    if not named:
        raise ConfigError(f"{path}: mail.folders needs at least one folder")
    return list(named)


# `folder = ...` as the first thing on a line, allowing for the leading
# whitespace TOML permits, and with whatever spacing the writer used
# before the `=`. Anchored so a commented-out line, or the word inside a
# value or a comment, is never touched.
_FOLDER_ASSIGNMENT = re.compile(r"^([ \t]*)(folder)([ \t]*)(=.*)$")


def rename_folder_key(path: Path) -> bool:
    """Rename a `folder = ...` line to `folders = ...` in place.

    A line edit rather than a parse and re-serialize: a config file is
    hand-written and full of comments, and tomllib cannot write at all -
    anything that round-tripped through a TOML writer would hand the
    reader back a file stripped of everything they had explained to
    themselves in it.

    The key gets one character longer and nothing else on the line moves,
    so a column-aligned file ends up one character out on that line. That
    is deliberate: silently eating a space to preserve the columns is a
    second change nobody asked for, and the alternative is more
    surprising than a nudged `=`.

    Returns whether anything changed.
    """
    try:
        original = path.read_text()
    except OSError:
        return False
    lines = original.split("\n")
    for index, line in enumerate(lines):
        match = _FOLDER_ASSIGNMENT.match(line)
        if match is None:
            continue
        indent, _key, spacing, rest = match.groups()
        lines[index] = indent + "folders" + spacing + rest
        path.write_text("\n".join(lines))
        return True
    return False


def _merged(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Overlay the file's tables onto the defaults, key by key.

    Merging per key rather than per table matters: a config that sets only
    `font_size_pt` must not discard the default margin and line height.

    Returns what the file actually said alongside the merge, because a
    merged value cannot be told from a default one - and `folder` has a
    default, so "did they write this down?" is a question only the raw
    file can answer.
    """
    merged = {section: dict(values) for section, values in DEFAULTS.items()}
    if not path.exists():
        return merged, {}
    loaded = tomllib.loads(path.read_text())
    for section, values in loaded.items():
        merged.setdefault(section, {}).update(values)
    return merged, loaded


def load_config(
    path: Path = DEFAULT_CONFIG_PATH,
    paper_override: str | None = None,
    cells_override: int | None = None,
) -> Config:
    """Load and validate config.toml, raising only ConfigError.

    Malformed TOML (tomllib.TOMLDecodeError), an unknown paper name
    (ValueError from paper_by_name), and a mistyped or unknown key in any
    section (rejected explicitly by _reject_unknown_keys, uniformly across
    [mail], [print], [layout], and [window]) are all real possibilities in
    a hand-edited file. Wrapping them here means cli.py's single
    `except (MailError, ConfigError)` handler can catch every config
    problem the same way, instead of the load itself needing its own
    special case to avoid an unhandled traceback.
    """
    try:
        data, written = _merged(path)
        _reject_unknown_keys(data)
        paper_name = (
            paper_override if paper_override is not None else data["print"]["paper"]
        )
        return Config(
            mail=MailConfig(
                host=data["mail"]["host"],
                user=data["mail"]["user"],
                folder=data["mail"]["folder"],
                trash=data["mail"]["trash"],
                folders=_folders(data["mail"], written.get("mail", {}), path),
            ),
            printing=PrintConfig(
                printer=data["print"]["printer"],
                paper=replace(
                    paper_by_name(paper_name),
                    cells_per_side=(
                        cells_override
                        if cells_override is not None
                        else data["print"]["cells_per_side"]
                    ),
                ),
                duplex=data["print"]["duplex"],
            ),
            layout=LayoutConfig(**data["layout"]),
            fallback_days=data["window"]["fallback_days"],
            packet=PacketConfig(**data["packet"]),
            output=OutputConfig(
                directory=Path(data["output"]["directory"]).expanduser(),
                keep_days=data["output"]["keep_days"],
            ),
            summary=SummaryConfig(
                enabled=data["summary"]["enabled"],
                api_key_file=Path(data["summary"]["api_key_file"]).expanduser(),
                api_key_var=data["summary"]["api_key_var"],
                model=data["summary"]["model"],
                timeout_seconds=data["summary"]["timeout_seconds"],
                personal=_personal(data["summary"]),
            ),
            path=path,
        )
    except (ValueError, TypeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{path}: {error}") from error


DEFAULT_PUBLICATIONS_PATH = Path.home() / ".config" / "newsprint" / "publications.toml"


@dataclass(frozen=True, slots=True)
class PublicationNames:
    """The two ways publications.toml can rename a publication.

    List-Id identifies the newsletter; the address only identifies the
    sending system - one address can send many distinct newsletters (the
    New York Times sends The Morning, Cooking, DealBook, and its named
    columnists all from nytdirect@nytimes.com). extract._publication()
    resolves a List-Id-keyed override before List-Id itself, and only
    falls back to an address-keyed override when a message carries no
    List-Id at all - so an address override can no longer blur every
    newsletter from a shared address into one name.
    """

    by_address: dict[str, str]
    by_list_id: dict[str, str]


def load_publication_names(
    path: Path = DEFAULT_PUBLICATIONS_PATH,
) -> PublicationNames:
    """Load both override tables from publications.toml.

    Both keys are compared in lower case, because senders (and users
    copying a List-Id by hand) are inconsistent about capitalization and
    the mapping should not be.
    """
    if not path.exists():
        return PublicationNames(by_address={}, by_list_id={})
    data = tomllib.loads(path.read_text())
    by_address = {
        address.lower(): name for address, name in data.get("names", {}).items()
    }
    by_list_id = {
        list_id.lower(): name for list_id, name in data.get("list_id_names", {}).items()
    }
    return PublicationNames(by_address=by_address, by_list_id=by_list_id)
