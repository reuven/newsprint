from datetime import UTC, datetime
from pathlib import Path

import pytest

from shabbat_print.config import load_config
from shabbat_print.models import Document, Origin, Verdict
from shabbat_print.pipeline import build

PROSE = (
    "<p>The Federal Reserve declined to move rates this month, which surprised "
    "almost nobody who had been paying attention to the minutes.</p>"
)
# Long enough to clear the default packet.min_words threshold (250) after
# cleaning, so a document built from it is a genuine Built, not skipped as
# a teaser by the H2 word-count check in build_one().
LONG_PROSE = PROSE * 15


@pytest.fixture
def config(tmp_path: Path):
    return load_config(tmp_path / "absent.toml")


def document(html: str, identifier: str = "<a@example.com>") -> Document:
    return Document(
        origin=Origin(kind="email", identifier=identifier, uid=1),
        publication="Test Weekly",
        title="An Issue",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=html,
    )


def test_builds_a_pdf_per_document(config, tmp_path: Path) -> None:
    built, failed = build(
        [document(LONG_PROSE, "<a@x>"), document(LONG_PROSE, "<b@x>")], config, tmp_path
    )
    assert len(built) == 2
    assert failed == []
    assert all(item.pdf.exists() for item in built)
    assert all(item.cells >= 1 for item in built)


def test_cells_matches_the_pdf_it_reports(config, tmp_path: Path) -> None:
    """Built.cells is what the sheet count is computed from, so it must equal
    the page count of the PDF actually handed on."""
    from shabbat_print.pdfutil import page_count

    built, _ = build([document(PROSE * 40)], config, tmp_path)
    assert built[0].cells == page_count(built[0].pdf)
    assert built[0].cells > 1
    assert isinstance(built[0].verdict, Verdict)


def test_a_failing_document_is_reported_not_raised(config, tmp_path: Path) -> None:
    broken = document(LONG_PROSE, "<c@x>")
    built, failed = build([broken], config, tmp_path, render_fn=_explode)
    assert built == []
    assert len(failed) == 1
    assert failed[0].document is broken
    assert "boom" in str(failed[0].error)


def _explode(*args, **kwargs):
    raise RuntimeError("boom")


def test_an_empty_document_is_a_failure_not_a_blank_page(
    config, tmp_path: Path
) -> None:
    """A newsletter that cleans down to nothing must not print an empty cell."""
    built, failed = build([document("")], config, tmp_path)
    assert built == []
    assert len(failed) == 1


def test_a_teaser_below_the_threshold_is_a_failure_not_a_built(
    config, tmp_path: Path
) -> None:
    """A headline-and-link teaser must be skipped, not printed - the same
    outcome shape as any other build failure, so the retirement invariant
    (uids come only from `built`) applies to it automatically."""
    from shabbat_print.pipeline import TeaserSkippedError

    teaser = document("<h1>An Issue</h1><p>Read more online.</p>", "<t@x>")
    built, failed = build([teaser], config, tmp_path)
    assert built == []
    assert len(failed) == 1
    assert isinstance(failed[0].error, TeaserSkippedError)
    assert failed[0].error.word_count < config.packet.min_words
    assert failed[0].error.min_words == config.packet.min_words
    assert failed[0].document is teaser


def test_a_document_at_or_above_the_threshold_is_built(config, tmp_path: Path) -> None:
    built, failed = build([document(LONG_PROSE, "<u@x>")], config, tmp_path)
    assert failed == []
    assert len(built) == 1


def test_force_include_bypasses_the_teaser_threshold(config, tmp_path: Path) -> None:
    """A document whose extracted word count is implausibly low can still
    be built when a caller has already confirmed "include it anyway" with
    the user - the one case where a document below packet.min_words must
    still reach the PDF rather than being silently re-skipped a second
    time, which would make that confirmation meaningless."""
    from dataclasses import replace

    teaser = replace(
        document("<h1>An Issue</h1><p>Read more online.</p>", "<v@x>"),
        force_include=True,
    )
    built, failed = build([teaser], config, tmp_path)
    assert failed == []
    assert len(built) == 1


def test_force_include_does_not_rescue_a_genuinely_empty_document(
    config, tmp_path: Path
) -> None:
    """force_include only bypasses the word-count threshold - a document
    that cleans down to literally nothing must still fail, since there is
    nothing to render regardless of what the user confirmed."""
    from dataclasses import replace

    empty = replace(document(""), force_include=True)
    built, failed = build([empty], config, tmp_path)
    assert built == []
    assert len(failed) == 1


# Phase 8 (charts.md): render_fn may now report failed image fetches via
# an `image_fetch_failures` kwarg (render.py's own contract - a list it
# appends URLs into, never replaces). build_one() must thread that same
# list through every render_fn call for one document - including any
# compression retry trim.fit triggers - and surface the result on Built,
# deduplicated, so a retry that fails on the same URL twice is reported
# once, not twice.
def _render_recording_a_failure(document, config, out_dir=None, **kwargs):
    failures = kwargs.get("image_fetch_failures")
    if failures is not None:
        failures.append("https://example.com/dead-chart.png")
    from shabbat_print.render import render as real_render

    return real_render(document, config, out_dir=out_dir)


def test_image_fetch_failures_are_reported_on_the_built_result(
    config, tmp_path: Path
) -> None:
    built, failed = build(
        [document(LONG_PROSE, "<f@x>")],
        config,
        tmp_path,
        render_fn=_render_recording_a_failure,
    )
    assert failed == []
    assert len(built) == 1
    assert built[0].image_fetch_failures == ("https://example.com/dead-chart.png",)


def test_a_document_with_no_image_fetch_failures_reports_an_empty_tuple(
    config, tmp_path: Path
) -> None:
    built, _ = build([document(LONG_PROSE, "<g@x>")], config, tmp_path)
    assert built[0].image_fetch_failures == ()


def test_the_same_image_cache_is_shared_across_every_render_fn_call(
    config, tmp_path: Path
) -> None:
    """render.py's own image_cache avoids re-fetching a URL trim.fit's
    compression retries would otherwise fetch again - measured live, up
    to 3x over. build_one() must pass the *same* dict object into every
    render_fn call for one document, not a fresh one per call, or the
    cache could never do its job."""
    seen_cache_ids: set[int] = set()

    def _recording_render(document, config, out_dir=None, **kwargs):
        cache = kwargs.get("image_cache")
        assert cache is not None, "build_one must pass image_cache"
        seen_cache_ids.add(id(cache))
        from shabbat_print.render import render as real_render

        return real_render(document, config, out_dir=out_dir)

    _built, failed = build(
        [document(LONG_PROSE, "<h@x>")],
        config,
        tmp_path,
        render_fn=_recording_render,
    )
    assert failed == []
    assert len(seen_cache_ids) == 1  # exactly one shared dict, never a fresh one


def test_documents_sharing_an_identifier_do_not_collide(config, tmp_path: Path) -> None:
    """render() names its output from the document's identifier, and bulk mail
    without a Message-ID falls back to the sender address in extract.py, so
    two such messages can arrive with the same identifier. build() must not
    let one silently overwrite the other under a shared out_dir."""
    built, failed = build(
        [
            document(LONG_PROSE, "<same@example.com>"),
            document(LONG_PROSE, "<same@example.com>"),
        ],
        config,
        tmp_path,
    )
    assert failed == []
    assert len(built) == 2
    assert built[0].pdf != built[1].pdf
    assert built[0].pdf.exists()
    assert built[1].pdf.exists()
