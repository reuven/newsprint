from pathlib import Path

import pytest

from newsprint.config import ConfigError, load_config
from newsprint.geometry import A4, LETTER

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
    assert config.printing.paper == A4
    assert config.layout.margin_mm == pytest.approx(9.0)
    assert config.fallback_days == 7
    assert config.packet.title == ""
    assert config.packet.min_words == 250
    assert config.summary.enabled is False
    assert config.summary.api_key_file == Path.home() / ".env"
    assert config.summary.api_key_var == "ANTHROPIC_API_KEY"
    assert config.summary.model == "claude-opus-5"
    assert config.summary.timeout_seconds == pytest.approx(120.0)


def test_no_personal_data_hides_in_the_defaults() -> None:
    """A guard for the open-source goal: catch a stray address or hostname."""
    import re

    from newsprint.config import DEFAULTS

    flattened = repr(DEFAULTS)
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", flattened)
    assert "emailsrvr" not in flattened
    assert not re.search(r"imap\.\w", flattened)


def test_require_mail_names_what_is_missing(tmp_path: Path) -> None:
    from newsprint.config import ConfigError

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
    assert config.printing.paper == LETTER
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
    assert config.printing.paper == A4


def test_unknown_paper_is_rejected(tmp_path: Path) -> None:
    """load_config must wrap this as a ConfigError, not let the raw
    ValueError from paper_by_name() escape - cli.py only catches
    (MailError, ConfigError), so anything else becomes an unhandled
    traceback in the user's terminal instead of a clean error message.
    """
    from newsprint.config import ConfigError

    path = tmp_path / "config.toml"
    path.write_text('[print]\npaper = "foolscap"\n')
    with pytest.raises(ConfigError, match="unknown paper"):
        load_config(path)


def test_an_unknown_print_key_is_rejected(tmp_path: Path) -> None:
    """_merged() previously accepted unknown TOML in [print] silently -
    PrintConfig is built by picking out three known keys by name, so a
    typo like "prnter" just vanished with no error at all. Now it must be
    rejected the same way an unknown [mail] or [layout] key is."""
    from newsprint.config import ConfigError

    path = tmp_path / "config.toml"
    path.write_text('[print]\nprnter = "Office"\n')
    with pytest.raises(ConfigError, match="prnter"):
        load_config(path)


def test_an_unknown_section_is_rejected(tmp_path: Path) -> None:
    """_reject_unknown_keys only ever iterated its own known sections, so a
    mistyped section header like [prnt] was never looked at: the section
    just vanished and the run silently fell back to every default in
    [print], including the printer. This is the same silent-typo failure
    the key-level check already guards against, one level up - a bad
    section name must be named, alongside the valid ones."""
    from newsprint.config import ConfigError

    path = tmp_path / "config.toml"
    path.write_text('[prnt]\nprinter = "Office"\n')
    with pytest.raises(ConfigError) as exc_info:
        load_config(path)
    message = str(exc_info.value)
    assert "prnt" in message
    for valid_section in ("mail", "print", "layout", "window", "packet", "summary"):
        assert valid_section in message


def test_an_unknown_mail_key_names_the_key_clearly(tmp_path: Path) -> None:
    """[mail] and [layout] raised a raw, unhelpful TypeError for an
    unknown key ("unexpected keyword argument"); now it must be the same
    clear ConfigError as every other section."""
    from newsprint.config import ConfigError

    path = tmp_path / "config.toml"
    path.write_text('[mail]\nhost = "imap.example.com"\nusr = "typo@example.com"\n')
    with pytest.raises(ConfigError, match="usr"):
        load_config(path)


def test_an_unknown_layout_key_is_rejected(tmp_path: Path) -> None:
    from newsprint.config import ConfigError

    path = tmp_path / "config.toml"
    path.write_text("[layout]\nmargn_mm = 9.0\n")
    with pytest.raises(ConfigError, match="margn_mm"):
        load_config(path)


def test_an_unknown_window_key_is_rejected(tmp_path: Path) -> None:
    """[window] had the same silent-acceptance bug as [print]: its only
    key is read by direct indexing (data["window"]["fallback_days"]),
    never splatted into a dataclass, so nothing ever checked the rest."""
    from newsprint.config import ConfigError

    path = tmp_path / "config.toml"
    path.write_text("[window]\nfallback_dyas = 7\n")
    with pytest.raises(ConfigError, match="fallback_dyas"):
        load_config(path)


def test_an_unknown_packet_key_is_rejected(tmp_path: Path) -> None:
    from newsprint.config import ConfigError

    path = tmp_path / "config.toml"
    path.write_text('[packet]\ntilte = "Oops"\n')
    with pytest.raises(ConfigError, match="tilte"):
        load_config(path)


