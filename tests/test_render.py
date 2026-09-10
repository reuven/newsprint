from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from newsprint.config import load_config
from newsprint.geometry import A4
from newsprint.models import Document, Origin
from newsprint.pdfutil import page_count, page_text, text_extent_mm
from newsprint.render import render

PROSE = (
    "<p>The Federal Reserve declined to move rates this month, which surprised "
    "almost nobody who had been paying attention to the minutes.</p>"
)


@pytest.fixture
def config(tmp_path: Path):
    return load_config(tmp_path / "absent.toml")


def document(html: str) -> Document:
    return Document(
        origin=Origin(kind="email", identifier="<x@example.com>"),
        publication="Money Stuff",
        title="Private Credit Gets Complicated",
        date=datetime(2026, 9, 5, tzinfo=UTC),
        html=html,
    )


def test_page_is_exactly_one_cell(config, tmp_path: Path) -> None:
    import pymupdf

    pdf = render(document(PROSE), config, out_dir=tmp_path)
    with pymupdf.open(pdf) as opened:
        rect = opened[0].rect
    expected_width, expected_height = A4.cell.as_points()
    assert rect.width == pytest.approx(expected_width, abs=1.0)
    assert rect.height == pytest.approx(expected_height, abs=1.0)


def test_title_and_publication_appear(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE), config, out_dir=tmp_path)
    text = page_text(pdf, 0)
    assert "Private Credit Gets Complicated" in text
    # The masthead is deliberately rendered in uppercase (text-transform,
    # paired with letter-spacing), so compare case-insensitively rather
    # than bending production behavior to fit the test.
    assert "money stuff" in text.replace("\n", " ").lower()


def test_content_appears(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE), config, out_dir=tmp_path)
    assert "Federal Reserve" in page_text(pdf, 0)


def test_long_document_spans_several_cells(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE * 40), config, out_dir=tmp_path)
    assert page_count(pdf) > 1


def test_compression_never_increases_the_page_count(config, tmp_path: Path) -> None:
    long_document = document(PROSE * 40)
    loose = render(long_document, config, out_dir=tmp_path)
    tight = render(long_document, config, compression=0.98, out_dir=tmp_path)
    assert page_count(tight) <= page_count(loose)


def test_compression_writes_a_distinct_file(config, tmp_path: Path) -> None:
    doc = document(PROSE)
    assert render(doc, config, out_dir=tmp_path) != render(
        doc, config, compression=0.98, out_dir=tmp_path
    )


def test_letter_paper_gives_a_letter_cell(tmp_path: Path) -> None:
    import pymupdf

    from newsprint.geometry import LETTER

    config = load_config(tmp_path / "absent.toml", paper_override="letter")
    pdf = render(document(PROSE), config, out_dir=tmp_path)
    with pymupdf.open(pdf) as opened:
        rect = opened[0].rect
    expected_width, _ = LETTER.cell.as_points()
    assert rect.width == pytest.approx(expected_width, abs=1.0)


def test_text_extent_of_a_short_page_is_small(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE), config, out_dir=tmp_path)
    assert text_extent_mm(pdf, 0, config.layout.margin_mm) < A4.cell.height_mm / 2


def test_text_extent_of_a_full_page_is_large(config, tmp_path: Path) -> None:
    pdf = render(document(PROSE * 40), config, out_dir=tmp_path)
    assert text_extent_mm(pdf, 0, config.layout.margin_mm) > A4.cell.height_mm / 2


def test_text_extent_counts_a_trailing_numeric_content_block(
    config, tmp_path: Path
) -> None:
    """A numeral that is genuine content (a year, here) must not be mistaken
    for the footer's page-number counter and excluded from the extent."""
    without_numeral = render(document(PROSE), config, out_dir=tmp_path / "without")
    with_numeral = render(
        document(PROSE + "<p>2026</p>"), config, out_dir=tmp_path / "with"
    )
    assert text_extent_mm(with_numeral, 0, config.layout.margin_mm) > text_extent_mm(
        without_numeral, 0, config.layout.margin_mm
    )


