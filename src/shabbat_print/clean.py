"""Reduce a newsletter to its article content.

This is a deny-list, not an allow-list: everything survives except a small,
specifically-identified set of top-level blocks - script/style/etc. tags,
images, and blocks whose text both reads as chrome under
boilerplate.content_ratio and is not structurally protected (see
_is_protected_heading) - plus a leading and a trailing run of chrome
elements peeled off each end of the document by _strip_leading_chrome_run
and _strip_trailing_chrome_run (see those functions' docstrings), plus
(round 2) individual lines removed anywhere in the document by
_strip_line_chrome when their whole text is an explicit, high-confidence
chrome phrase - see that function's docstring for why matching explicit
text, rather than a score, is what makes it safe to run without regard to
position. Measured against the fixture corpus, all of this together
removes under 1% of the corpus by word count; the rest of the document -
including unsubscribe lines and mailing addresses that slip past every
check - passes through untouched. This module does not by itself stop a
filler page from existing: trim.py does essentially all of that work, by
judging the document's *last* cell after rendering and dropping or
squeezing it. See trim.py's own docstring for that half of the design.

One reverted attempt is part of this module's history (commit 095d0e5):
removing whole nested BLOCKS scored by content_ratio, anywhere in the
document. That destroyed real headlines, mastheads and datelines, because
a heading-only block scores exactly 0.00 by ratio and no positive
threshold can spare it. _strip_line_chrome looks similar - it, too, can
act anywhere in the document - but is a different mechanism: its unit is
one already-atomic line matched against explicit text, never a block
judged by a score, and a single line cannot contain an article. Any change
here that starts judging a block or a ratio in the middle of the document
is that same reverted generalization again.

Phase 1 drops every image. Keeping content figures is spec phase 7 and is
deliberately not implemented here.
"""

import re
from collections.abc import Iterator
from dataclasses import replace

from bs4 import BeautifulSoup, NavigableString, Tag

from .boilerplate import (
    SHORT_LINE,
    content_ratio,
    is_definite_chrome_line,
    is_full_line_chrome,
)
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


def _strip_leading_chrome_run(root: Tag) -> tuple[DroppedBlock, ...]:
    """Remove a contiguous run of chrome from the very start of the document.

    F1 (round 2): the exact mirror of _strip_trailing_chrome_run, walking
    forward instead of backward. "Forwarded this email?" and similar
    "was this shared with you" banners sit at line 1-3 of several real
    newsletters, ahead of any content and often nested inside a container
    a block-level pass cannot see into - the same nested-container shape
    G1 fixed for the trailing side (see
    test_trailing_chrome_nested_inside_a_protected_container_is_removed).

    The safety argument is identical to the trailing rule's, direction
    reversed: this walk can only ever remove a contiguous PREFIX of the
    document's leaf list, so it structurally cannot reach into the body,
    however chrome-shaped a later element looks in isolation. See that
    function's docstring for the shared reasoning and guard.
    """
    leaves = list(_iter_text_elements(root))
    removed: list[tuple[Tag, str]] = []
    for leaf in leaves:
        text = leaf.get_text("\n", strip=True)
        if _is_protected_heading(leaf, text) or content_ratio(text) >= CHROME_RATIO:
            break
        removed.append((leaf, text))
    else:
        # Every leaf in the document read as chrome; removing all of it
        # would empty an otherwise non-empty document. Nothing is removed,
        # matching _strip_trailing_chrome_run's guard exactly.
        return ()

    if not removed:
        return ()

    for leaf, _text in removed:
        leaf.decompose()
    return tuple(DroppedBlock(text=text) for _leaf, text in removed)


def _is_line_boundary(item: object) -> bool:
    """True for anything that starts a fresh visual line: an explicit
    <br>, or any block-level (non-inline) tag. A <table>/<div> sibling
    never runs together with adjacent inline content or bare text the way
    two inline tags, or an inline tag and a bare text node, would."""
    return isinstance(item, Tag) and (
        item.name == "br" or item.name not in _INLINE_TAGS
    )


