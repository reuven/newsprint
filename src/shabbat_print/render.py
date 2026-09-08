"""Typeset a Document onto cell-sized pages.

The page box is exactly one cell, so `impose.py` can tile four of them at 100%
scale. A font specified at 9pt therefore measures 9pt on the paper.

G2: the masthead is a stack of quarter-pages' main navigation cue - the
signal that a new newsletter has started - so it has to win against the
article headline below it, not read as a subtitle to it. It is set bold,
roughly level with the headline's own 1.15rem (not smaller, which is what a
size bump alone would still have been), in its existing small caps with
letter-spacing, under a single heavy rule. A heavy *reversed* bar was
considered and rejected: small reversed type fills in on a mono laser
printer and costs toner on every newsletter start, four times a page. The
thin rule that used to sit *below* the masthead is gone too - one heavy
rule reads more clearly than two.
"""

import hashlib
import tempfile
from collections.abc import Callable
from html import escape
from io import BytesIO
from pathlib import Path
from string import Template

from PIL import Image

from ._libpath import prepare_dyld_fallback_library_path

# Must run before the weasyprint import: see _libpath.py for why this
# actually works despite dyld only reading DYLD_* once, at process start.
prepare_dyld_fallback_library_path()

from weasyprint import HTML
from weasyprint.urls import URLFetcher, URLFetcherResponse

from .config import Config
from .geometry import MM_PER_INCH
from .models import Document

# What render() (and the HTML() constructor it feeds) needs from a URL
# fetcher: something callable with a URL, returning an object with a
# `.read()` -> bytes method. weasyprint.urls.URLFetcher instances satisfy
# this via __call__; so does any test double (see test_render.py's
# _FakeResponse) - the same "pass anything with the right shape" contract
# printer.spool's `runner` and mail.Mailbox's `imap_factory` already use.
ImageFetcher = Callable[[str], object]

# A per-image ceiling on how long a fetch may block: a slow or dead chart
# host must not stall an entire print run. 8s is generous for a single
# already-CDN-hosted image (Substack's own image proxy, in the corpus this
# rule was measured against) while still bounded - a newsletter with
# several kept charts pays this at most once per image, never cumulatively
# blocking on one that will not answer.
_IMAGE_FETCH_TIMEOUT_S = 8.0

# The resolution a kept chart is rasterised at once resized to fit the
# cell. 200dpi is comfortably above what a laser printer resolves as
# separate detail at this size, while still shrinking a typical ~550-1100px
# source substantially - see charts.md's own "reduced about 2.5x" note.
_IMAGE_DPI = 200

# string.Template, not str.format: the CSS is full of braces, and the
# substituted content is not rescanned, so a "$" in a newsletter is harmless.
_TEMPLATE = Template("""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>$title</title>
<style>
@page {
  size: ${width_mm}mm ${height_mm}mm;
  margin: ${margin_mm}mm;
}
html { font-size: ${font_size_pt}pt; }
body { font-family: Charter, Georgia, "Times New Roman", serif;
       line-height: $line_height; margin: 0; hyphens: auto; text-align: justify; }
.masthead { font-family: -apple-system, "Helvetica Neue", Helvetica, sans-serif;
            font-size: 1rem; font-weight: bold; text-transform: uppercase;
            letter-spacing: 0.06em;
            border-top: 1.5pt solid #000; padding-top: 3pt; margin-bottom: 8pt; }
.packet-title { font-family: -apple-system, "Helvetica Neue", Helvetica, sans-serif;
            font-size: 1.4rem; font-weight: 900; text-transform: uppercase;
            letter-spacing: 0.02em; margin: 0 0 4pt; }
h1 { font-size: 1.15rem; line-height: 1.2; margin: 0 0 6pt; }
h2, h3, h4 { font-size: 1rem; margin: 8pt 0 3pt; }
p { margin: 0 0 5pt; orphans: 2; widows: 2; }
ul, ol { margin: 0 0 5pt; padding-left: 12pt; }
blockquote { margin: 0 0 5pt 8pt; font-style: italic; }
a { color: inherit; text-decoration: none; }
/* A kept image already arrives grayscale and capped to the cell's text
   width - see _fetch_and_process below. There is deliberately no
   `filter: grayscale(...)` here: WeasyPrint 69.0 does not implement the
   CSS `filter` property at all (confirmed against its own css/ package -
   no such rule ever existed there), so it would be silent, uninspected
   dead CSS if it were, exactly the kind of thing that reads as "handled"
   without doing anything. max-width still matters: it is real layout,
   not a filter, and keeps the printed cell's own layout robust even if a
   fetched image's processed width is ever slightly larger than the cap. */
img { max-width: 100%; }
.figure-placeholder { font-style: italic; font-size: 0.85em; color: #444;
                       margin: 0 0 5pt; }
</style></head>
<body>
$packet_title_html<div class="masthead">$publication &middot; $date</div>
<h1>$title</h1>
$content
</body></html>""")


