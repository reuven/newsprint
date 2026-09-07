"""Reduce a newsletter to its article content.

This is a deny-list, not an allow-list: everything survives except a small,
specifically-identified set of top-level blocks - script/style/etc. tags,
images, and blocks whose text both reads as chrome under
boilerplate.content_ratio and is not structurally protected (see
_is_protected_heading) - plus a trailing run of chrome elements peeled off
the very end of the document by _strip_trailing_chrome_run (see that
function's docstring). Measured against the 102-fixture corpus, that removes
under 0.5% of the corpus by word count; the rest of the document - including
unsubscribe lines and mailing addresses that slip past the ratio check -
passes through untouched. This module does not by itself stop a filler page
from existing: trim.py does essentially all of that work, by judging the
document's *last* cell after rendering and dropping or squeezing it. See
trim.py's own docstring for that half of the design.

Phase 1 drops every image. Keeping content figures is spec phase 7 and is
deliberately not implemented here.
"""

from collections.abc import Iterator
from dataclasses import replace

from bs4 import BeautifulSoup, Tag

from .boilerplate import SHORT_LINE, content_ratio, is_definite_chrome_line
from .models import Document, DroppedBlock, DroppedImage

# Removed outright, wherever they appear.
_NEVER_CONTENT = ("script", "style", "noscript", "iframe", "form", "button", "head")

# Wrappers an email layout hides its content inside.
_CONTAINERS = frozenset(
    {"div", "table", "tbody", "tr", "td", "center", "article", "section", "main"}
)

# A block whose text scores below this is chrome, not content.
CHROME_RATIO = 0.15

# Structural headings: a block containing one of these is never decomposed,
# however its text scores under content_ratio.
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# Formatting tags that sit inline within a run of prose. A tag in this set
# never counts as a content-bearing child of its own for
# _iter_text_elements's descent: an <a> inside a <p> is part of that
# paragraph, not a separate element in its own right, which is what keeps a
# paragraph from being torn apart looking for its worst line (see
# test_leaf_tags_are_kept_whole_not_fragmented).
_INLINE_TAGS = frozenset(
    {
        "a",
        "span",
        "br",
        "em",
        "strong",
        "b",
        "i",
        "u",
        "small",
        "sub",
        "sup",
        "s",
        "code",
        "abbr",
        "cite",
        "mark",
        "q",
        "time",
        "wbr",
        "font",
    }
)

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


def _is_protected_heading(block: Tag, text: str) -> bool:
    """A block spared from ratio-based removal even though it may score as
    chrome under content_ratio.

    is_boilerplate_line's short-line fallback treats any line under
    SHORT_LINE characters without sentence punctuation as chrome, so a
    block that is *only* a headline, masthead, subtitle, or dateline scores
    exactly 0.00 - the same as a link-roundup item - and no positive ratio
    threshold could ever spare it. An earlier version of this heuristic
    used content_ratio alone and silently destroyed real headlines,
    subtitles, and mastheads that happened to be short. Guard those
    structurally instead of by score:

    - a block containing an h1-h6 is always spared, regardless of what
      else is in it;
    - a block whose entire text is a single line shorter than one full
      line of prose is spared too, *unless* that line is itself a
      confident chrome signal (a known phrase, or a bare URL) - a phrase
      match like "Unsubscribe" must still be removed however short it is.
    """
    if block.find(_HEADING_TAGS) is not None:
        return True
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        return False
    line = lines[0]
    return len(line) < SHORT_LINE and not is_definite_chrome_line(line)


def _strip_chrome_blocks(root: Tag) -> tuple[DroppedBlock, ...]:
    """Remove any top-level block whose text is overwhelmingly chrome.

    Applied to every block, not only trailing ones: "view in browser" bars sit
    at the top, and unsubscribe blocks at the bottom. Every removal is
    reported, the way trim.py reports a dropped cell, so a wrong removal is
    visible rather than invisible.
    """
    dropped: list[DroppedBlock] = []
    for block in list(root.children):
        if not isinstance(block, Tag):
            continue
        text = block.get_text("\n", strip=True)
        if not text:
            block.decompose()
            continue
        if _is_protected_heading(block, text):
            continue
        if content_ratio(text) < CHROME_RATIO:
            dropped.append(DroppedBlock(text=text))
            block.decompose()
    return tuple(dropped)