def test_packet_title_and_threshold_are_configurable(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[packet]\ntitle = "Family Shabbat Reading"\nmin_words = 200\n')
    config = load_config(path)
    assert config.packet.title == "Family Shabbat Reading"
    assert config.packet.min_words == 200


def test_malformed_toml_is_rejected_as_a_config_error(tmp_path: Path) -> None:
    """tomllib.TOMLDecodeError must not escape as a bare exception either."""
    from newsprint.config import ConfigError

    path = tmp_path / "config.toml"
    path.write_text("this is not = = valid toml")
    with pytest.raises(ConfigError):
        load_config(path)


def test_an_unknown_summary_key_is_rejected(tmp_path: Path) -> None:
    from newsprint.config import ConfigError

    path = tmp_path / "config.toml"
    path.write_text("[summary]\nenbled = true\n")
    with pytest.raises(ConfigError, match="enbled"):
        load_config(path)


def test_summary_settings_are_configurable_and_the_home_tilde_is_expanded(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[summary]\n"
        "enabled = true\n"
        'api_key_file = "~/.secrets/anthropic.env"\n'
        'api_key_var = "MY_KEY"\n'
        'model = "claude-sonnet-5"\n'
        "timeout_seconds = 30.0\n"
    )
    config = load_config(path)
    assert config.summary.enabled is True
    assert config.summary.api_key_file == Path.home() / ".secrets" / "anthropic.env"
    assert config.summary.api_key_var == "MY_KEY"
    assert config.summary.model == "claude-sonnet-5"
    assert config.summary.timeout_seconds == pytest.approx(30.0)


def test_publication_names_default_to_empty(tmp_path: Path) -> None:
    from newsprint.config import PublicationNames, load_publication_names

    assert load_publication_names(tmp_path / "absent.toml") == PublicationNames(
        by_address={}, by_list_id={}
    )


def test_publication_names_are_lowercased(tmp_path: Path) -> None:
    from newsprint.config import load_publication_names

    path = tmp_path / "publications.toml"
    path.write_text('[names]\n"NoReply@News.Bloomberg.com" = "Money Stuff"\n')
    names = load_publication_names(path)
    assert names.by_address == {"noreply@news.bloomberg.com": "Money Stuff"}
    assert names.by_list_id == {}


def test_publication_names_reads_list_id_table(tmp_path: Path) -> None:
    """List-Id identifies the newsletter, not the sending address - the
    NYT sends many distinct newsletters from one address, so
    publications.toml needs a way to key an override by List-Id too."""
    from newsprint.config import load_publication_names

    path = tmp_path / "publications.toml"
    path.write_text(
        '[names]\n"nytdirect@nytimes.com" = "NYT"\n\n'
        '[list_id_names]\n"Jamelle Bouie" = "Jamelle Bouie"\n'
        '"the veggie" = "The Veggie"\n'
    )
    names = load_publication_names(path)
    assert names.by_address == {"nytdirect@nytimes.com": "NYT"}
    assert names.by_list_id == {
        "jamelle bouie": "Jamelle Bouie",
        "the veggie": "The Veggie",
    }


# ---------------------------------------------------------------------------
# [summary.personal] - the one nested table in the file. Its presence is
# what asks for the second summary page, so there is no flag inside it and
# no empty-string sentinel outside it.
# ---------------------------------------------------------------------------


def _summary_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(f"[summary]\nenabled = true\n{body}")
    return path


def test_no_personal_section_means_no_second_page(tmp_path: Path) -> None:
    config = load_config(_summary_config(tmp_path, ""))
    assert config.summary.personal is None


def test_a_personal_section_is_read(tmp_path: Path) -> None:
    config = load_config(
        _summary_config(
            tmp_path,
            '[summary.personal]\ntitle = "Bamboo Weekly Candidates"\n'
            'looking_for = "Things with public data behind them."\n',
        )
    )
    assert config.summary.personal is not None
    assert config.summary.personal.title == "Bamboo Weekly Candidates"
    assert config.summary.personal.looking_for == "Things with public data behind them."


def test_a_personal_section_may_omit_its_title(tmp_path: Path) -> None:
    config = load_config(
        _summary_config(
            tmp_path, '[summary.personal]\nlooking_for = "Anything on trade."\n'
        )
    )
    assert config.summary.personal is not None
    assert config.summary.personal.title == "Follow-ups"


def test_a_personal_section_without_looking_for_is_an_error(tmp_path: Path) -> None:
    """Present but empty is the sentinel this shape exists to avoid: if you
    asked for the page, say what it should look for."""
    with pytest.raises(ConfigError, match="looking_for"):
        load_config(_summary_config(tmp_path, '[summary.personal]\ntitle = "X"\n'))


def test_a_personal_section_that_is_not_a_table_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="must be a table"):
        load_config(_summary_config(tmp_path, 'personal = "just a string"\n'))