def _stem(document: Document, compression: float) -> str:
    digest = hashlib.sha256(document.origin.identifier.encode()).hexdigest()[:12]
    return f"{digest}-{compression:.2f}"


def _build_html(
    document: Document,
    config: Config,
    compression: float = 1.0,
    packet_title: str = "",
) -> str:
    """Fill in the page template. Split out from render() so tests can
    inspect the generated markup and CSS directly, rather than only
    through rendered PDF geometry.

    packet_title is empty for every ordinary newsletter; only contents.py
    passes one, so the packet's own title line renders once, on the
    contents page, never on a newsletter cell. Empty means no element at
    all rather than an empty one - no margin, no shift - so the tracked
    default (empty, until a user sets one in their own config) changes
    nothing about the page.
    """
    cell = config.printing.paper.cell
    packet_title_html = (
        f'<div class="packet-title">{escape(packet_title)}</div>\n'
        if packet_title
        else ""
    )
    return _TEMPLATE.substitute(
        width_mm=f"{cell.width_mm:g}",
        height_mm=f"{cell.height_mm:g}",
        margin_mm=f"{config.layout.margin_mm:g}",
        font_size_pt=f"{config.layout.font_size_pt:g}",
        line_height=f"{config.layout.line_height * compression:.4f}",
        publication=escape(document.publication),
        date=escape(document.date.strftime("%-d %B %Y")),
        title=escape(document.title),
        content=document.html,
        packet_title_html=packet_title_html,
    )


def _cap_width_px(config: Config) -> int:
    """The pixel width a kept chart is resized down to: the cell's own text
    column (its width minus both margins), at _IMAGE_DPI. Split out so
    tests can compare an embedded image's actual width against the exact
    figure render() used, rather than a re-derived approximation of it."""
    cell = config.printing.paper.cell
    text_width_mm = max(cell.width_mm - 2 * config.layout.margin_mm, 1.0)
    return max(1, round(text_width_mm / MM_PER_INCH * _IMAGE_DPI))


def _grayscale_and_cap(data: bytes, max_width_px: int) -> bytes:
    """Convert a fetched image's raw bytes to 8-bit grayscale PNG, resized
    down if wider than `max_width_px`. Real pixel-level work, not CSS: see
    the `img` rule's own comment in _TEMPLATE for why WeasyPrint's
    complete lack of `filter` support rules that out. Raises on anything
    Pillow cannot open - a corrupt download, or a CDN's HTML error page
    served with an image content-type - so the caller can treat it exactly
    like a network failure (see _fetch_and_process)."""
    with Image.open(BytesIO(data)) as opened:
        opened.load()
        grayscale = opened.convert("L")
    if grayscale.width > max_width_px:
        ratio = max_width_px / grayscale.width
        grayscale = grayscale.resize(
            (max_width_px, max(1, round(grayscale.height * ratio))),
            Image.Resampling.LANCZOS,
        )
    buffer = BytesIO()
    grayscale.save(buffer, format="PNG")
    return buffer.getvalue()


