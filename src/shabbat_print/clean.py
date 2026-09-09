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

Phase 1 dropped every image outright. Phase 7 kept every image dropped but
left a text placeholder in the flow for a small, carefully chosen subset:
"[figure: US-China trade balances as a percent of GDP, 2010-2026]", built
from the image's own `alt` attribute. See _figure_placeholder_text for
that selection rule and why it is deliberately narrow (measured against
the fixture corpus: a loose width+alt-length rule yields roughly 1.6
placeholders per newsletter, dominated by decorative illustrations and
machine-generated alt text; restricting to alt text that reads as data - a
chart, a survey, a GDP figure - cuts that to roughly one every five
newsletters, which is the version implemented here).

Phase 8 (this revision, see charts.md) is the first to actually keep an
image: an earlier survey judged images by alt text, found nothing but ads
and mastheads, and concluded the archive held no real charts. That was
wrong - Noahpinion's own charts all carry alt="" and were invisible to it.
The signal that works is the surrounding TEXT, not the image itself: an
author introducing a figure as evidence writes a sentence that ends in a
colon ("Here's a graphical comparison:"), and often follows the image with
a "Source:" caption. _is_argument_figure implements that rule, reading the
nearest non-blank text immediately before and after the <img> in document
order. Measured over 601 archived messages, 10.6% of wide images carry a
colon lead-in and 0.9% a Source-style caption; 88.5% carry neither - that
88.5% is the decoration this module has always dropped. See
_is_argument_figure's own docstring for the exact rule.
"""

import re
from collections.abc import Iterator
from dataclasses import replace

from bs4 import BeautifulSoup, CData, NavigableString, Tag

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

_IMAGE_DROPPED = "no lead-in or caption (decoration)"
_IMAGE_PLACEHOLDER = "kept as a text placeholder (figure)"

# Below this, an image reads as a spacer, an icon, or a tracking pixel,
# never a chart or figure worth a placeholder. Present on ~96% of images
# in the fixture corpus, so requiring it costs very little coverage.
_FIGURE_MIN_WIDTH_PX = 300

# How much of the alt text a placeholder keeps before truncating with an
# ellipsis - long enough that the user's own worked example ("US-China
# trade balances as a percent of GDP, 2010-2026", 57 characters) survives
# untouched, short enough to stay a one-line note rather than a caption.
_FIGURE_ALT_MAX_CHARS = 70

# Explicit, high-confidence chrome alt text the corpus shows is highly
# stereotyped - matched by exact (casefolded) equality, not substring, the
# same discipline is_full_line_chrome uses for a whole-line match rather
# than PHRASES' substring match: an "Ad" here is a bare CTA label, not a
# real word appearing inside a longer, legitimate alt text.
_CHROME_ALT_TEXT: frozenset[str] = frozenset(
    text.casefold()
    for text in (
        "Ad",
        "Filled Star",
        "Share on Facebook",
        "Tweet this Story",
        "Post to LinkedIn",
        "Email this Story",
        "Text this Story",
        "Start writing",
        "Article Image",
    )
)

# A width attribute's leading digits, tolerating a trailing unit ("600px")
# or surrounding whitespace. Anything that does not start with a digit
# (e.g. "auto", "100%") is not a pixel width this rule can judge, and the
# image is treated the same as one with no width attribute at all.
_WIDTH_PX = re.compile(r"\s*(\d+)")

# Found scanning the fixture corpus (the "verify" pass, not the design
# spec): Axios's own section-banner illustrations describe themselves as
# "Illustration of bar chart columns forming a briefcase" and "Animated
# illustration of a pair of sparkles moving up a stock chart, leaving
# trendlines behind them" - decorative motif art that happens to mention a
# chart, not a real data figure. Self-describing as an "illustration" is a
# more reliable signal of decorative art than a data word is a signal of
# real data, so it overrides a _DATA_ALT_PATTERN match rather than
# competing with it as just another exclusion.
_ILLUSTRATION_ALT = re.compile(r"\billustrations?\b", re.IGNORECASE)

# Alt text that reads as data - a chart, a survey, a GDP figure - rather
# than a decorative illustration or a machine-generated caption. This is
# deliberately the narrow list from the design spec, not a broader family:
# matched with \b so "graph" cannot match inside "paragraph" or
# "biography", the same false-positive shape PHRASES' bare substring
# matching would invite here. A few simple plurals are included
# (charts, graphs, ...) since substring matching is not doing that work
# for us anymore; irregular or rarer inflections are deliberately left
# out rather than guessed at.
_DATA_ALT_TERMS: tuple[str, ...] = (
    "chart",
    "charts",
    "graph",
    "graphs",
    "figure",
    "figures",
    "plot",
    "plots",
    "index",
    "indexes",
    "indices",
    "yield",
    "yields",
    "inflation",
    "unemployment",
    "gdp",
    "percent",
    "percentage",
    "percentages",
    "share of",
    "trend",
    "trends",
    "survey",
    "surveys",
)
_DATA_ALT_PATTERN = re.compile(
    r"\b(?:" + "|".join(_DATA_ALT_TERMS) + r")\b", re.IGNORECASE
)

# Also found scanning the corpus: an alt text that is nothing but a bare
# category word ("Figure") names the *kind* of thing an image is, not
# what it shows - no more informative than "Article Image", already
# excluded above on the same grounds. Only the category nouns a real alt
# text could plausibly be reduced to alone are listed here (not "gdp" or
# "inflation", which are never themselves a whole alt text in the corpus,
# only ever part of a real description).
_BARE_GENERIC_ALT: frozenset[str] = frozenset(
    {"chart", "charts", "graph", "graphs", "figure", "figures", "plot", "plots"}
)


def _image_width_px(image: Tag) -> int | None:
    raw = image.get("width")
    if raw is None:
        return None
    match = _WIDTH_PX.match(str(raw))
    return int(match.group(1)) if match else None


# charts.md's colon-lead-in rule, restated as a family: the text right
# before a figure most often ends in a bare colon ("...between the two
# countries:"), but a few real lead-ins from the archive keep going after
# the phrase itself ("Here's the chart below, showing...") with no colon
# at the very end. Matched anywhere in the preceding text's tail, not just
# at its end, so either shape counts.
_LEAD_IN_PHRASE = re.compile(
    r"here'?s\s+(?:the|a)\s+(?:chart|graph|data|breakdown)\b", re.IGNORECASE
)

# The other half of the same signal: a caption crediting or labelling an
# image right after it - "Source: Census Bureau", "Chart: ...", "Fig. 3:
# ...". `Data` is required to carry its own colon (data must fullmatch
# "data:", not just start with the word) because the bare word alone
# starts a lot of ordinary prose in a way "Source" or "Figure" at the very
# start of a caption line does not.
_CAPTION_LEAD = re.compile(
    r"^(?:source\b|chart\b|figure\b|fig\.|credit\b)|^data\s*:", re.IGNORECASE
)

# The third caption shape: a screenshot the author is citing, followed by
# nothing but a parenthesised link to the original post. Platformer's
# "Those good posts" section is the archetype - each social-media
# screenshot is followed by its own <p>(<a>Link</a>)</p> and nothing else.
# The images carry empty alt text and no lead-in colon, so neither
# existing gate sees them, and dropping them left a column of bare
# "(Link)" lines where the joke used to be. Measured against the 268
# archived newsletters in tests/fixtures: zero images match this anywhere
# else, so it cannot quietly readmit decorative images elsewhere.
_CITATION_CAPTION = re.compile(r"^\(\s*link\s*\)$", re.IGNORECASE)

# The block elements a caption can be. Checked as whole blocks rather than
# via _nearest_rendered_text because that returns the first text *node*:
# for "(<a>Link</a>)" that is the bare "(", which no caption rule could
# usefully match.
_CAPTION_BLOCK_TAGS = frozenset({"p", "div", "figcaption", "td", "li", "blockquote"})

# Same threshold _figure_placeholder_text uses for the same reason: below
# this an image reads as a spacer, an icon, or a tracking pixel, never a
# figure worth keeping - and tracking pixels in particular are <=3px, so
# they fail this gate (and are never fetched) long before any lead-in or
# caption text is even examined.
_ARGUMENT_FIGURE_MIN_WIDTH_PX = _FIGURE_MIN_WIDTH_PX


def _nearest_rendered_text(image: Tag, *, forward: bool) -> str | None:
    """The nearest non-blank text in document order before (forward=False)
    or after (forward=True) `image` - across sibling and container
    boundaries alike, since a lead-in sentence and its chart are often in
    separate but adjacent <p> tags, not the same one.

    Only genuine renderable text counts: an HTML comment (Outlook's own
    MSO conditional markup) or other non-visible NavigableString subtype
    is skipped, the same discipline _rendered_lines applies for the same
    reason (see that function's own comment on
    Tag.MAIN_CONTENT_STRING_TYPES) - `string=True` alone would also match
    those.
    """
    walker = image.find_all_next if forward else image.find_all_previous
    for node in walker(string=True):
        if type(node) not in (NavigableString, CData):
            continue
        text = str(node).strip()
        if text:
            return text
    return None


def _nearest_block_text(image: Tag) -> str | None:
    """All the text of the nearest block element following `image`.

    _nearest_rendered_text answers with the first text *node*, which is
    the right unit for a lead-in sentence but the wrong one for a caption
    built out of markup: Platformer's "(<a>Link</a>)" starts with a text
    node of just "(". Joining the block's own text puts the caption back
    together before any rule looks at it.
    """
    for node in image.find_all_next():
        if node.name in _CAPTION_BLOCK_TAGS:
            text = node.get_text(" ", strip=True)
            if text:
                return text
    return None


def _is_argument_figure(image: Tag) -> bool:
    """True when `image` is wide enough to be a figure AND the author
    introduced it as one - see charts.md, "The rule". Two independent
    gates, either one sufficient once the width gate passes: the nearest
    text before the image ends in a colon or a "here's the chart"-style
    phrase (_LEAD_IN_PHRASE), or the nearest text after it opens with a
    Source/Chart/Figure/Fig./Credit/Data: caption (_CAPTION_LEAD).

    Must be evaluated before _strip_presentational_attrs strips `width` -
    see this module's docstring and _strip_images's own comment on why
    that ordering is load-bearing, not incidental.
    """
    width = _image_width_px(image)
    if width is None or width < _ARGUMENT_FIGURE_MIN_WIDTH_PX:
        return False
    before = _nearest_rendered_text(image, forward=False)
    if before is not None and (
        before.rstrip().endswith(":") or _LEAD_IN_PHRASE.search(before)
    ):
        return True
    after = _nearest_rendered_text(image, forward=True)
    if after is not None and _CAPTION_LEAD.match(after):
        return True
    following_block = _nearest_block_text(image)
    return following_block is not None and bool(
        _CITATION_CAPTION.fullmatch(following_block)
    )


def _figure_placeholder_text(image: Tag, publication: str) -> str | None:
    """The text placeholder for `image`, or None if it does not qualify.

    Three conditions, all required: the image is wide enough to plausibly
    be a figure rather than a spacer or icon (_FIGURE_MIN_WIDTH_PX), its
    alt text is present and is not one of the known chrome labels or a
    bare repeat of the publication's own name, and that alt text reads as
    data (_DATA_ALT_PATTERN) rather than a decorative or machine-generated
    caption.

    Deliberately narrow, on the user's own explicit choice: a looser rule
    (width and alt length alone, no content judgement) was measured
    against 900 archived messages at 1.61 placeholders per newsletter,
    dominated by decorative illustration alt text ("Illustration of a
    burger with star-shaped pickles") and machine-generated captions
    ("Image may contain: Logo, Symbol, and Electronics") - clutter, not
    signal. This rule was measured at roughly 0.2 per newsletter instead:
    rare enough that a placeholder appearing means something.

    Two further exclusions - self-described illustrations
    (_ILLUSTRATION_ALT) and bare generic labels (_BARE_GENERIC_ALT) - were
    not in the original design spec; they were added after actually
    reading every placeholder the rule above produced across the 268-
    fixture corpus (the "verify" pass) and finding two real false
    positives: Axios's own decorative section-banner art, which mentions
    "chart" purely as a motif, and a lone "Figure" alt text with no
    description at all. See those constants' own comments for the
    evidence.
    """
    width = _image_width_px(image)
    if width is None or width < _FIGURE_MIN_WIDTH_PX:
        return None
    alt = " ".join((image.get("alt") or "").split())
    if not alt:
        return None
    lowered = alt.casefold()
    if lowered in _CHROME_ALT_TEXT:
        return None
    if lowered == publication.strip().casefold():
        return None
    if lowered.rstrip(".") in _BARE_GENERIC_ALT:
        return None
    if _ILLUSTRATION_ALT.search(alt):
        return None
    if not _DATA_ALT_PATTERN.search(alt):
        return None
    if len(alt) > _FIGURE_ALT_MAX_CHARS:
        alt = alt[: _FIGURE_ALT_MAX_CHARS - 1].rstrip() + "…"
    return f"[figure: {alt}]"


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


def _strip_images(root: Tag, publication: str) -> tuple[int, tuple[DroppedImage, ...]]:
    """Decide each image's fate: kept, given a text placeholder, or
    dropped outright. Most images are still dropped, exactly as before -
    only the small subset _is_argument_figure recognises as an author's
    own evidence survives as a real <img>, to be fetched at render time
    (see render.py). No other image is ever fetched: only its `src` is
    recorded here, for reporting, never dereferenced.

    Must run before _strip_presentational_attrs: both _is_argument_figure
    and _figure_placeholder_text read the `width` attribute that pass
    later strips (see this module's docstring on _PRESENTATIONAL_ATTRS and
    clean_document's ordering comments) - the width gate silently never
    firing is exactly the failure mode charts.md warns this ordering
    guards against. A kept image is left exactly as-is here (its own
    width/height/style still get stripped by the later, unconditional
    pass, same as any other retained tag; render.py's CSS caps its display
    width instead - see that module). For the smaller, non-overlapping
    subset _figure_placeholder_text recognises among what is NOT kept, the
    <img> is replaced with a small, textual placeholder element so it
    keeps its place in the document's flow like any other line; every
    other image is decomposed exactly as before.
    """
    dropped = []
    kept = 0
    for image in root.find_all("img"):
        src = image.get("src", "")
        if _is_argument_figure(image):
            kept += 1
            continue
        placeholder_text = _figure_placeholder_text(image, publication)
        if placeholder_text is None:
            dropped.append(DroppedImage(src=src, reason=_IMAGE_DROPPED))
            image.decompose()
            continue
        placeholder = image.new_tag("p", attrs={"class": "figure-placeholder"})
        placeholder.string = placeholder_text
        image.replace_with(placeholder)
        dropped.append(DroppedImage(src=src, reason=_IMAGE_PLACEHOLDER))
    return kept, tuple(dropped)


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

    Known, documented gap (found while fixing the derive-chrome spec's
    part B, the get_text("\\n", ...) line-fragmentation bug - see
    _rendered_lines): this only protects a single line UNDER SHORT_LINE.
    A block or leaf that is a single line LONGER than that - a real,
    substantial sentence that happens to mention a PHRASES word once in
    passing ("...there is an unsubscribe link somewhere below...") - gets
    no such protection, and content_ratio (whose per-line scoring is
    otherwise "one bad line among several good lines just lowers a
    block's score, never deletes anything", see is_definite_chrome_line's
    docstring) has nothing else to dilute against when there is only the
    one line. This was already true before the part B fix, confirmed by
    hand against a version of the adversarial sentence with no inline
    tags at all - fragmentation only ever accidentally shielded the
    tagged case, never fixed the underlying gap - and is unaffected by
    that fix in either direction. It costs nothing across the 268-fixture
    corpus (see the derive-chrome measurement: zero real newsletters are
    shaped like "a single paragraph and nothing else"), and no safe
    general fix was found: widening this guard to any single line (not
    just a short one) was tried and reverted, because it also spares
    genuine one-paragraph disclosure footers ("You received this email
    because you signed up for newsletters from Axios...") that must still
    be removed and have no other line to be diluted against either - the
    same shape as the sentence this gap is about, just with the opposite
    correct answer. Like the "Have a great weekend, / Jon" gap in
    boilerplate.py's PHRASES comment, this is left as a documented risk
    rather than patched with an untested heuristic; see
    test_leaf_tags_are_kept_whole_not_fragmented for how the tests handle
    it (a lone paragraph with nothing around it is not how any real
    newsletter in the corpus is shaped, so the test uses a realistic
    multi-paragraph document instead of asserting a guarantee that was
    never actually available).
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

    The empty-text branch below predates Phase 8 (charts.md) and was
    written when every image had already been resolved by _strip_images
    before this function ever ran - a top-level block with no text was
    always dead layout scaffolding. That is no longer true: a kept
    argument figure has no text of its own either, and it, or a table
    wrapping only it, can easily be root's own direct child (a chart is
    rarely the very last thing in its own <p>). `block.find("img")` is
    unambiguous at this point in the pipeline - _strip_images has already
    run, so the only <img> tags left anywhere are the ones it decided to
    keep - so this is the third place (with _is_argument_figure's own
    width-before-stripping ordering, and _has_visible_text below) that
    would otherwise silently undo a keep decision this module just made.
    Found the same way as the other two: by reading this pass's own logic
    against the new keep-path, not by a failing test surfacing it first.
    """
    dropped: list[DroppedBlock] = []
    for block in list(root.children):
        if not isinstance(block, Tag):
            continue
        text = "\n".join(_rendered_lines(block))
        if not text:
            if block.name == "img" or block.find("img") is not None:
                continue
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
        text = "\n".join(_rendered_lines(leaf))
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
        text = "\n".join(_rendered_lines(leaf))
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


def _rendered_lines(tag: Tag) -> list[str]:
    """`tag`'s text, split into lines the way a browser actually renders
    it: a new line starts only at a genuine block boundary - an explicit
    <br>, or a non-inline tag (_is_line_boundary, already used by
    _is_standalone_line for the same "what shares a rendered line" idea) -
    never at an inline tag's own start or end.

    This replaces plain `tag.get_text("\\n", strip=True)`, which inserts
    its separator between every text fragment bs4 finds regardless of the
    tag structure between them - so a single sentence broken up by <em>,
    <a>, or <strong> (e.g. "...in France, and later in Spain") was coming
    out as several separate pseudo-lines ("in France", ", and later", "in
    Spain"), each short and unpunctuated enough for is_boilerplate_line's
    short-line fallback to score it as chrome on its own. That degraded
    content_ratio for every block containing ordinary inline markup,
    throughout this module and in scripts/derive_chrome.py, which walks
    the same cleaned output looking for chrome by frequency (derive-chrome
    spec, part B).

    Implemented as an explicit stack of open lines rather than reusing
    get_text: only a text-accumulation pass that tracks "am I inside a
    line-starting element right now" can tell an inline tag's boundary
    apart from a block one, and bs4's own separator-joining has no such
    concept.
    """
    lines: list[str] = []
    current: list[str] = []

    def flush() -> None:
        text = "".join(current).strip()
        if text:
            lines.append(text)
        current.clear()

    def walk(node: Tag | NavigableString) -> None:
        node_type = type(node)
        if node_type is NavigableString or node_type is CData:
            current.append(str(node))
            return
        if isinstance(node, NavigableString):
            # A NavigableString subclass that is not itself real text -
            # Comment (Outlook's own MSO conditional markup, e.g.
            # "<!--[if mso]>...<![endif]-->", is the one the fixture
            # corpus is full of), Declaration, Doctype, or
            # ProcessingInstruction. None of these render as visible text
            # in any browser, so none of them may ever become a "line"
            # here - matching bs4's own get_text(), whose default `types`
            # restricts to exactly {NavigableString, CData} for the same
            # reason (see Tag.MAIN_CONTENT_STRING_TYPES). tag.children
            # never yields anything but a Tag or some NavigableString
            # subclass, so this is also the point nothing further is a
            # Tag either - the branch below is reached only when neither
            # check above matched.
            return
        boundary = _is_line_boundary(node)
        if boundary:
            flush()
        for child in node.children:
            walk(child)
        if boundary:
            flush()

    for child in tag.children:
        walk(child)
    flush()
    return lines


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

    dropped = tuple(
        DroppedBlock(text=leaf.get_text(" ", strip=True), kind="duplicate_title")
        for leaf in block
    )
    for leaf in block:
        leaf.decompose()
    return dropped


# Round 3, section E: elements that render as nothing but still occupy
# block space - a spacer/image-placeholder cell left empty once its own
# image is stripped, or a publisher's own invisible preheader padding
# (used to control the preview snippet an inbox shows). Unlike every
# other check in this module, get_text(strip=True) is not enough to
# detect either: bs4's strip only removes characters Python's
# str.isspace() recognises. That covers ASCII whitespace and a few
# Unicode space characters (NBSP, FIGURE SPACE, NARROW NO-BREAK SPACE),
# but not the zero-width and other format characters - soft hyphen,
# combining grapheme joiner, zero-width space/joiner/non-joiner, word
# joiner, BOM - that real preheader padding is actually built from, so
# those survive get_text(strip=True) as if they were real text.
# content_ratio then scores a padding div as a substantial "line" no
# PHRASES entry recognises, and _is_protected_heading's short-line
# exception - built to spare a genuine short subtitle - protects it
# outright, the same way it protects "So far, so good". Once
# _strip_presentational_attrs removes the style="display:none" that hid
# it from an email client, an untouched padding div like this renders as
# a tall blank gap on the printed page.
_INVISIBLE_CHARS = (
    "\u00a0"  # NO-BREAK SPACE
    "\u00ad"  # SOFT HYPHEN
    "\u034f"  # COMBINING GRAPHEME JOINER
    "\u180e"  # MONGOLIAN VOWEL SEPARATOR
    "\u200b"  # ZERO WIDTH SPACE
    "\u200c"  # ZERO WIDTH NON-JOINER
    "\u200d"  # ZERO WIDTH JOINER
    "\u2007"  # FIGURE SPACE
    "\u202f"  # NARROW NO-BREAK SPACE
    "\u2060"  # WORD JOINER
    "\ufeff"  # ZERO WIDTH NO-BREAK SPACE / BOM
)


def _has_visible_text(tag: Tag) -> bool:
    """True when `tag` should be kept because it has meaningful text.

    An element with literally no text at all - the empty <td> a spacer
    or an already-image-stripped placeholder leaves behind - is never
    kept, UNLESS it contains a kept <img> (Phase 8, charts.md): an image
    _strip_images decided to keep as an author's own figure has no text
    of its own, and neither does a <td> or <a> that wraps only that image,
    so an unqualified "no text -> no visible content" reading would delete
    the very thing _strip_images just spent a whole rule deciding to keep
    - not by the rule failing to fire (the width/height ordering trap
    charts.md warns about), but by a *later*, unrelated pass undoing a
    rule that fired correctly. Found by reading this pass's own logic
    against the new keep-path, not by a failing test surfacing it first;
    see test_a_kept_image_alone_in_its_cell_is_not_pruned_as_invisible.
    Beyond that, ordinary whitespace is deliberately left alone
    even when it is a tag's *entire* content, however much of it there
    is: it has real rendered width and can be there on purpose - most
    concretely, syntax-highlighted code wraps each token in its own
    <span>, including the single space between them
    ("<span>pip</span><span> </span><span>install</span>"), and treating
    that lone space as invisible glues the two words together on the
    printed page. This was tried the other way first (stripping ordinary
    whitespace the same as the invisible characters) and the fixture
    corpus caught it directly: mostly-python-ghost-io-oldest.eml's
    "pip install gh-profiler" code sample lost its spaces and ran the
    words together. Only when the whitespace-and-invisible-stripped text
    is empty AND the raw text contains at least one genuinely invisible
    character - proof this is padding, not deliberate spacing, since
    ordinary code and prose never contain a soft hyphen or a zero-width
    joiner on their own - is the element pruned. Real preheader padding
    always satisfies this: it interleaves ordinary spaces with invisible
    characters, so it is never mistaken for plain spacing.
    """
    text = tag.get_text()
    if not text:
        return tag.find("img") is not None
    if re.sub(rf"[\s{re.escape(_INVISIBLE_CHARS)}]+", "", text):
        return True
    return not any(char in _INVISIBLE_CHARS for char in text)


def _prune_invisible_elements(root: Tag) -> int:
    """Remove any element with no visible text, anywhere in the document.

    Unlike every other pass in this module, this one is not a judgement
    call: an element with no visible text at all cannot be article
    content, headline, dateline, or byline, so there is no threshold and
    no heading guard to apply - the guard exists to protect short
    genuine text from being *mis-scored*, and there is no text here to
    mis-score. <br>, <hr>, and <img> are the exceptions: <br>/<hr> are
    legitimately textless and carry real meaning (a line break, a rule);
    <img> is legitimately textless too, and _has_visible_text's own
    `tag.find("img")` check protects a *container* wrapping a kept image,
    but that check looks at descendants, not the tag itself - the <img>
    element is itself one of the tags find_all(True) visits directly, so
    it needs its own exemption here the same way <br>/<hr> do (Phase 8,
    charts.md: before this, every image had already been resolved to a
    decision by _strip_images, so any <img> reaching this pass was
    unreachable; now a kept one survives all the way to render.py).

    Runs last, after every other pass, so it prunes whatever those passes
    left behind rather than racing them - removing an empty cell earlier
    could only ever help a later pass's ratio arithmetic in the same
    direction pruning does anyway.

    A single pass is enough, with no repeat-until-stable loop needed to
    cascade an emptied child up through its layout wrappers (a <td> that
    loses its only image, then its <tr>, then its <tbody>, then its
    <table>): find_all(True) visits a tag before its own descendants (an
    ancestor cannot appear later in document order than a tag inside it),
    and get_text() is always recursive, so a container's own visibility
    already reflects the *eventual* state of everything inside it, not
    just what has been individually decomposed so far - a <table> whose
    only content is one empty <td> reads as having no visible text from
    the very first check, before that <td> has been visited at all, and
    decomposing the <table> right there takes the whole empty subtree
    with it in one call. Confirmed directly (not just reasoned about):
    calling .decompose() on a tag whose ancestor was already decomposed
    earlier in the same pass, or calling get_text() on one, is harmless -
    both simply see no text - so a later, redundant visit to an
    already-gone descendant in this same pass's tag list can never raise
    or double-count.
    """
    removed = 0
    for tag in root.find_all(True):
        if tag.name in ("br", "hr", "img"):
            continue
        if not _has_visible_text(tag):
            tag.decompose()
            removed += 1
    return removed


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
    # Must run before _strip_presentational_attrs, below: the figure-
    # placeholder judgement inside _strip_images reads the `width`
    # attribute that pass strips. Confirmed here, not assumed - see
    # _strip_images's own docstring.
    kept, dropped_images = _strip_images(root, document.publication)
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
    # Round 3, section E: runs last, after every judgement-based pass
    # above, and prunes what none of them could even see - an element
    # with no visible text is never a judgement call. Also runs before
    # _strip_presentational_attrs so the style="display:none" that hid a
    # padding div from an email client is still present when pruning
    # decides whether to remove it, though the decision itself does not
    # depend on that attribute.
    _prune_invisible_elements(root)
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