def test_no_page_number_is_emitted_in_the_footer(config, tmp_path: Path) -> None:
    """F1 moved the footer to stamp.py; render.py's CSS must no longer draw
    a bare page-number counter in @bottom-center, or the packet footer
    stamped later would collide with it."""
    pdf = render(document(PROSE * 40), config, out_dir=tmp_path)
    assert page_count(pdf) > 1
    # The old counter rendered a bare "1" (just the page number, on its
    # own) in the bottom margin; with no @bottom-center rule at all, the
    # only text on the page is the masthead, heading and body.
    text = page_text(pdf, 0).replace("\n", " ")
    words = text.split(" ")
    assert "1" not in words


def test_dollar_signs_in_content_survive(config, tmp_path: Path) -> None:
    """The template substitutes with string.Template; $ in the content must
    not be treated as a placeholder."""
    pdf = render(
        document("<p>It cost $500 and $unexpected trouble.</p>"),
        config,
        out_dir=tmp_path,
    )
    assert "$500" in page_text(pdf, 0)


def test_masthead_reads_as_a_section_break(config) -> None:
    """G2: the masthead must read as a section break, not a subtitle - a
    heavy rule above it, and the name set bold and roughly level with the
    headline (not smaller than it). The old thin rule below the masthead
    goes: one heavy rule above is clearer than two rules."""
    from newsprint.render import _build_html

    html = _build_html(document(PROSE), config, compression=1.0)
    masthead_rule = _masthead_css(html)
    assert "border-top" in masthead_rule
    assert "border-bottom" not in masthead_rule
    assert "font-weight: bold" in masthead_rule or "font-weight:bold" in masthead_rule
    assert "1rem" in masthead_rule or "1.0rem" in masthead_rule
    # Roughly level with the headline, not smaller than it: h1 stayed at
    # 1.15rem, so the masthead (>= 1rem) must not be the smaller of the two.
    assert "0.70rem" not in masthead_rule
    assert "0.85rem" not in masthead_rule
    # Still reads as a masthead, not ordinary running text.
    assert "text-transform: uppercase" in masthead_rule
    assert "letter-spacing" in masthead_rule


def _masthead_css(html: str) -> str:
    start = html.index(".masthead")
    end = html.index("}", start)
    return html[start : end + 1]


def test_no_packet_title_line_renders_by_default(config, tmp_path: Path) -> None:
    """The tracked default is empty; an empty packet title must render
    nothing, not a blank line - the whole open-source point of H1."""
    pdf = render(document(PROSE), config, out_dir=tmp_path)
    text = page_text(pdf, 0)
    assert "packet-title" not in text


def test_a_packet_title_renders_above_the_masthead(config, tmp_path: Path) -> None:
    pdf = render(
        document(PROSE),
        config,
        out_dir=tmp_path,
        packet_title="Reuven's Shabbat reading",
    )
    text = page_text(pdf, 0)
    assert "shabbat reading" in text.lower()


def test_an_empty_packet_title_changes_nothing_in_the_generated_html(config) -> None:
    """Passing packet_title="" (the default) must produce byte-identical
    HTML to not passing it at all - nothing shifts."""
    from newsprint.render import _build_html

    without_kwarg = _build_html(document(PROSE), config)
    with_empty = _build_html(document(PROSE), config, packet_title="")
    assert without_kwarg == with_empty


def test_the_packet_title_is_larger_and_bolder_than_the_masthead(config) -> None:
    from newsprint.render import _build_html

    html = _build_html(document(PROSE), config, packet_title="Family Reading")
    start = html.index(".packet-title")
    end = html.index("}", start)
    packet_title_rule = html[start : end + 1]
    assert "font-weight: 900" in packet_title_rule
    assert "1.4rem" in packet_title_rule


def test_figure_placeholder_is_small_and_italic(config) -> None:
    """clean.py's figure placeholder ("[figure: ...]") is a note about
    something absent, not content - it must read as visually distinct
    from body prose: italic, and smaller than the surrounding text."""
    from newsprint.render import _build_html

    html = _build_html(document(PROSE), config, compression=1.0)
    start = html.index(".figure-placeholder")
    end = html.index("}", start)
    rule = html[start : end + 1]
    assert "italic" in rule
    assert "em" in rule.split("font-size:")[1].split(";")[0]
    # Smaller than 1em (the body's own size), not merely re-stated at 1em.
    size = rule.split("font-size:")[1].split(";")[0].strip()
    assert size not in ("1em", "1.0em")