def _fetch_and_process(
    base_fetcher: ImageFetcher,
    max_width_px: int,
    failures: list[str],
    cache: dict[str, bytes | None] | None = None,
) -> ImageFetcher:
    """Wrap `base_fetcher` so every image it fetches for this render is
    converted to grayscale and capped to `max_width_px` before WeasyPrint
    ever lays it out, and so a failure at either step - the fetch itself,
    or Pillow failing to decode what came back - is recorded in
    `failures` and re-raised.

    Re-raising, rather than swallowing the error here, matters: WeasyPrint
    already catches any exception a url_fetcher raises (converting it to
    URLFetcherResponse=None internally and simply omitting the image - see
    weasyprint.images.get_image_from_uri) and logs it, so re-raising costs
    nothing beyond what would happen anyway, and is what keeps `failures`
    an accurate record rather than a silent gap. This is why "must not
    crash on a fetch failure" needs no try/except at the render() call
    site below - WeasyPrint's own image-loading code is already that
    boundary, confirmed by reading weasyprint/images.py rather than
    assumed.

    `cache`, when given, is checked first and updated after: a successful
    fetch stores its processed bytes; a failed one stores None, so a
    second call for the same URL neither hits the network again nor waits
    out the timeout again - it still raises (so WeasyPrint still treats it
    as a missing image) and still appends to `failures` (each render()
    call reports its own outcome; pipeline.build_one is what deduplicates
    across calls). Measured live against the real starred queue before
    this existed: trim.fit's own compression retries re-render the same
    document up to twice more, so the same chart URL was being fetched up
    to 3 times over - 61 real requests for only 35 distinct kept images.
    """

    def fetch(url: str) -> URLFetcherResponse:
        if cache is not None and url in cache:
            cached = cache[url]
            if cached is None:
                failures.append(url)
                raise ValueError(f"previously failed to fetch or decode {url!r}")
            return URLFetcherResponse(url, cached, {"Content-Type": "image/png"})
        try:
            response = base_fetcher(url)
        except Exception:
            failures.append(url)
            if cache is not None:
                cache[url] = None
            raise
        try:
            data = response.read()
        finally:
            # A real URLFetcherResponse wraps an open urllib response;
            # leaving it unclosed after read() surfaces later as an
            # unraisable ResourceWarning at garbage-collection time -
            # under this project's own `filterwarnings = ["error"]`
            # pytest config, that fails the whole test session, not just
            # one test, which is how this was actually found. `getattr`
            # rather than assuming close() exists: test doubles (see
            # test_render.py's _FakeResponse) need not implement it.
            close = getattr(response, "close", None)
            if close is not None:
                close()
        try:
            processed = _grayscale_and_cap(data, max_width_px)
        except Exception:
            failures.append(url)
            if cache is not None:
                cache[url] = None
            raise
        if cache is not None:
            cache[url] = processed
        return URLFetcherResponse(url, processed, {"Content-Type": "image/png"})

    return fetch


def render(
    document: Document,
    config: Config,
    compression: float = 1.0,
    out_dir: Path | None = None,
    packet_title: str = "",
    url_fetcher: ImageFetcher | None = None,
    image_fetch_failures: list[str] | None = None,
    image_cache: dict[str, bytes | None] | None = None,
) -> Path:
    """Render to a cell-sized PDF. `compression` scales the line height.

    packet_title is the packet's own title line, shown once above the
    masthead - only contents.py ever passes one, so it appears solely on
    the contents page.

    A kept image (clean.py's own _is_argument_figure) is a remote <img
    src> that survives into `document.html`; WeasyPrint fetches it itself
    when it lays out the page. `url_fetcher` is the injection point for
    that fetch - default None builds a real, timeout-bounded
    weasyprint.urls.URLFetcher, following the same pattern printer.spool's
    `runner` and mail.Mailbox's `imap_factory` already use, so no test
    needs the network. `image_fetch_failures`, if given, is appended to
    (never replaced) with the URL of any image that failed to fetch or
    decode - reported distinctly from an ordinary drop, which happens
    earlier, in clean.py, and never reaches this function at all. A
    document with no kept images never calls the fetcher: `HTML().
    write_pdf()` only ever invokes url_fetcher for a resource an <img> (or
    other external reference) actually names, and clean.py never leaves
    one in `document.html` unless it was kept. `image_cache`, if given, is
    shared across repeated render() calls for the same document (see
    pipeline.build_one, which passes the same dict into every compression
    retry) so a URL already fetched is reused instead of fetched again -
    see _fetch_and_process's own docstring for the live measurement behind
    this.
    """
    html = _build_html(document, config, compression, packet_title)
    directory = out_dir if out_dir is not None else Path(tempfile.mkdtemp())
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{_stem(document, compression)}.pdf"
    base_fetcher = (
        url_fetcher
        if url_fetcher is not None
        else URLFetcher(timeout=_IMAGE_FETCH_TIMEOUT_S)
    )
    failures = image_fetch_failures if image_fetch_failures is not None else []
    fetcher = _fetch_and_process(
        base_fetcher, _cap_width_px(config), failures, image_cache
    )
    HTML(string=html, url_fetcher=fetcher).write_pdf(output)
    return output
