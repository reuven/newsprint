from pathlib import Path

import pytest

from shabbat_print.config import load_config
from shabbat_print.geometry import A4, LETTER

SAMPLE = """
[mail]
host = "imap.example.com"
user = "someone@example.com"
folder = "INBOX/queue"

[print]
printer = "Test_Printer"
paper = "Letter"

[layout]
font_size_pt = 10.0
"""


def test_missing_file_yields_structural_defaults(tmp_path: Path) -> None:
    """Defaults describe the layout, never a person."""
    config = load_config(tmp_path / "absent.toml")
    assert config.mail.host == ""
    assert config.mail.user == ""
    assert config.printing.printer == ""
    assert config.mail.folder == "INBOX/toprint"
    assert config.printing.paper is A4
    assert config.layout.margin_mm == pytest.approx(9.0)
    assert config.fallback_days == 7


def test_no_personal_data_hides_in_the_defaults() -> None:
    """A guard for the open-source goal: catch a stray address or hostname."""
    import re

    from shabbat_print.config import DEFAULTS

    flattened = repr(DEFAULTS)
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", flattened)
    assert "emailsrvr" not in flattened
    assert not re.search(r"imap\.\w", flattened)


def test_require_mail_names_what_is_missing(tmp_path: Path) -> None:
    from shabbat_print.config import ConfigError

    config = load_config(tmp_path / "absent.toml")
    with pytest.raises(ConfigError, match="mail.host and mail.user"):
        config.require_mail()


def test_require_mail_passes_when_configured(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    load_config(path).require_mail()  # must not raise


def test_file_values_override_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    config = load_config(path)
    assert config.mail.host == "imap.example.com"
    assert config.mail.folder == "INBOX/queue"
    assert config.printing.printer == "Test_Printer"
    assert config.printing.paper is LETTER
    assert config.layout.font_size_pt == pytest.approx(10.0)


def test_unspecified_keys_keep_their_defaults(tmp_path: Path) -> None:
    """A partial [layout] table must not wipe out the other layout keys."""
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    config = load_config(path)
    assert config.layout.margin_mm == pytest.approx(9.0)
    assert config.layout.line_height == pytest.approx(1.35)


def test_paper_override_beats_the_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    config = load_config(path, paper_override="a4")
    assert config.printing.paper is A4


def test_unknown_paper_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[print]\npaper = "foolscap"\n')
    with pytest.raises(ValueError, match="unknown paper"):
        load_config(path)
