"""Reduce a newsletter to its article content.

This is an allow-list, not a deny-list: the output contains what the cleaner
judged to be content, so chrome is never rendered rather than rendered and
then removed. That is what stops a filler page from existing in the first
place.

Phase 1 drops every image. Keeping content figures is spec phase 7 and is
deliberately not implemented here.
"""

from dataclasses import replace

from bs4 import BeautifulSoup, Tag

from .boilerplate import content_ratio
from .models import Document, DroppedImage

# Removed outright, wherever they appear.
_NEVER_CONTENT = ("script", "style", "noscript", "iframe", "form", "button", "head")

# Wrappers an email layout hides its content inside.
_CONTAINERS = frozenset(
    {"div", "table", "tbody", "tr", "td", "center", "article", "section", "main"}
)

# A block whose text scores below this is chrome, not content.
CHROME_RATIO = 0.15

_IMAGES_DEFERRED = "images deferred to phase 7"

# Sender layout attributes stripped from every retained tag. We supply our
# own typography and page box, so inheriting a sender's fixed widths is
# unnecessary as well as risky: a template built for a ~600px browser
# column overflows a quarter-sheet cell's text block by roughly 2x, and
# fixed widths paired with overflow:hidden are the mechanism behind text
# clipped mid-word on the printed page. `class` is left alone - harmless
# with no stylesheet attached, and useful for debugging.
_PRESENTATIONAL_ATTRS = ("style", "width", "height", "bgcolor", "align")


def _content_root(soup: BeautifulSoup) -> Tag:
    """Descend through single-child layout wrappers to the real content."""
    node: Tag = soup.body if soup.body is not None else soup
    while True:
        children = [
            child
            for child in node.children
            if isinstance(child, Tag) and child.get_text(strip=True)
        ]
        if len(children) == 1 and children[0].name in _CONTAINERS:
            node = children[0]
            continue
        return node


def _strip_images(root: Tag) -> tuple[int, tuple[DroppedImage, ...]]:
    dropped = []
    for image in root.find_all("img"):
        dropped.append(DroppedImage(src=image.get("src", ""), reason=_IMAGES_DEFERRED))
        image.decompose()
    return 0, tuple(dropped)


def _strip_chrome_blocks(root: Tag) -> None:
    """Remove any top-level block whose text is overwhelmingly chrome.

    Applied to every block, not only trailing ones: "view in browser" bars sit
    at the top, and unsubscribe blocks at the bottom.
    """
    for block in list(root.children):
        if not isinstance(block, Tag):
            continue
        text = block.get_text("\n", strip=True)
        if not text or content_ratio(text) < CHROME_RATIO:
            block.decompose()


def _strip_presentational_attrs(root: Tag) -> None:
    """Drop inherited sender layout attributes from every retained tag."""
    for tag in (root, *root.find_all(True)):
        for attr in _PRESENTATIONAL_ATTRS:
            tag.attrs.pop(attr, None)


def clean_document(document: Document) -> Document:
    soup = BeautifulSoup(document.html, "lxml")
    for tag_name in _NEVER_CONTENT:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    root = _content_root(soup)
    kept, dropped = _strip_images(root)
    _strip_chrome_blocks(root)
    _strip_presentational_attrs(root)

    return replace(
        document,
        html=root.decode_contents().strip(),
        images_kept=kept,
        images_dropped=dropped,
    )