def _is_standalone_line(node: Tag | NavigableString) -> bool:
    """True when nothing else shares node's own rendered line.

    A node's immediate parent.children is partitioned into runs at each
    <br> or block-level sibling - either of those starts a fresh line, so
    they bound the run without needing to be inside it. If node's own run
    (after removing node itself) has no other non-blank text or non-empty
    inline tag, node is the only thing on its line *at that level* - but
    an inline wrapper can itself be nested inside another inline wrapper
    (<a><span>Unsubscribe</span></a>), so this ascends one level at a time,
    repeating the same check for the wrapper against *its* siblings, and
    only stops once the enclosing parent is itself block-level (a fresh
    line boundary) or the top of the document.

    This is what lets _strip_line_chrome tell "Unsubscribe" nested two
    inline tags deep, sibling of block content at the level that matters,
    apart from "unsubscribe" as one word inside a real sentence - which
    also sits inside an <a>, but that <a>'s own parent has real prose
    beside it - and, for a bare text node with no wrapping tag at all,
    apart from a chrome-shaped line that happens to sit right next to real
    prose on the very same line, with no <br> between them.
    """
    current: Tag | NavigableString = node
    while True:
        parent = current.parent
        if parent is None:
            return False
        siblings = list(parent.children)
        idx = siblings.index(current)
        start = idx
        while start > 0 and not _is_line_boundary(siblings[start - 1]):
            start -= 1
        end = idx
        while end < len(siblings) - 1 and not _is_line_boundary(siblings[end + 1]):
            end += 1
        for sibling in siblings[start : end + 1]:
            if sibling is current:
                continue
            if isinstance(sibling, NavigableString):
                if sibling.strip():
                    return False
            elif isinstance(sibling, Tag) and sibling.get_text(strip=True):
                return False
        if not isinstance(parent, Tag) or parent.name not in _INLINE_TAGS:
            return True
        current = parent


def _strip_line_chrome(root: Tag) -> tuple[DroppedBlock, ...]:
    """Remove any single element, anywhere in the document, whose whole
    rendered line of text is a high-confidence chrome phrase (F3, round 2).

    This is deliberately not the ratio/heading-guard machinery the other
    passes share: it can never reach a block or a run, only ever one
    already-atomic element - a <td>, a bare <a>, a <p> with no block
    children - matched against explicit text (boilerplate.is_full_line_
    chrome) rather than a statistical score. A single line cannot contain
    an article, which is what makes it safe to apply anywhere, unlike a
    block or a ratio threshold: that generalization was tried once
    (095d0e5), destroyed real headlines and mastheads, and was reverted -
    see this module's own docstring and the project history.

    Includes bare inline elements - a lone <a>Unsubscribe</a> sitting as a
    sibling of block content, rather than embedded in a sentence - that
    _iter_text_elements deliberately never yields on their own (its
    block_children filter drops every tag in _INLINE_TAGS unconditionally,
    so a standalone one is simply invisible to the leading/trailing walk
    and to the block-level pass alike), and bare text nodes with no
    wrapping tag at all - a postal address is often just text between two
    <br> tags, sharing a <p> with a real "© 2026" line and a company-name
    span, never its own element for a tag-only walk to find.
    _is_standalone_line is the guard that keeps either kind of candidate
    from matching "unsubscribe" quoted inside a real sentence, or a
    chrome-shaped line sitting right next to real prose with no <br>
    between them: a candidate only counts as a whole line when nothing
    else shares its own <br>-delimited run.

    Runs before every other pass: removing a known-chrome line first can
    only help the leading/trailing walks reach further (e.g. once a bare
    "Get the Bulwark app" line has been removed, whatever content used to
    sit right after it is now the walk's new stopping point, if it was
    stopping there at all), never hurt them, since is_full_line_chrome is
    strictly narrower and more explicit than the ratio-based judgement
    those walks already make on the same text.
    """
    dropped: list[DroppedBlock] = []
    candidates: list[Tag | NavigableString] = [
        *root.find_all(True),
        *root.find_all(string=True),
    ]
    for node in candidates:
        # decompose() (used below, and by Tag candidates in general)
        # deletes the `parent` attribute from every descendant outright,
        # rather than leaving it None the way extract() does - so a
        # candidate already removed as part of an earlier match above it
        # may no longer have the attribute at all.
        if getattr(node, "parent", None) is None:
            continue
        if isinstance(node, NavigableString):
            if not node.strip():
                continue
            if not _is_standalone_line(node):
                continue
            text = node.strip()
        else:
            if node.name in _INLINE_TAGS and not _is_standalone_line(node):
                continue
            text = node.get_text(" ", strip=True)
        if not is_full_line_chrome(text):
            continue
        dropped.append(DroppedBlock(text=text))
        parent = node.parent
        if isinstance(node, NavigableString):
            node.extract()
        else:
            node.decompose()
        # Clean up ancestors left with no text, so a removed line does not
        # leave a blank gap behind on the printed page.
        while (
            parent is not None
            and parent is not root
            and not parent.get_text(strip=True)
        ):
            grandparent = parent.parent
            parent.decompose()
            parent = grandparent
    return tuple(dropped)