def test_an_unknown_key_in_the_personal_section_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"\[summary.personal\]"):
        load_config(
            _summary_config(
                tmp_path,
                '[summary.personal]\nlooking_for = "x"\ninterest_title = "old name"\n',
            )
        )


def test_cells_per_side_comes_from_the_file(tmp_path: Path) -> None:
    """Two a side is a standing preference for most people who want it -
    it is about how well they can read, not about one particular
    packet - so it belongs in the file rather than in a flag typed every
    week."""
    path = tmp_path / "config.toml"
    path.write_text("[print]\ncells_per_side = 2\n")
    config = load_config(path)
    assert config.printing.paper.cells_per_side == 2
    assert config.printing.paper.cell.width_mm == pytest.approx(148.5)


def test_a_cells_per_side_override_beats_the_file(tmp_path: Path) -> None:
    """And the flag still wins for one run, the way --paper does."""
    path = tmp_path / "config.toml"
    path.write_text("[print]\ncells_per_side = 2\n")
    assert load_config(path, cells_override=4).printing.paper.cells_per_side == 4


def test_one_folder_can_be_named_without_a_list(tmp_path: Path) -> None:
    """Most people read from one folder, and making them wrap it in
    brackets to say so is a tax on the common case."""
    path = tmp_path / "config.toml"
    path.write_text('[mail]\nfolders = "INBOX/reading"\n')
    assert load_config(path).mail.folders == ["INBOX/reading"]


def test_the_older_singular_spelling_still_works(tmp_path: Path) -> None:
    """`folder` is what every config written before this says, including
    the one --setup used to write. It means the same thing and is not
    going anywhere."""
    path = tmp_path / "config.toml"
    path.write_text('[mail]\nfolder = "INBOX/reading"\n')
    assert load_config(path).mail.folders == ["INBOX/reading"]


def test_several_folders_can_be_named(tmp_path: Path) -> None:
    """Filters do not always put everything in one place."""
    path = tmp_path / "config.toml"
    path.write_text('[mail]\nfolders = ["INBOX/toprint", "INBOX/work"]\n')
    config = load_config(path)
    assert config.mail.folders == ["INBOX/toprint", "INBOX/work"]


def test_folders_wins_over_folder_when_both_are_given(tmp_path: Path) -> None:
    """Naming both is a config half-edited. The plural is the more
    deliberate of the two, so it decides - and saying so beats guessing."""
    path = tmp_path / "config.toml"
    path.write_text('[mail]\nfolder = "INBOX/old"\nfolders = ["INBOX/new"]\n')
    assert load_config(path).mail.folders == ["INBOX/new"]


def test_an_empty_folders_list_is_refused(tmp_path: Path) -> None:
    """Nowhere to read from is a mistake worth naming, not a run that
    finds nothing."""
    path = tmp_path / "config.toml"
    path.write_text("[mail]\nfolders = []\n")
    with pytest.raises(ConfigError, match="at least one folder"):
        load_config(path)


def test_a_folders_value_that_is_neither_a_name_nor_a_list_is_refused(
    tmp_path: Path,
) -> None:
    """A string and a list of strings are the two shapes this takes.
    Anything else is a mistake worth naming, not something to coerce."""
    path = tmp_path / "config.toml"
    path.write_text("[mail]\nfolders = 7\n")
    with pytest.raises(ConfigError, match="a folder name or a list"):
        load_config(path)

    path.write_text("[mail]\nfolders = [1, 2]\n")
    with pytest.raises(ConfigError, match="a folder name or a list"):
        load_config(path)


def test_naming_both_folder_and_folders_says_which_one_won(
    tmp_path: Path, capsys
) -> None:
    """Adding `folders` to a config that already has `folder`, meaning
    "also read this one", silently drops the folder the tool has been
    reading all along - and a packet that arrives without it looks like a
    broken run rather than a half-edited config. Say so."""
    path = tmp_path / "config.toml"
    path.write_text('[mail]\nfolder = "INBOX/toprint"\nfolders = ["INBOX/work"]\n')

    config = load_config(path)

    assert config.mail.folders == ["INBOX/work"]
    warning = capsys.readouterr().err
    assert "INBOX/toprint" in warning
    assert "folders" in warning


def test_folders_alone_says_nothing(tmp_path: Path, capsys) -> None:
    """The warning is for a config that contradicts itself, not for one
    that simply uses the plural."""
    path = tmp_path / "config.toml"
    path.write_text('[mail]\nfolders = ["INBOX/work"]\n')
    load_config(path)
    assert capsys.readouterr().err == ""