def _iter_text_elements(node: Tag) -> Iterator[Tag]:
    """Yield the document's leaf content elements, in document order.

    A "leaf" is a tag that holds its text directly, rather than only by
    containing further block-level children: a <p>, an <h1>-<h6>, an <li>,
    or a <div>/<td> whose only descendants are inline formatting tags. A
    container with multiple such children (or one, non-inline child) is
    never itself yielded - only its content-bearing children are, found by
    recursing - so a wrapping <div> around several paragraphs is never
    treated as one indivisible unit the way _strip_chrome_blocks treats
    root.children.
    """
    block_children = [
        child
        for child in node.children
        if isinstance(child, Tag)
        and child.name not in _INLINE_TAGS
        and child.get_text(strip=True)
    ]
    if not block_children:
        if node.get_text(strip=True):
            yield node
        return
    for child in block_children:
        yield from _iter_text_elements(child)


def _strip_trailing_chrome_run(root: Tag) -> tuple[DroppedBlock, ...]:
    """Remove a contiguous run of chrome from the very end of the document.

    This is the safe cousin of an earlier, reverted attempt that examined
    blocks anywhere in the document and destroyed real content as a
    result (a heading, a masthead, a dateline - see clean_document's
    docstring and the module history for the specifics). The difference
    is the shape of the walk, not a tuned threshold: starting from the
    document's last leaf element and moving backwards, each element is
    removed only while it is chrome (by the same rules
    _strip_chrome_blocks already uses - content_ratio plus the heading
    guard), and the walk stops for good at the first element judged to be
    genuine content. Nothing before that point is ever inspected, so this
    cannot reach into the middle of an article, however chrome-shaped a
    given element looks in isolation.

    Guard: if the walk would reach the very first element without ever
    finding genuine content - the whole remaining document reads as
    chrome - nothing is removed. A newsletter that is entirely chrome
    should be reported (by whatever ran clean_document), not silently
    vanished by this pass; _strip_chrome_blocks, which runs after this
    one, already has its own, separately-tested behaviour for that case.
    """
    leaves = list(_iter_text_elements(root))
    removed: list[tuple[Tag, str]] = []
    for leaf in reversed(leaves):
        text = leaf.get_text("\n", strip=True)
        if _is_protected_heading(leaf, text) or content_ratio(text) >= CHROME_RATIO:
            break
        removed.append((leaf, text))
    else:
        # The loop ran to completion without ever breaking: every leaf in
        # the document was judged chrome. Removing all of it would empty
        # an otherwise non-empty document, which this pass must not do.
        return ()

    if not removed:
        return ()

    removed.reverse()  # back to document order, for reporting and removal
    for leaf, _text in removed:
        leaf.decompose()
    return tuple(DroppedBlock(text=text) for _leaf, text in removed)


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
    kept, dropped_images = _strip_images(root)
    # Trailing-run removal runs first, on the untouched tree: it can only
    # ever remove a strict suffix, so running it before the block-level
    # pass lets it reach chrome nested inside a block _strip_chrome_blocks
    # would otherwise keep whole (see _strip_trailing_chrome_run). The
    # block-level pass then still catches leading and mid-document chrome
    # blocks, which the suffix-only pass structurally cannot touch.
    dropped_trailing = _strip_trailing_chrome_run(root)
    dropped_blocks = _strip_chrome_blocks(root)
    _strip_presentational_attrs(root)

    return replace(
        document,
        html=root.decode_contents().strip(),
        images_kept=kept,
        images_dropped=dropped_images,
        blocks_dropped=(*dropped_trailing, *dropped_blocks),
    )
