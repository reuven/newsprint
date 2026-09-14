"""Build the newsprint logo, from the shapes rather than from a drawing.

The mark is an open folded sheet - the thing the tool actually hands you,
two A5 leaves creased down the middle - with a masthead rule and lines of
type on each leaf, which is what a cell looks like when it prints.

The wordmark is set the way render.py sets a packet title: heavy sans,
uppercase, letterspaced, under one heavy rule. It is converted to
outlines here so the logo renders the same everywhere, with no font to
install and nothing to fall back to.

Run: uv run --with cairosvg python assets/build_logo.py
"""

from __future__ import annotations

from pathlib import Path

# fontTools and cairosvg ship no type information; this script is not
# part of the package and is checked only for its own sake.
from fontTools.pens.svgPathPen import SVGPathPen  # type: ignore[import-untyped]
from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

HERE = Path(__file__).parent
INK = "#14110f"
PAPER = "#faf8f4"
SHEET = "#ffffff"

WORDMARK_FONT = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
TAGLINE_FONT = "/System/Library/Fonts/Supplemental/Georgia Italic.ttf"


def outline(
    text: str, font_path: str, size: float, tracking: float = 0.0
) -> tuple[str, float]:
    """`text` as SVG path data, and how wide it came out."""
    font = TTFont(font_path)
    scale = size / font["head"].unitsPerEm
    cmap, glyphs, hmtx = font.getBestCmap(), font.getGlyphSet(), font["hmtx"]
    parts: list[str] = []
    x = 0.0
    for char in text:
        name = cmap.get(ord(char))
        if name is None:
            x += size * 0.4
            continue
        pen = SVGPathPen(glyphs)
        glyphs[name].draw(pen)
        if commands := pen.getCommands():
            parts.append(
                f'<path transform="translate({x:.2f} 0) '
                f'scale({scale:.5f} {-scale:.5f})" d="{commands}"/>'
            )
        x += hmtx[name][0] * scale + tracking
    return "".join(parts), x


def mark(*, stroke: int, rules: int, rule_h: int, line_h: int, gap: int) -> str:
    """The open folded sheet. `rules` lines of type a leaf, heavier and
    fewer as the mark gets smaller - at 32px anything finer fills in."""

    def leaf(x0: int, opacity: str) -> str:
        rows = "".join(
            f'<rect y="{rule_h + 14 + i * gap}" '
            f'width="{112 - (26 if i == rules - 1 else 0)}" height="{line_h}"/>'
            for i in range(rules)
        )
        return (
            f'<g fill="{INK}" transform="translate({x0} -118)">'
            f'<rect width="112" height="{rule_h}"/>'
            f'<g opacity="{opacity}">{rows}</g></g>'
        )

    return (
        f'<path d="M -186 -178 L 0 -150 L 0 150 L -186 178 Z" fill="{SHEET}" '
        f'stroke="{INK}" stroke-width="{stroke}" stroke-linejoin="round"/>'
        f'<path d="M 186 -178 L 0 -150 L 0 150 L 186 178 Z" fill="{SHEET}" '
        f'stroke="{INK}" stroke-width="{stroke}" stroke-linejoin="round"/>'
        f'<line x1="0" y1="-150" x2="0" y2="150" stroke="{INK}" stroke-width="{stroke}"/>'
        + leaf(-156, ".55")
        + leaf(44, ".40")
    )


def square(body: str, *, ground: str | None) -> str:
    back = f'<rect width="512" height="512" fill="{ground}"/>' if ground else ""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" '
        f'width="512" height="512">{back}'
        f'<g transform="translate(256 256)">{body}</g></svg>'
    )


def lockup(*, ground: str | None) -> str:
    word, width = outline("NEWSPRINT", WORDMARK_FONT, 88, tracking=2.0)
    tag, _ = outline("your newsletters, on paper", TAGLINE_FONT, 30, tracking=0.3)
    back = f'<rect width="1010" height="300" fill="{ground}"/>' if ground else ""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1010 300" '
        f'width="1010" height="300">{back}'
        f'<g transform="translate(140 150) scale(0.60)">'
        f"{mark(stroke=20, rules=3, rule_h=22, line_h=16, gap=44)}</g>"
        '<g transform="translate(300 0)">'
        f'<rect x="0" y="92" width="{width:.0f}" height="13" fill="{INK}"/>'
        f'<g fill="{INK}" transform="translate(0 190)">{word}</g>'
        f'<g fill="{INK}" opacity=".70" transform="translate(3 232)">{tag}</g>'
        "</g></svg>"
    )


FILES = {
    # the full mark, for anywhere it is shown at size
    "mark.svg": square(
        mark(stroke=14, rules=5, rule_h=16, line_h=11, gap=30), ground=PAPER
    ),
    "mark-bare.svg": square(
        mark(stroke=14, rules=5, rule_h=16, line_h=11, gap=30), ground=None
    ),
    # fewer, heavier lines: what survives a favicon
    "icon.svg": square(
        mark(stroke=20, rules=3, rule_h=22, line_h=16, gap=44), ground=PAPER
    ),
    "logo.svg": lockup(ground=PAPER),
    "logo-bare.svg": lockup(ground=None),
}

PNGS = {"mark.svg": (512,), "icon.svg": (32, 64, 128, 256), "logo.svg": (1010,)}


def main() -> None:
    for name, svg in FILES.items():
        (HERE / name).write_text(svg)
    import cairosvg  # type: ignore[import-not-found]

    for name, sizes in PNGS.items():
        for size in sizes:
            stem = Path(name).stem
            out = HERE / (f"{stem}.png" if len(sizes) == 1 else f"{stem}-{size}.png")
            cairosvg.svg2png(url=str(HERE / name), write_to=str(out), output_width=size)
    print(
        f"wrote {len(FILES)} svg and "
        f"{sum(len(s) for s in PNGS.values())} png into {HERE}"
    )


if __name__ == "__main__":
    main()
