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
}


class ConfigError(Exception):
    """The configuration is missing something the run needs."""


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
class Config:
    mail: MailConfig
    printing: PrintConfig
    layout: LayoutConfig
    fallback_days: int
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
    data = _merged(path)
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
        path=path,
    )


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
