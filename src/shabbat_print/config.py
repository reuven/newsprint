"""Configuration, merged over defaults.

The password is deliberately absent: it is read from the macOS Keychain at run
time, so it never appears in a config file or in the repository.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .geometry import Paper, paper_by_name

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "shabbat-print" / "config.toml"

# Structural defaults only. Nothing here identifies a person, a mail host, or
# a printer: those come from the user's own config file. An empty printer name
# means "whatever CUPS treats as the default destination".
DEFAULTS: dict[str, dict[str, Any]] = {
    "mail": {"host": "", "user": "", "folder": "INBOX/toprint", "trash": "auto"},
    "print": {"printer": "", "paper": "A4", "duplex": "two-sided-long-edge"},
    "layout": {"margin_mm": 9.0, "font_size_pt": 9.0, "line_height": 1.35},
    "window": {"fallback_days": 7},
    # title empty: the contents page renders nothing above "CONTENTS ·"
    # until the user sets one in their own config file. min_words: measured
    # against a real live queue - a gap of nearly 400 words separates the
    # largest teaser (124) from the smallest real article (514), so 250
    # sits comfortably in the middle with no tuning required.
    "packet": {"title": "", "min_words": 250},
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
# different directions; this is the one behaviour applied everywhere.
_SECTION_KEYS: dict[str, frozenset[str]] = {
    "mail": frozenset({"host", "user", "folder", "trash"}),
    "print": frozenset({"printer", "paper", "duplex"}),
    "layout": frozenset({"margin_mm", "font_size_pt", "line_height"}),
    "window": frozenset({"fallback_days"}),
    "packet": frozenset({"title", "min_words"}),
    "summary": frozenset(
        {"enabled", "api_key_file", "api_key_var", "model", "timeout_seconds"}
    ),
}


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


@dataclass(frozen=True, slots=True)
class MailConfig:
    host: str
    user: str
    folder: str
    trash: str


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


@dataclass(frozen=True, slots=True)
class Config:
    mail: MailConfig
    printing: PrintConfig
    layout: LayoutConfig
    fallback_days: int
    packet: PacketConfig
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


def _merged(path: Path) -> dict[str, dict[str, Any]]:
    """Overlay the file's tables onto the defaults, key by key.

    Merging per key rather than per table matters: a config that sets only
    `font_size_pt` must not discard the default margin and line height.
    """
    merged = {section: dict(values) for section, values in DEFAULTS.items()}
    if not path.exists():
        return merged
    loaded = tomllib.loads(path.read_text())
    for section, values in loaded.items():
        merged.setdefault(section, {}).update(values)
    return merged


def load_config(
    path: Path = DEFAULT_CONFIG_PATH, paper_override: str | None = None
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
        data = _merged(path)
        _reject_unknown_keys(data)
        paper_name = (
            paper_override if paper_override is not None else data["print"]["paper"]
        )
        return Config(
            mail=MailConfig(**data["mail"]),
            printing=PrintConfig(
                printer=data["print"]["printer"],
                paper=paper_by_name(paper_name),
                duplex=data["print"]["duplex"],
            ),
            layout=LayoutConfig(**data["layout"]),
            fallback_days=data["window"]["fallback_days"],
            packet=PacketConfig(**data["packet"]),
            summary=SummaryConfig(
                enabled=data["summary"]["enabled"],
                api_key_file=Path(data["summary"]["api_key_file"]).expanduser(),
                api_key_var=data["summary"]["api_key_var"],
                model=data["summary"]["model"],
                timeout_seconds=data["summary"]["timeout_seconds"],
            ),
            path=path,
        )
    except (ValueError, TypeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"{path}: {error}") from error


DEFAULT_PUBLICATIONS_PATH = (
    Path.home() / ".config" / "shabbat-print" / "publications.toml"
)


def load_publication_names(
    path: Path = DEFAULT_PUBLICATIONS_PATH,
) -> dict[str, str]:
    """Map a sender address to the name that should appear on the cell.

    Addresses are compared in lower case, because senders are inconsistent
    about capitalisation and the mapping should not be.
    """
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text())
    return {address.lower(): name for address, name in data.get("names", {}).items()}