# Round 3, section D: within the leading region only, look for the
# newsletter's own copy of its title, repeated as a second header block.
_DUPLICATE_TITLE_LEAD_WINDOW = 15

# How many leaves past a title match to look for the date line that
# confirms it. Measured against the full fixture corpus: every genuine
# duplicated header block (title, an optional subtitle, an optional
# byline, a date) resolves within 3 further leaves - never more. This is
# deliberately NOT given extra margin beyond that measured maximum: an
# earlier draft used 4, reasoning that a little slack was harmless, and a
# test built from this project's own flagship example
# (test_the_real_headline_at_the_document_start_survives_a_later_
# duplicate) proved that reasoning wrong - a genuine opening headline
# immediately followed by a genuine duplicate block let the *real*
# headline's own 4-away lookahead reach across the duplicate and find
# ITS date line, swallowing the real headline into the removal too. 3 is
# the tightest bound the evidence supports, and closes that hole.
_DUPLICATE_TITLE_LOOKAHEAD = 3

_TITLE_PUNCTUATION = re.compile(r"[^\w]+")

# A bare "Month Day[, Year]" line - Substack's own dateline ("Aug 15",
# "Jul 24, 2026"). Deliberately narrow: fullmatch, so a real sentence that
# happens to mention a date ("The event is scheduled for Sep 4.") can
# never qualify - it always has surrounding words the date line does not.
_SHORT_DATE_LINE = re.compile(
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+"
    r"\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?",
    re.IGNORECASE,
)


def _normalize_title_text(text: str) -> str:
    return _TITLE_PUNCTUATION.sub("", text).casefold()


def _title_line_matches(line: str, title: str) -> bool:
    """True when `line`, normalised by case-folding and stripping every
    non-word character, is the same as the normalised Subject or a prefix
    of it in either direction - publications truncate a long title in
    either the Subject header or their own rendered copy, so whichever one
    is shorter must be a prefix of the longer one."""
    normalized_line = _normalize_title_text(line)
    normalized_title = _normalize_title_text(title)
    if not normalized_line or not normalized_title:
        return False
    return normalized_line.startswith(normalized_title) or normalized_title.startswith(
        normalized_line
    )


def _is_short_date_line(line: str) -> bool:
    return bool(_SHORT_DATE_LINE.fullmatch(line.strip()))


