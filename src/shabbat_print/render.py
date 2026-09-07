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
from html import escape
from pathlib import Path
from string import Template

from ._libpath import prepare_dyld_fallback_library_path

# Must run before the weasyprint import: see _libpath.py for why this
# actually works despite dyld only reading DYLD_* once, at process start.
prepare_dyld_fallback_library_path()

from weasyprint import HTML

from .config import Config
from .models import Document

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
h1 { font-size: 1.15rem; line-height: 1.2; margin: 0 0 6pt; }
h2, h3, h4 { font-size: 1rem; margin: 8pt 0 3pt; }
p { margin: 0 0 5pt; orphans: 2; widows: 2; }
ul, ol { margin: 0 0 5pt; padding-left: 12pt; }
blockquote { margin: 0 0 5pt 8pt; font-style: italic; }
a { color: inherit; text-decoration: none; }
img { max-width: 100%; filter: grayscale(100%); }
</style></head>
<body>
<div class="masthead">$publication &middot; $date</div>
<h1>$title</h1>
$content
</body></html>""")


def _stem(document: Document, compression: float) -> str:
    digest = hashlib.sha256(document.origin.identifier.encode()).hexdigest()[:12]
    return f"{digest}-{compression:.2f}"


def _build_html(document: Document, config: Config, compression: float = 1.0) -> str:
    """Fill in the page template. Split out from render() so tests can
    inspect the generated markup and CSS directly, rather than only
    through rendered PDF geometry."""
    cell = config.printing.paper.cell
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
    )


def render(
    document: Document,
    config: Config,
    compression: float = 1.0,
    out_dir: Path | None = None,
) -> Path:
    """Render to a cell-sized PDF. `compression` scales the line height."""
    html = _build_html(document, config, compression)
    directory = out_dir if out_dir is not None else Path(tempfile.mkdtemp())
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{_stem(document, compression)}.pdf"
    HTML(string=html).write_pdf(output)
    return output