def test_figure_placeholder_renders_into_the_flow(config, tmp_path: Path) -> None:
    """No image is ever fetched or rendered - the placeholder is plain
    text produced by clean.py, and render.py must simply lay it out like
    any other line, with no image request of any kind."""
    html = (
        "<p>The Federal Reserve declined to move rates this month.</p>"
        '<p class="figure-placeholder">[figure: GDP growth chart]</p>'
    )
    pdf = render(document(html), config, out_dir=tmp_path)
    text = page_text(pdf, 0)
    assert "figure: gdp growth chart" in text.lower()


def test_masthead_appears_only_once_per_newsletter(config, tmp_path: Path) -> None:
    """The masthead is the newsletter's own navigation cue and must appear
    once, on the first cell only - even when the article spans several
    cells."""
    pdf = render(document(PROSE * 40), config, out_dir=tmp_path)
    assert page_count(pdf) > 1
    first_page = page_text(pdf, 0).replace("\n", " ").lower()
    assert "money stuff" in first_page
    for index in range(1, page_count(pdf)):
        later_page = page_text(pdf, index).replace("\n", " ").lower()
        assert "money stuff" not in later_page


# Phase 8 (charts.md, 2026-09-07): a kept <img> (clean.py's own
# _is_argument_figure) is a remote URL that WeasyPrint fetches itself when
# the tag survives into the rendered HTML. None of these tests ever touch
# the network: url_fetcher is fully injectable, the same pattern
# printer.spool's `runner` and mail.Mailbox's `imap_factory` already use -
# every test below supplies its own fake fetcher instead.