def test_the_singular_folder_says_it_is_deprecated(tmp_path: Path, capsys) -> None:
    """Grandfathered, not endorsed. A config that says `folder` keeps
    working for as long as it takes its owner to get round to changing
    it, and says so once a run so that getting round to it happens."""
    path = tmp_path / "config.toml"
    path.write_text('[mail]\nfolder = "INBOX/reading"\n')

    assert load_config(path).mail.folders == ["INBOX/reading"]
    warning = capsys.readouterr().err
    assert "folder" in warning and "folders" in warning
    assert "deprecated" in warning.lower()


def test_a_config_that_sets_neither_says_nothing(tmp_path: Path, capsys) -> None:
    """The default is the singular's value, but nobody wrote it down, so
    there is nothing to warn them about."""
    load_config(tmp_path / "absent.toml")
    assert capsys.readouterr().err == ""


def test_renaming_the_key_leaves_everything_else_exactly_as_it_was(
    tmp_path: Path,
) -> None:
    """A config is hand-written and full of comments. Rewriting it by
    parsing and re-serializing would throw those away, so the rename is a
    line edit: the key changes and nothing else does - not the value, not
    the trailing comment, not a single other line. The `=` on that one
    line ends up a character to the right, which is the whole cost."""
    from newsprint.config import rename_folder_key

    path = tmp_path / "config.toml"
    original = (
        "# my notes\n"
        "[mail]\n"
        'host   = "imap.example.com"\n'
        'folder = "INBOX/toprint"         # the one I star into\n'
        'trash  = "auto"\n'
        "\n"
        "[print]\n"
        '# folder = "not this one, it is a comment"\n'
        'paper = "A4"\n'
    )
    path.write_text(original)

    assert rename_folder_key(path) is True
    assert path.read_text() == original.replace(
        'folder = "INBOX/toprint"', 'folders = "INBOX/toprint"'
    )


def test_renaming_reports_when_there_is_nothing_to_rename(tmp_path: Path) -> None:
    """Already renamed, or never named at all - either way the file is
    left alone and the caller is told so rather than shown a success it
    did not earn."""
    from newsprint.config import rename_folder_key

    path = tmp_path / "config.toml"
    path.write_text('[mail]\nfolders = "INBOX/toprint"\n')
    assert rename_folder_key(path) is False
    assert path.read_text() == '[mail]\nfolders = "INBOX/toprint"\n'


def test_an_indented_key_is_renamed_and_stays_indented(tmp_path: Path) -> None:
    """TOML allows leading whitespace on a key, and some people indent
    the body of a table. The indent is theirs and survives."""
    from newsprint.config import rename_folder_key

    path = tmp_path / "config.toml"
    path.write_text('[mail]\n    folder = "INBOX/toprint"\n')
    assert rename_folder_key(path) is True
    assert path.read_text() == '[mail]\n    folders = "INBOX/toprint"\n'


def test_a_config_that_cannot_be_read_is_left_to_the_loader(tmp_path: Path) -> None:
    """Reporting an unreadable config properly is load_config's job, a
    moment later. A rename that cannot read it just declines to act."""
    from newsprint.config import rename_folder_key

    assert rename_folder_key(tmp_path / "not-here.toml") is False


def test_two_a_side_reads_at_a_larger_default_size(tmp_path: Path) -> None:
    """A cell twice the size at the same type size does not give larger
    type, it gives longer lines - 88 characters, where 55 is comfortable.
    Measured on a real packet, 14pt puts the landscape cell back at the
    same 55 characters the four-up default gets."""
    path = tmp_path / "config.toml"
    path.write_text("[print]\ncells_per_side = 2\n")
    assert load_config(path).layout.font_size_pt == pytest.approx(14.0)


def test_the_flag_gets_the_larger_default_too(tmp_path: Path) -> None:
    """--cells-per-side 2 is how the layout is usually tried for the first
    time, and it is exactly the case where nothing has been written down
    to size the type. The default has to follow the layout actually being
    printed, not the one in the file."""
    path = tmp_path / "config.toml"
    path.write_text("[print]\ncells_per_side = 4\n")
    config = load_config(path, cells_override=2)
    assert config.layout.font_size_pt == pytest.approx(14.0)


def test_a_font_size_written_down_is_never_overridden(tmp_path: Path) -> None:
    """The larger size is a default, not a policy. Someone who sets 11
    gets 11."""
    path = tmp_path / "config.toml"
    path.write_text("[print]\ncells_per_side = 2\n\n[layout]\nfont_size_pt = 11.0\n")
    assert load_config(path).layout.font_size_pt == pytest.approx(11.0)


def test_four_a_side_keeps_the_smaller_default(tmp_path: Path) -> None:
    """The default layout is unchanged: 9pt in an A6 cell is already the
    55-character measure."""
    path = tmp_path / "config.toml"
    path.write_text("[print]\ncells_per_side = 4\n")
    assert load_config(path).layout.font_size_pt == pytest.approx(9.0)