def _strip_duplicate_title_block(
    root: Tag, document: Document
) -> tuple[DroppedBlock, ...]:
    """Remove Substack's own duplicated title/subtitle/author/date header
    block, when it sits in the leading region of the body (round 3,
    section D - reversing an earlier decision to leave this alone).

    render.py synthesises its own masthead and <h1> from the message's
    Subject; several publications' own body HTML repeats the same title,
    often with a subtitle, byline and date, as a second header block a few
    lines into the body - the user's own screenshot showed the title
    printed twice, six lines apart. An earlier investigation measured this
    at "under half a cell" and the controller recommended declining it, on
    two grounds both later found to be wrong: the cost is visual (a reader
    sees the same headline twice), not spatial, and - most importantly for
    this function's shape - 14 of 15 live occurrences sit in lines 1-14,
    not mid-document. This is a leading-region problem, gated to the first
    _DUPLICATE_TITLE_LEAD_WINDOW lines of the (already partly cleaned)
    body, the same way _strip_leading_chrome_run is gated to a contiguous
    run from position 0 - except the match here is anchored to a specific
    known string (the Subject), not a score, so it does not need to start
    at position 0 itself.

    A title match alone is not enough to act on: a recurring section or
    column name (the live queue's "Money Talks", "Cover Story", "Axios
    AM") is often a genuine prefix of a longer Subject purely by
    publication convention, and removing it would delete a real subtitle
    and byline. What distinguishes Substack's own duplicated header is
    that it always ends in a short, bare date line ("Aug 15", "Jul 24,
    2026") within a few lines of the title - a section name never does.
    Requiring that date line, found by scanning forward from each
    candidate match rather than assumed to be one line away, is the
    guard: measured against all 268 fixtures, it is what correctly finds
    all 14 leading duplicates (plus several more, honest, mid-window
    duplicates the earlier "under half a cell" measurement missed) while
    leaving every section-name false positive untouched.

    Matching Document.author for the "author" line, as the original
    design sketch suggested, was tried and abandoned: the live queue's own
    flagship example - "Don't Call it a Cult" - has a body byline of
    "Ellie Power" against a Document.author of "Amanda & Hélène" (the
    newsletter account's combined display name), so a literal author match
    would silently fail to remove this exact case. The date line is the
    reliable signal; whatever sits between the title and the date
    (a subtitle, a byline, both, or neither) is removed with it,
    unexamined - the same way _strip_leading_chrome_run removes a
    contiguous run without individually validating every element in it.

    If an earlier candidate title match in the window has no date line
    within reach, the scan continues to the next candidate rather than
    giving up - this is what correctly leaves a genuine opening headline
    alone (the newsletter's own real <h2>, which is never followed by a
    date) while still reaching a genuine duplicate a few lines later. See
    test_the_real_headline_at_the_document_start_survives_a_later_
    duplicate, which reproduces this project's own history: an earlier,
    reverted change destroyed this exact headline by a cruder rule.
    """
    leaves = list(_iter_text_elements(root))
    match_idx: int | None = None
    end_idx: int | None = None
    for idx in range(min(_DUPLICATE_TITLE_LEAD_WINDOW, len(leaves))):
        text = leaves[idx].get_text(" ", strip=True)
        if not _title_line_matches(text, document.title):
            continue
        for later in range(
            idx + 1, min(idx + 1 + _DUPLICATE_TITLE_LOOKAHEAD, len(leaves))
        ):
            if _is_short_date_line(leaves[later].get_text(" ", strip=True)):
                match_idx, end_idx = idx, later
                break
        if match_idx is not None:
            break
    if match_idx is None or end_idx is None:
        return ()

    block = leaves[match_idx : end_idx + 1]
    if len(block) >= len(leaves):
        # Removing the whole block would empty an otherwise non-empty
        # document - the same guard the leading/trailing runs apply.
        return ()

    dropped = tuple(DroppedBlock(text=leaf.get_text(" ", strip=True)) for leaf in block)
    for leaf in block:
        leaf.decompose()
    return dropped


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
    # Round 2, F3: the line-level phrase pass runs first, on the untouched
    # tree. It matches explicit text rather than a score, so running it
    # early is always safe (see _strip_line_chrome's docstring) and can
    # only help the two directional walks below reach further, never
    # cause them to remove something they otherwise would not have.
    dropped_lines = _strip_line_chrome(root)
    # Leading- and trailing-run removal run next, in either order (each
    # can only ever remove a run from its own end of the document, so they
    # cannot interfere with one another), before the block-level pass:
    # they can reach chrome nested inside a block _strip_chrome_blocks
    # would otherwise keep whole (see _strip_trailing_chrome_run). The
    # block-level pass then still catches remaining mid-document chrome
    # blocks that are themselves top-level, which the run-only passes
    # structurally cannot touch.
    dropped_leading = _strip_leading_chrome_run(root)
    # Round 3, section D: with the leading chrome run already gone, the
    # "first 15 lines" this pass is gated to are spent on the newsletter's
    # own content rather than on a "Forwarded this email?" banner ahead of
    # it. Runs before the trailing run and the block-level pass, neither
    # of which this leading-region pass could ever conflict with.
    dropped_duplicate_title = _strip_duplicate_title_block(root, document)
    dropped_trailing = _strip_trailing_chrome_run(root)
    dropped_blocks = _strip_chrome_blocks(root)
    _strip_presentational_attrs(root)

    return replace(
        document,
        html=root.decode_contents().strip(),
        images_kept=kept,
        images_dropped=dropped_images,
        blocks_dropped=(
            *dropped_lines,
            *dropped_leading,
            *dropped_duplicate_title,
            *dropped_trailing,
            *dropped_blocks,
        ),
    )