class _FakeResponse:
    """The minimal shape render.py's fetcher wrapper needs: `.read()`
    returning raw bytes, matching weasyprint.urls.URLFetcherResponse
    closely enough to stand in for it in a test."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


def _wide_rgb_png(width: int = 900, height: int = 300) -> bytes:
    """A synthetic in-memory chart-shaped image: wide, colorful, and
    nothing like a real chart - grayscale conversion is easy to see on a
    saturated red source, and the width is comfortably past any reasonable
    cell cap so the resize path is actually exercised."""
    image = Image.new("RGB", (width, height), color=(220, 30, 30))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _image_document(src: str = "https://example.com/chart.png") -> Document:
    html = f'<p>Here\'s the chart:</p><img src="{src}"><p>Source: Census Bureau</p>'
    return document(html)


def test_grayscale_and_cap_does_not_upscale_a_narrower_source() -> None:
    """The resize branch only ever shrinks: a source already narrower than
    the cap must come out exactly as wide as it went in, not stretched up
    to fill the cap."""
    from newsprint.render import _grayscale_and_cap

    narrow = Image.new("RGB", (120, 40), color=(30, 120, 200))
    buffer = BytesIO()
    narrow.save(buffer, format="PNG")

    processed = _grayscale_and_cap(buffer.getvalue(), max_width_px=600)
    with Image.open(BytesIO(processed)) as result:
        assert result.mode == "L"
        assert result.width == 120


def test_a_fetched_image_is_embedded_grayscale_and_capped_to_the_cell(
    config, tmp_path: Path
) -> None:
    import pymupdf

    from newsprint.render import _cap_width_px

    fetcher_calls = []

    def fake_fetcher(url: str) -> _FakeResponse:
        fetcher_calls.append(url)
        return _FakeResponse(_wide_rgb_png())

    pdf = render(_image_document(), config, out_dir=tmp_path, url_fetcher=fake_fetcher)
    assert fetcher_calls == ["https://example.com/chart.png"]

    with pymupdf.open(pdf) as opened:
        images = opened[0].get_images(full=True)
        assert images, "expected the fetched chart to be embedded on the page"
        xref = images[0][0]
        base_image = opened.extract_image(xref)
    with Image.open(BytesIO(base_image["image"])) as embedded:
        assert embedded.mode == "L"  # grayscale, not color
        assert embedded.width <= _cap_width_px(config)


def test_a_failed_fetch_is_dropped_reported_and_does_not_crash(
    config, tmp_path: Path
) -> None:
    """A slow or dead image must not stall or crash a print run: the page
    still renders, just without that image, and the failure is reported
    back to the caller rather than silently swallowed."""

    def dying_fetcher(url: str) -> _FakeResponse:
        raise TimeoutError("simulated network failure")

    failures: list[str] = []
    pdf = render(
        _image_document(),
        config,
        out_dir=tmp_path,
        url_fetcher=dying_fetcher,
        image_fetch_failures=failures,
    )
    assert failures == ["https://example.com/chart.png"]
    # The page still built and still carries the surrounding prose.
    assert "Source: Census Bureau" in page_text(pdf, 0)


def test_a_non_image_response_is_treated_as_a_fetch_failure(
    config, tmp_path: Path
) -> None:
    """Corrupt or unparseable bytes (a dead CDN returning an HTML error
    page instead of an image, say) must degrade the same way a network
    failure does, not raise out of render()."""

    def bad_fetcher(url: str) -> _FakeResponse:
        return _FakeResponse(b"not actually an image")

    failures: list[str] = []
    render(
        _image_document(),
        config,
        out_dir=tmp_path,
        url_fetcher=bad_fetcher,
        image_fetch_failures=failures,
    )
    assert failures == ["https://example.com/chart.png"]


def test_an_image_cache_avoids_a_second_fetch_of_the_same_url(
    config, tmp_path: Path
) -> None:
    """trim.fit re-renders the same document up to twice more (its
    compression retries), so without a cache the exact same chart URL is
    fetched once per attempt - measured live against the real starred
    queue, 61 requests for only 35 distinct kept images. `image_cache`
    lets a caller (pipeline.build_one) share one dict across every
    render() call for one document, so a URL already fetched and
    processed is reused rather than fetched again."""
    import pymupdf

    fetcher_calls = []

    def fake_fetcher(url: str) -> _FakeResponse:
        fetcher_calls.append(url)
        return _FakeResponse(_wide_rgb_png())

    cache: dict[str, bytes | None] = {}
    render(
        _image_document(),
        config,
        out_dir=tmp_path / "one",
        url_fetcher=fake_fetcher,
        image_cache=cache,
    )
    failures: list[str] = []
    second = render(
        _image_document(),
        config,
        out_dir=tmp_path / "two",
        url_fetcher=fake_fetcher,
        image_cache=cache,
        image_fetch_failures=failures,
    )
    assert fetcher_calls == ["https://example.com/chart.png"]
    # Not fetching again is only half of it: what was cached has to be the
    # image. Caching the *failure* of a successful fetch would look
    # identical here - one call, no second - while quietly dropping the
    # chart from every compression retry after the first.
    assert failures == []
    with pymupdf.open(second) as opened:
        assert opened[0].get_images(full=True), (
            "the cached chart must still be embedded on the second render"
        )


def test_a_cached_failure_is_not_retried_but_is_still_reported(
    config, tmp_path: Path
) -> None:
    """A dead image should not be re-attempted on every compression retry
    - each attempt would pay the full timeout again - but each render()
    call must still report it as a failure so pipeline.build_one's
    dedup(-and-keep) logic sees it regardless of which attempt produced
    the final PDF."""
    fetcher_calls = []

    def dying_fetcher(url: str) -> _FakeResponse:
        fetcher_calls.append(url)
        raise TimeoutError("simulated network failure")

    cache: dict[str, bytes | None] = {}
    failures_one: list[str] = []
    failures_two: list[str] = []
    render(
        _image_document(),
        config,
        out_dir=tmp_path / "one",
        url_fetcher=dying_fetcher,
        image_cache=cache,
        image_fetch_failures=failures_one,
    )
    render(
        _image_document(),
        config,
        out_dir=tmp_path / "two",
        url_fetcher=dying_fetcher,
        image_cache=cache,
        image_fetch_failures=failures_two,
    )
    assert fetcher_calls == ["https://example.com/chart.png"]  # not retried
    assert failures_one == ["https://example.com/chart.png"]
    assert failures_two == ["https://example.com/chart.png"]  # still reported


def test_no_images_means_no_fetch_is_ever_attempted(config, tmp_path: Path) -> None:
    """Only kept images are ever fetched - an ordinary document with no
    <img> at all must never call the fetcher, confirming clean.py's own
    filtering, not render.py, is what keeps network traffic to a
    minimum."""

    def exploding_fetcher(url: str) -> _FakeResponse:
        raise AssertionError(f"unexpected fetch of {url!r}")

    pdf = render(
        document(PROSE), config, out_dir=tmp_path, url_fetcher=exploding_fetcher
    )
    assert "Federal Reserve" in page_text(pdf, 0)


def test_the_image_cap_is_the_cells_exact_text_column() -> None:
    """The other cap test asserts `width <= _cap_width_px(config)`, which
    every wrong arithmetic still satisfies - adding the margins instead of
    subtracting them, dividing by the DPI, or tripling the margin all
    passed. This pins the figure itself: an A4 cell is 105mm wide, so its
    text column is 105 - 2*9 = 87mm, and at 200dpi that is 685px.
    """
    from newsprint.config import load_config
    from newsprint.render import _cap_width_px

    # A deliberately absent path, so this reads the packaged defaults and
    # not whatever config the person running the tests happens to have.
    config = load_config(Path("/nonexistent/newsprint/config.toml"))
    assert config.printing.paper.cell.width_mm == 105.0
    assert config.layout.margin_mm == 9.0
    assert _cap_width_px(config) == 685


def test_resizing_a_wide_image_preserves_its_aspect_ratio() -> None:
    """Height scales by the same ratio as width. Dividing by the ratio
    instead of multiplying stretches a 2:1 chart into a 1:3 tower, and
    nothing asserted on the resulting height."""
    from newsprint.render import _grayscale_and_cap

    source = Image.new("RGB", (1000, 500), color=(200, 50, 50))
    buffer = BytesIO()
    source.save(buffer, format="PNG")

    processed = _grayscale_and_cap(buffer.getvalue(), max_width_px=400)
    with Image.open(BytesIO(processed)) as result:
        assert (result.width, result.height) == (400, 200)


def test_a_very_short_image_keeps_at_least_one_pixel_of_height() -> None:
    """A wide, one-pixel-high rule scales to less than half a pixel; the
    floor of 1 is what stops Pillow being asked for a zero-height image."""
    from newsprint.render import _grayscale_and_cap

    source = Image.new("RGB", (1000, 1), color=(0, 0, 0))
    buffer = BytesIO()
    source.save(buffer, format="PNG")

    processed = _grayscale_and_cap(buffer.getvalue(), max_width_px=100)
    with Image.open(BytesIO(processed)) as result:
        assert (result.width, result.height) == (100, 1)


# ---------------------------------------------------------------------------
# Where a rendered PDF goes, and what it is called.
# ---------------------------------------------------------------------------


def test_the_filename_names_the_message_and_the_compression(config, tmp_path) -> None:
    """trim.fit re-renders one document at several compressions and keeps
    whichever fits, so the attempts have to be able to sit side by side
    without one overwriting another. The name is a digest of the message's
    own identifier - twelve hex characters of it, enough to separate a
    morning's newsletters and short enough to read in a listing - and the
    compression that produced it.
    """
    import hashlib

    doc = document("<p>Hello</p>")
    digest = hashlib.sha256(doc.origin.identifier.encode()).hexdigest()[:12]

    loose = render(doc, config, out_dir=tmp_path)
    tight = render(doc, config, compression=0.9, out_dir=tmp_path)

    assert loose.name == f"{digest}-1.00.pdf"
    assert tight.name == f"{digest}-0.90.pdf"
    assert loose != tight, "two attempts at one document must not collide"


def test_a_different_message_renders_to_a_different_file(config, tmp_path) -> None:
    """Two newsletters in one packet, both at the same compression: the
    identifier is the whole of what tells their files apart."""
    first = document("<p>Hello</p>")
    second = Document(
        origin=Origin(kind="email", identifier="<other@example.com>"),
        publication=first.publication,
        title=first.title,
        date=first.date,
        html="<p>Hello</p>",
    )
    assert render(first, config, out_dir=tmp_path) != render(
        second, config, out_dir=tmp_path
    )


def test_with_no_directory_given_the_pdf_lands_somewhere_temporary(config) -> None:
    """out_dir is optional - a caller that only wants the bytes back need
    not invent a home for them first."""
    output = render(document("<p>Hello</p>"), config)
    assert output.exists()
    assert output.parent != Path.cwd()


def test_a_downscaled_chart_is_resampled_with_lanczos(config) -> None:
    """Pillow's default resampling for a downscale is bicubic, which
    softens the one-pixel gridlines and hairline series a chart is mostly
    made of. Lanczos is chosen deliberately, and at 200 dpi onto a cell
    barely three inches wide almost every chart is downscaled, so the
    choice applies to nearly all of them."""
    from newsprint.render import _grayscale_and_cap

    source = Image.new("L", (400, 120), color=255)
    for x in range(0, 400, 8):  # hairline gridlines, one pixel wide
        for y in range(120):
            source.putpixel((x, y), 0)
    buffer = BytesIO()
    source.save(buffer, format="PNG")

    processed = _grayscale_and_cap(buffer.getvalue(), 100)

    def resampled(filter_: Image.Resampling | None) -> bytes:
        out = BytesIO()
        if filter_ is None:
            source.resize((100, 30)).save(out, format="PNG")
        else:
            source.resize((100, 30), filter_).save(out, format="PNG")
        return out.getvalue()

    assert processed == resampled(Image.Resampling.LANCZOS)
    assert processed != resampled(None), "the default would have been bicubic"


def test_the_default_compression_leaves_the_line_height_alone(config) -> None:
    """Compression is a multiplier on the configured line height, and 1.0
    is the identity - the first attempt at a document renders it exactly
    as the config asks, and only a retry squeezes it."""
    from newsprint.render import _build_html

    html = _build_html(document(PROSE), config)
    assert f"line-height: {config.layout.line_height:.4f}" in html


def test_an_undecodable_image_is_cached_as_a_failure_too(config, tmp_path) -> None:
    """A dead CDN returning an error page instead of an image costs a
    fetch and a decode attempt, and both are wasted a second time on every
    compression retry. The failure is remembered the same way a network
    failure is - and each render still reports it, since each reports its
    own outcome."""
    fetcher_calls = []

    def bad_fetcher(url: str) -> _FakeResponse:
        fetcher_calls.append(url)
        return _FakeResponse(b"not actually an image")

    cache: dict[str, bytes | None] = {}
    failures_one: list[str] = []
    failures_two: list[str] = []
    render(
        _image_document(),
        config,
        out_dir=tmp_path / "one",
        url_fetcher=bad_fetcher,
        image_cache=cache,
        image_fetch_failures=failures_one,
    )
    render(
        _image_document(),
        config,
        out_dir=tmp_path / "two",
        url_fetcher=bad_fetcher,
        image_cache=cache,
        image_fetch_failures=failures_two,
    )
    assert fetcher_calls == ["https://example.com/chart.png"], "not fetched again"
    assert failures_one == ["https://example.com/chart.png"]
    assert failures_two == ["https://example.com/chart.png"]


def test_the_default_image_fetcher_is_given_a_timeout(config, tmp_path) -> None:
    """Nothing in a print run is worth waiting on an unresponsive host
    for: a newsletter with a dead chart still has to reach the printer.
    The timeout is on the fetcher render() builds when the caller gives it
    none, which is every real run."""
    import newsprint.render as render_module
    from newsprint.render import _IMAGE_FETCH_TIMEOUT_S

    built: list[float | None] = []

    class SpyFetcher:
        def __init__(self, timeout: float | None = None) -> None:
            built.append(timeout)

        def __call__(self, url: str) -> object:
            raise TimeoutError("never reached")

    original = render_module.URLFetcher
    render_module.URLFetcher = SpyFetcher  # type: ignore[misc]
    try:
        render(document("<p>No images here at all.</p>"), config, out_dir=tmp_path)
    finally:
        render_module.URLFetcher = original  # type: ignore[misc]
    assert built == [_IMAGE_FETCH_TIMEOUT_S]
    assert _IMAGE_FETCH_TIMEOUT_S > 0
