"""Deciding what is newsletter chrome and what is newsletter content.

The rule is about what the text *is*, never how much of it there is: 1,200
characters of pure link roundup is chrome, and 200 characters of a real
closing paragraph is content.
"""

import re

PHRASES: tuple[str, ...] = (
    "unsubscribe",
    "manage your preferences",
    "manage preferences",
    "email preferences",
    "update your profile",
    "view in browser",
    "view this email",
    "you are receiving this",
    "you're receiving this",
    "you received this",
    "was this forwarded",
    "forwarded this email",
    "forward to a friend",
    "add us to your address book",
    "mailing address",
    "all rights reserved",
    "privacy policy",
    "terms of service",
    "sponsored by",
    "advertisement",
    "presented by",
    "follow us",
    "share this",
    "thanks for reading",
    "see you next week",
    "until next time",
    "©",
    # G1: added from real footer prose in the user's own printed packet.
    # Modern footers read as conversational sentences ending in periods,
    # which defeats the short-line fallback below entirely - these are
    # caught by phrase instead. "get the bulwark app" was left out as too
    # specific to one publication, and no safe general "get the ... app"
    # pattern was found that would not also match ordinary prose ("get the
    # sense", "get the picture"); the trailing-run removal in clean.py is
    # the safety net for that one gap.
    "subscribe here",
    "like comment restack",
    "restack",
    "a message from our sponsor",
    "is published by",
    "newsletter preferences",
    "review our faq",
    "give the gift of",
    "check out my masterclass",
    # Requested after a second read of the user's real printed output.
    # "need help?", "brand partnerships", and "watch now" were also
    # requested but are deliberately NOT here - all three were tried and
    # measured against the full 102-fixture corpus, and reverted on
    # concrete evidence of real content loss, not a hypothetical:
    #
    # - "watch now" removed a genuine editorial sentence in
    #   pete-aidailybrief-io.eml: "AI moved fast while I was away; and
    #   this new chapter of AI Daily Brief begins with the trends, tools,
    #   and opportunities creators should watch now." A phrase match,
    #   unlike the short-line fallback, does not care about length or
    #   position, so it strikes a real sentence that happens to end in
    #   those two words exactly as readily as a bare CTA button.
    #
    # - "need help?" and "brand partnerships" are individually narrow, but
    #   together they made a real chrome block (Puck's FAQ/brand-
    #   partnerships footer paragraph) newly chrome in jon-puck-news.eml -
    #   and that paragraph had been the trailing run's stopping point,
    #   protecting everything before it. With it reclassified as chrome,
    #   the walk continued one leaf further back and removed a genuine,
    #   two-line authorial sign-off: "Have a great weekend, / Jon". That
    #   sign-off was never independently protected - the heading guard's
    #   single-line exception only covers a block whose *entire* text is
    #   one line, and this one is two ("Have a great weekend," then
    #   "Jon"), each short and unpunctuated enough to score as chrome on
    #   its own. This is a real, structural gap - short multi-line
    #   sign-offs are not protected the way a single-line subtitle is -
    #   worth its own careful look in a future change; it is not fixed
    #   here, and these three phrases are the reason it surfaced at all.
    #
    # See the report for the full trace of both findings.
    "you received this email because",
    "to stop receiving",
    "manage all your email preferences",
    "read in app",
    # Round 3, section B: a third pass at footer prose, from a real printed
    # packet. "thanks for being a bulwark+ member" was requested by name,
    # but the fixture corpus shows the shape generalizes cleanly across
    # publications with no collision found: "Thanks for being a valued
    # subscriber" (pete-aidailybrief-io.eml) and "Thanks for being a part
    # of the club!" (serioustrouble-substack-com.eml) are both the same
    # kind of footer sign-off, immediately followed by Like/Comment/Restack
    # or a Restack button in both cases - so the general "thanks for being
    # a" is used instead of the publication-specific phrase.
    "thanks for being a",
    # "update your newsletter preferences" and "update your email
    # preferences" are subsumed by the pre-existing "newsletter
    # preferences" and "email preferences" entries above - any line
    # containing the longer phrase already contains the shorter one, so
    # these change nothing in this corpus. Kept anyway since they were
    # explicitly requested and are harmless, same treatment G1 gave
    # "you received this email because" / "manage all your email
    # preferences".
    "update your newsletter preferences",
    "update your email preferences",
    "visit our help center",
    "set up your personal rss feed",
    "is exclusively for members of",
    # Derive-chrome spec (2026-09-07), part D: a trailing self-promotional
    # paragraph reported live (edconway-substack-com.eml's "Material World
    # is a free newsletter... If you enjoyed this post, please do share it
    # with friends... Buy Trade World"). Frequency analysis alone would
    # never find this - the exact wording appears in only one publication
    # - but each phrase below is a sign-off convention, not one author's
    # voice, so it was checked against the corpus the same way any other
    # phrase here is:
    #
    # - "if you enjoyed this post" is Substack's OWN platform-generated
    #   pledge sentence ("X is free today. But if you enjoyed this post,
    #   you can tell X that their writing is valuable by pledging a
    #   future subscription.") - confirmed appearing verbatim across
    #   several unrelated authors' newsletters (Bite Code!, Simon
    #   Willison, Data Science Education Program), which is what makes it
    #   templated chrome rather than personal wording.
    # - "share it with friends" is the same shape as the already-accepted
    #   "share this" and "forward to a friend" above.
    # - "is a free newsletter" is an about-this-newsletter meta-sentence,
    #   the same shape as the already-accepted "is published by".
    #
    # "order a copy of", "pre-order", and "let me know what you think"
    # were also requested but are deliberately NOT here - each was
    # checked against the live archive and found colliding with genuine
    # editorial content: "order a copy of" and "pre-order" both appear in
    # real book/cookbook recommendations and announcements unrelated to
    # self-promotion (Sebastian Raschka's own reading list, "five
    # weeknight dishes"' own cookbook-release announcement - core
    # editorial content for a cooking newsletter, not incidental chrome);
    # "let me know what you think" is a common, versatile authorial
    # engagement closer used constantly about real article content (David
    # Epstein: "If you take a look, let me know what you think. And enjoy
    # the World Cup final!"; Peter Yang: "Get my free /no-ai-slop skill on
    # Github and let me know what you think!"). Adding any of these three
    # would strike real content exactly the way "watch now" did as a
    # substring, above.
    "if you enjoyed this post",
    "share it with friends",
    "is a free newsletter",
    # Round 5 (the user's Axios report). These are scoring-only entries,
    # never full-line matches: each sits inside a block whose other lines
    # are long enough to read as prose, so the block scored 0.82 content
    # and held the trailing chrome run open even after the standalone
    # footer lines around it had been removed. Each is house or bulk-mail
    # language rather than editorial text, and as substrings they can only
    # lower a block's score, never delete a line on their own.
    "thanks our partners for supporting",
    "you can reach the authors by replying",
    "how we use ai in our journalism",
    "sponsorship has no influence",
    "smart brevity",
    "hands-on training or internal comms",
    "like this comms style and format",
    # The CAN-SPAM postal address every bulk sender must carry. _ADDRESS_LINE
    # already matches the bare street-address form, but not one prefixed
    # with the sender's name ("Axios, PO Box 101060, Arlington VA 22201"),
    # and widening that regex was measured to be dangerous - it fires
    # against the raw document, where it can carry a whole enclosing
    # element away (see its own comment). As a scoring-only phrase this
    # cannot delete anything by itself; it just stops such a line being
    # counted as prose, which is what was holding the trailing chrome run
    # open on every Axios newsletter.
    "po box",
)

_URL_ONLY = re.compile(r"^(https?://\S+|www\.\S+)$", re.IGNORECASE)

# The lead-in above a row of social icons - "Follow Axios across:", "Follow
# us on:". It ends in a colon, so the short-line fallback reads it as a
# sentence and scores it as content, which is what stopped the trailing
# chrome run dead on every Axios newsletter: it is the document's very last
# leaf, so the walk halted before removing anything at all. Bounded to a
# short brand name so it cannot match a real sentence beginning "Follow".
_FOLLOW_ACROSS_LINE = re.compile(
    r"^follow\s+(?:us|[\w'.\u2019-]+(?:\s+[\w'.\u2019-]+){0,2})"
    r"\s+(?:across|on)\s*:?$",
    re.IGNORECASE,
)
_SENTENCE_END = (".", "!", "?", '"', "'", ")", ":", "”", "’")

# A line that IS a postal address - a street address, or a PMB/Suite/Apt/#
# box - ending in a two-letter state and five-digit ZIP. Anchored to the
# start of the line (via fullmatch) deliberately: without that anchor this
# would also match a genuine sentence that merely quotes an address, since
# real addresses often appear mid-sentence in real prose.
_ADDRESS_LINE = re.compile(
    # NOT widened to allow a sender name before the box ("Axios, PO Box
    # 101060, Arlington VA 22201"). That was tried and reverted: measured
    # across the fixture corpus it cost 2,417 lines and reduced
    # jon-puck-news.eml from 280 lines to 1 - the match fires against the
    # raw document, where the line it hits can carry a large enclosing
    # element away with it. Counting post-clean matches (16, all genuine
    # addresses) hid that entirely.
    r"(?:\d+\s+\S.*|(?:PMB|Suite|Ste\.?|Apt\.?|#)\s*\S.*)"
    r",\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\.?",
    re.IGNORECASE,
)

# Below this, a line without sentence-ending punctuation reads as a
# link-roundup item or a navigation label rather than as prose.
SHORT_LINE = 40


def is_definite_chrome_line(line: str) -> bool:
    """True only for a confident signal: a known phrase, a bare URL, or a
    postal address.

    Unlike is_boilerplate_line, this leaves out the short-line fallback -
    "under 40 characters without sentence punctuation" - which is a weak
    heuristic that also matches a bare headline, subtitle, masthead, or
    dateline. Callers that need to tell a block of *definite* chrome apart
    from something merely short (clean.py's heading guard, in particular)
    should use this instead of is_boilerplate_line.
    """
    stripped = line.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if any(phrase in lowered for phrase in PHRASES):
        return True
    if _URL_ONLY.match(stripped):
        return True
    return bool(_ADDRESS_LINE.fullmatch(stripped))


def is_boilerplate_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if is_definite_chrome_line(stripped):
        return True
    return len(stripped) < SHORT_LINE and not stripped.endswith(_SENTENCE_END)


# Round 2, F3: phrases that are unambiguously chrome only when they
# constitute an ENTIRE line - as opposed to PHRASES above, which is matched
# as a substring anywhere in a line. Substring matching is safe only for
# the ratio-based scoring content_ratio does (one bad line among several
# good ones just lowers a block's score, never deletes anything); it is
# not safe for deleting a specific line outright, since a real sentence can
# quote or contain one of those words ("...an unsubscribe link somewhere
# below..."). A full-line match is a different, stronger claim - see
# clean.py's _strip_line_chrome, which is the only caller allowed to act on
# it by deleting the matching element outright, anywhere in the document.
_FULL_LINE_CHROME: frozenset[str] = frozenset(
    {
        "forwarded this email?",
        "forwarded this email? subscribe here for more",
        "restack",
        "a message from our sponsor",
        "unsubscribe",
        # Round 3, section A. "Like" and "Comment" were declined in round 2
        # as PHRASES substring entries - the live queue contains real
        # article sentences ("Like most of America, Dry Powder will be...",
        # "Like it or not, we now live in a world...", "Like, right now.",
        # "Share this newsletter with someone who prefers truth...") that a
        # substring match would destroy. This is a different, stronger
        # claim: matched only when one of these words IS the whole
        # rendered line (see is_full_line_chrome's docstring and
        # _strip_line_chrome's _is_standalone_line guard), which none of
        # those sentences ever are - a sentence beginning with "Like" or
        # "Share" always has more text on the same line. See
        # test_a_sentence_starting_with_like_or_share_survives.
        "share",
        "like",
        "comment",
        "share now",
        # Round 5 (the user's Axios report): a trailing footer whose lines
        # are short declarative sentences. The short-line fallback only
        # treats a line under SHORT_LINE as chrome when it has no
        # sentence-ending punctuation, so each of these scored a full 1.00
        # as content and, being the document's own tail, held the trailing
        # chrome run open. All are bare calls to action or bulk-mail
        # disclosure, never editorial text, when they are the whole line.
        "advertise with us",
        "advertise with us.",
        "learn more",
        "learn more.",
        "discover how",
        "discover how.",
        "sponsorship has no influence on editorial content",
        "sponsorship has no influence on editorial content.",
        "read in app",
        "view in browser",
        "share the bulwark",
        # Round 3b, item 1: "READ TO ME" is a Bulwark variant of "READ IN
        # APP" - the same audio/app-affordance CTA shape, requested by
        # name. "LISTEN NOW" is added alongside it after a small family
        # investigation: measured against the full 268-fixture corpus, it
        # always appears as a bare, standalone line immediately after a
        # "Listen now (NN mins) | ..." metadata line, in five other
        # publications' fixtures (datascienceeducation, johnfdickerson,
        # pragmaticengineer, serioustrouble, thepythonshow), with zero
        # collisions with real content anywhere in the corpus (172 total
        # matches of a broader trial pattern, every one of them either
        # "READ IN APP", "READ TO ME", or a bare "Listen now" line).
        #
        # A genuinely general family pattern - "read"/"listen" combined
        # with "now"/"to me"/"in app"/"aloud" - was tried and declined:
        # only these three specific combinations have fixture evidence,
        # and at least one untested combination is a real risk rather than
        # a hypothetical one. "Listen to me." is a plausible complete
        # sentence in first-person prose (an imperative, the same shape as
        # a real closing line), not just a CTA, and nothing in the corpus
        # rules that collision out. Literals only, matching this
        # frozenset's existing style.
        "read to me",
        "listen now",
        # Round 4 (derive-chrome spec, 2026-09-07): every literal below
        # was found by scripts/derive_chrome.py ranking lines that
        # SURVIVE clean_document() by how many distinct publications and
        # messages each appears in, over the full local archive (2,159
        # messages) - chrome repeats across publications that share no
        # editorial content, article prose does not (see that script's
        # own docstring and the derive-chrome report for the exact counts
        # and evidence for each). Several of these already match an
        # EXISTING PHRASES substring for ratio scoring (e.g. "unsubscribe"
        # or "privacy policy" appear inside them) but were still
        # surviving in practice: that is the documented gap in
        # clean.py's _is_protected_heading (a single rendered line longer
        # than SHORT_LINE gets no dilution to rely on when its own block
        # has too little other text) - promoting the exact observed line
        # to a whole-line match here, deleted by _strip_line_chrome
        # regardless of position or surrounding ratio, is what actually
        # reaches it. "Watch now" was requested by the user and
        # previously declined only as a PHRASES *substring* (it struck a
        # real sentence ending "...trends, tools, and opportunities
        # creators should watch now." in pete-aidailybrief-io.eml,
        # confirmed still true above in
        # test_need_help_brand_partnerships_and_watch_now_were_tried_and_
        # reverted) - as a *whole line* it is safe, following the exact
        # precedent "listen now" already sets: confirmed against 9 live
        # occurrences, always a bare CTA button immediately after a
        # "Recorded on ... · NN min" line, zero collisions, and that
        # 150-character real sentence is never, itself, equal to the two
        # words "watch now".
        "upgrade to paid",
        "leave a comment",
        "preview",
        "subscribed",
        "paid",
        "invite friends",
        "invite your friends and earn rewards",
        "change your emailprivacy policycontact uscalifornia notices",
        "connect with us on:",
        "if you received this newsletter from someone else, subscribe here.",
        "need help? review our newsletter help page or contact us for assistance.",
        # The Puck FAQ/brand-partnerships block the user reported ("Need
        # help? Review our FAQ page or contact us for assistance. For
        # brand partnerships, email ads@puck.news.") is deliberately NOT
        # here, even as a full, exact whole-line match. Round 2 declined
        # "need help?" and "brand partnerships" as PHRASES *substrings*
        # after they made this paragraph newly chrome and let the
        # trailing walk continue one leaf further into a real sign-off
        # ("Have a great weekend, / Jon") - see PHRASES' own comment. This
        # literal was tried anyway, on the theory that a full-line match
        # is a different, safer mechanism (it cannot lower any OTHER
        # block's ratio) - and measured directly against
        # jon-puck-news.eml in the derive-chrome measurement, where it
        # reproduced the EXACT same regression by a different route: once
        # _strip_line_chrome deletes this leaf outright, it is simply
        # gone from the tree before the trailing walk ever runs, so that
        # walk's new last leaf becomes "Have a great weekend, / Jon"
        # instead - a genuine short, unpunctuated two-line sign-off with
        # no protection of its own (see _is_protected_heading's own
        # comment on this exact gap), destroyed all over again. The
        # mechanism was different; the outcome was identical. Declined
        # for the same reason Round 2 declined it, confirmed against the
        # same fixture.
        "the new york times company. 620 eighth avenue new york, ny 10018",
        "a subscription gets you:",
        "copyright © the economist newspaper limited 2026. all rights reserved.",
        "registered in england and wales. no. 236383.",
        "subscribe to the timesget the new york times app",
        "follow axios on social media:",
        "download our app for ios and android",
        # A paywall/section badge ("For subscribers") that sits directly
        # beside a masthead's own title/subtitle/date lines (The
        # Economist's "Insider"/"Money Talks"/"Drum Tower" editions) -
        # never a plausible complete sentence on its own.
        "for subscribers",
        "get it in your inbox.",
        "get it in your inbox",
        "claim my free post",
        "continue reading this post for free in the substack app",
        "or upgrade your subscription. upgrade to paid",
        "update your email preferences or unsubscribe here",
        "pledge your support",
        "watch now",
        "watch on demand",
        "next show",
        "all upcoming shows",
        "add to calendar",
        "download from the app store or google play",
        (
            "download the app to stream events on the go, watch picture-in-picture, "
            "and get real-time notifications so you never miss a moment"
        ),
        (
            "visit the hub to see all upcoming events and to submit questions to "
            "our editors ahead of the discussion."
        ),
        "visit insider hub →",
        "view email online privacy policy terms & conditions",
        "unsubscribe contact us update your details",
        "photo: getty images",
        "read full story",
        "read more",
        # Not from the frequency table (both found reading the live
        # queue's own dry-run output by hand, checking the user's
        # reported strings directly - see the derive-chrome report).
        # "Listen Now." is the same CTA round 3b already vetted as
        # "listen now" (bare, unpunctuated), but William D. Cohan's own
        # Puck template renders it WITH a trailing period, which
        # normalize_full_line does not strip - so the existing entry
        # never actually matched this real occurrence. "Listen to the
        # episode here." is a new one, confirmed in the same live
        # message, in a sponsored third-party podcast ad block
        # ("Introducing InterSectors, a new podcast from White & Case"),
        # sitting right beside two "A MESSAGE FROM OUR SPONSOR" lines
        # already removed from the same document - unambiguous ad CTA
        # text, not the newsletter author's own prose. Round 3b's own
        # comment explicitly declined a broader "listen ..." family
        # pattern because "Listen to me." reads as a plausible real
        # first-person sentence; "Listen to the episode here." does not
        # have that shape (a real sentence does not open "the episode"
        # this way), so it is added as its own literal, not folded into
        # a wider pattern.
        "listen now.",
        "listen to the episode here.",
        # Not from the frequency table (a single publication, so
        # frequency alone could never surface it) - the trailing
        # self-promotional block reported live in
        # edconway-substack-com.eml (see PHRASES' own comment for the
        # rest of that block). "Buy Trade World" is Substack's own
        # ButtonCreateButton widget text (confirmed in the raw HTML:
        # `data-component-name="ButtonCreateButton"`), not a sentence -
        # without this, it is protected as a short, unpunctuated single
        # line the same way a genuine short subtitle is (clean.py's
        # _is_protected_heading), which stops the trailing walk right
        # there and leaves the whole self-promotional paragraph behind it
        # untouched. A general "Buy <title>" pattern was considered and
        # declined: "buy" is too common an imperative in ordinary prose
        # ("Buy the ticket, take the ride") for a shape that short to be
        # safe: this is a publication-specific literal because no safe
        # general pattern was found, the same way "Get the Bulwark app"
        # was declined as a literal in favour of the narrower "... app"
        # shape - here even that narrower shape is not safe.
        "buy trade world",
    }
)

# Round 3b, item 2: a publisher's own pipe/middot/bullet-separated
# navigation row, e.g. the NYT's "View in browser | nytimes.com". This is
# rendered as several inline siblings (a link, a bare "|" span, a second
# link) sitting on one visual line with no <br> between them, so it is
# invisible to _strip_line_chrome's per-node candidate walk the same way a
# lone "View in browser" sharing a line with real prose is (see that
# module's _is_standalone_line) - but the *containing* block-level
# element's own merged text (its get_text(" ", strip=True), already
# offered whole to is_full_line_chrome by the existing candidate loop -
# see clean.py's docstring) is exactly "View in browser | nytimes.com".
# No new mechanism is needed in clean.py: extending what counts as a
# whole-line chrome match here is enough.
_NAV_DELIMITER = re.compile(r"[|·•]")

# A bare domain - "nytimes.com", "bulwark.com" - with no scheme and no
# path: one or more dot-separated labels ending in a plausible top-level
# domain of at least two letters, and nothing else (fullmatch, the same
# anchoring _ADDRESS_LINE and _URL_ONLY already use). This is what keeps
# an abbreviation that happens to contain dots ("U.S.", "e.g.") from
# matching - both lack a final two-letter-or-longer label after their
# last dot.
_BARE_DOMAIN = re.compile(
    r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\.[a-z]{2,}",
    re.IGNORECASE,
)


def _is_bare_domain(segment: str) -> bool:
    return bool(_BARE_DOMAIN.fullmatch(segment))


def _is_delimited_chrome_row(text: str) -> bool:
    """True when `text` splits on '|', '·' or '•' into two or more
    non-blank segments, EVERY one of which is independently a known
    whole-line chrome match (recursing into is_full_line_chrome itself -
    "View in browser", "Unsubscribe", a postal address, ...) or a bare
    domain. Requiring every segment to qualify, not just one, is the
    safety margin this function exists to provide: a real sentence that
    happens to contain a pipe, or a nav row with one genuine content
    segment among the chrome, is never all-chrome on a per-segment basis,
    so it survives untouched. Measured against the fixture corpus: 341
    existing lines contain one of these three delimiters, and none of
    them newly matches - including a genuine engagement-stats row
    ("a month ago · 99 likes · 11 comments · Renee DiResta") that
    superficially looks like the same shape but has no segment that is
    itself known chrome.
    """
    if not _NAV_DELIMITER.search(text):
        return False
    segments = [segment.strip() for segment in _NAV_DELIMITER.split(text)]
    if len(segments) < 2 or any(not segment for segment in segments):
        return False
    return all(
        is_full_line_chrome(segment) or _is_bare_domain(segment) for segment in segments
    )


# "You received this email because ..." always continues with a
# newsletter-specific reason (who you signed up for, which list you are
# on), so it cannot be a fixed phrase - but the opening words alone are
# distinctive enough of bulk-mail disclosure language that no genuine
# editorial sentence plausibly starts this way. (The shorter substring
# "you received this" is already in PHRASES above for ratio scoring; this
# is deliberately the longer, more specific opening, since a prefix match
# is a stronger commitment than a substring match and this is the one
# used to delete a whole line.)
_FULL_LINE_CHROME_PREFIXES: tuple[str, ...] = (
    "you received this email because",
    # Round 4 (derive-chrome spec, 2026-09-07): each of these always
    # continues with something newsletter-specific (a publication name, a
    # recipient's own email address, a sender's own postal address), so
    # none can be a fixed phrase - but the opening words alone are
    # distinctive bulk-mail disclosure language no genuine editorial
    # sentence plausibly starts with, the same reasoning as the original
    # entry above. "You're currently a free subscriber to" was Part A's
    # own top-ranked sample hit (18 publications, 83 messages, in the
    # spec's own worked example) - the publication name that follows it
    # is exactly why a fixed phrase could not have caught it.
    "you’re currently a free subscriber to",
    "this email was sent by:",
    "this email was sent to:",
    "this email has been sent to",
    # Round 5 (the user's Axios report), same family as the entries above:
    # each continues with a publication or brand name, so none can be a
    # fixed phrase, and each is bulk-mail disclosure or house promotion no
    # editorial sentence opens with.
    "thank you for signing up for this",
    "you can reach the authors by replying",
    "sponsorship has no influence",
)

# "Get the Bulwark app" - the user's own report names one publication, but
# the shape "Get the <name> app" is a general CTA-button pattern, not
# something a real sentence would independently produce as its own whole
# line. A broader "get the ..." pattern (no required "app" ending) was
# tried in the previous wave and found real false positives in the fixture
# corpus ("get the picture", "get the sense"); anchoring on the "... app"
# ending keeps the generalization past the one named publication without
# reintroducing that risk.
_GET_THE_APP_LINE = re.compile(r"^get the [\w' .-]+ app$", re.IGNORECASE)

# Round 4 (derive-chrome spec, 2026-09-07): the remaining frequency-derived
# rules are shapes, not literals - see scripts/derive_chrome.py's own
# docstring for the signal and the derive-chrome report for the count each
# one cleared in the live archive (2,159 messages).

# A single character that is nothing but a bullet/separator glyph, used by
# a podcast/audio-player widget to divide adjacent metadata ("38:00 ∙
# Preview", "51:11 ∙ Paid") rather than punctuate a sentence. '∙' (BULLET
# OPERATOR, U+2219, 16 publications/165 messages) and '·' (MIDDLE DOT,
# U+00B7, 5/8) are the two the live archive actually contains; only those
# two are listed - a line cannot be shorter or more information-free than
# one bare punctuation character, so no realistic sentence collides, but
# nothing else is guessed at without its own evidence.
_BARE_SEPARATOR_CHARS: frozenset[str] = frozenset({"∙", "·"})

# A bare footnote-reference number (8 publications/16 messages for "1"
# alone; "2", "3", and "17" also appear, each individually below the
# frequency threshold but sharing the same shape). Confirmed against the
# live archive's own raw HTML, not assumed: Substack's own footnote markup
# is `<span class="footnote-number">1</span><div class="footnote-content">
# <p>More on that later – stay tuned!</p></div>` - the numeral is its own
# rendered line, entirely separate from the footnote's own text (which
# this pattern can never touch: it only ever matches the bare numeral,
# never a line with any other character on it). 1-3 digits only, so a
# 4-digit year ("2026") standing alone is never caught here - if that
# ever needs its own rule it needs its own evidence.
_BARE_FOOTNOTE_NUMBER = re.compile(r"\d{1,3}")

# A bare elapsed-time or duration readout from a podcast/video player
# widget ("0:00", 5 publications/16 messages in the live archive; "38:00",
# "51:11", "29:09" alongside it in the same messages) - never a real
# sentence, since it has no words in it at all.
_BARE_DURATION = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")

# A copyright line's own opening: "© 2026 Prof G Media" (5 publications/86
# messages). The company name and year vary by publisher, but no genuine
# editorial sentence opens with the © glyph. Matches only the START of the
# line (not fullmatch), so a copyright notice at the head of an otherwise
# longer line ("© 2026 Prof G Media. All rights reserved.") still
# qualifies as one whole chrome line once collapsed.
_COPYRIGHT_LINE_START = re.compile(r"^©")

# "Powered by beehiiv" (5 publications/67 messages). The platform varies,
# but "Powered by <one platform name>" as a short, complete line is a
# near-universal newsletter-platform footer credit - the same kind of
# closed CTA/credit shape _GET_THE_APP_LINE already generalises for an app
# badge. Only "beehiiv" is in the live archive's own evidence; the pattern
# generalises past it on the same reasoning _GET_THE_APP_LINE generalised
# past "Get the Bulwark app".
_POWERED_BY_LINE = re.compile(r"^powered by [\w.]+$", re.IGNORECASE)

# "Get more New Yorker in your inbox." (4 publications/53 messages, all
# different New Yorker newsletter editions). The publication name varies;
# the shape - fixed opening, one name in the middle, fixed close - is the
# same one _GET_THE_APP_LINE already generalises for "Get the ... app". A
# trailing period is optional since both forms were observed.
_GET_MORE_IN_INBOX_LINE = re.compile(
    r"^get more [\w' .-]+ in your inbox\.?$", re.IGNORECASE
)

# NYT Cooking's own recipe photo-credit line: "<Photographer> for The New
# York Times.[ Food Stylist[:.] <Name>.][ Prop Stylist[:.] <Name>.]" - one
# general pattern rather than the ~20 distinct names the live archive
# contains (Christopher Testani, David Malosh, Linda Xiao, Julia Gartland,
# Armando Rafael, ...), each individually too rare on its own to clear the
# frequency threshold but all sharing this one fixed shape. Every image is
# already stripped outright by clean.py, Phase 1/7 - no photo is ever
# rendered - so a credit line for a photo that no longer exists in the
# printed output is orphaned caption text, not article content; this
# pattern can only ever match the credit line itself, never a recipe's own
# text. "Food Stylist"/"Prop Stylist" are each optionally followed by ':'
# or '.' - the source itself is inconsistent (most read "Food Stylist:
# Simon Andrews.", at least one has the typo "Food Stylist. Simon
# Andrews.").
#
# Deliberately NOT case-insensitive, unlike every other pattern here: an
# unconstrained "<any text> for The New York Times." would also match a
# genuine sentence of real prose ("She used to write for The New York
# Times."), so each name is instead required to be 1-5 Title Case words -
# what every observed byline actually is, and what an ordinary sentence
# (full of lowercase function words like "used", "to", "write") is not.
# This is matched against the ORIGINAL-case collapsed text (see
# is_full_line_chrome), not the case-folded one every other check here
# uses, for exactly this reason.
_TITLE_CASE_NAME = r"[A-Z][a-zA-Z'-]*(?: [A-Z][a-zA-Z'-]*){0,4}"
_NYT_PHOTO_CREDIT = re.compile(
    rf"^{_TITLE_CASE_NAME} for The New York Times\.?"
    rf"(?: Food Stylist[:.] {_TITLE_CASE_NAME}\.)?"
    rf"(?: Prop Stylist[:.] {_TITLE_CASE_NAME}\.)?$"
)


def _normalize_full_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def is_full_line_chrome(text: str) -> bool:
    """True when `text`, taken as one whole rendered line (its internal
    line breaks collapsed to single spaces), is unambiguously chrome
    regardless of where in the document it sits.

    Unlike is_definite_chrome_line, which matches a PHRASES entry as a
    substring anywhere in the line, this requires the phrase to BE the
    whole line: "Unsubscribe" alone is chrome, but "...there is an
    unsubscribe link somewhere below..." is not, and this function says
    False for the latter - the exact adversarial case this project's
    history warns about.

    Also matches a bare postal address (reusing the same _ADDRESS_LINE
    pattern is_definite_chrome_line already uses, anchored with fullmatch
    and already proven safe against an address quoted mid-sentence). This
    closes a gap the F2 trace found live: a footer block reading "© 2026 /
    Substack Inc. / <address> / Unsubscribe" scores content_ratio just
    over CHROME_RATIO because "Substack Inc." ends in a period and so
    reads as a "sentence" under the short-line fallback - and removing the
    standalone "Unsubscribe" line only raises that ratio further, so the
    address is never reachable through the ratio path no matter what else
    this pass removes from around it. It needs the same kind of
    independent, explicit match "Unsubscribe" already has.

    Also matches a delimiter-separated navigation row (round 3b, item 2) -
    see _is_delimited_chrome_row for the rule and its safety argument.

    Round 4 (derive-chrome spec, 2026-09-07) adds several more shapes,
    each with its own comment where it is defined: a bare bullet/separator
    character, a bare footnote number, a bare player duration, a copyright
    line's own opening, a "Powered by <platform>" credit, a "Get more
    <publication> in your inbox." CTA, and NYT Cooking's own photo-credit
    line.
    """
    collapsed = _normalize_full_line(text)
    if not collapsed:
        return False
    normalized = collapsed.casefold()
    if normalized in _FULL_LINE_CHROME:
        return True
    if any(normalized.startswith(prefix) for prefix in _FULL_LINE_CHROME_PREFIXES):
        return True
    if _GET_THE_APP_LINE.match(normalized):
        return True
    if _GET_MORE_IN_INBOX_LINE.match(normalized):
        return True
    if _POWERED_BY_LINE.match(normalized):
        return True
    if _NYT_PHOTO_CREDIT.match(collapsed):
        return True
    if normalized in _BARE_SEPARATOR_CHARS:
        return True
    if _BARE_FOOTNOTE_NUMBER.fullmatch(collapsed):
        return True
    if _BARE_DURATION.fullmatch(collapsed):
        return True
    if _COPYRIGHT_LINE_START.match(collapsed):
        return True
    if _ADDRESS_LINE.fullmatch(collapsed):
        return True
    if _FOLLOW_ACROSS_LINE.match(normalized):
        return True
    return _is_delimited_chrome_row(collapsed)


def content_ratio(text: str) -> float:
    """Fraction of non-blank characters that look like real content."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    total = sum(len(line) for line in lines)
    content = sum(len(line) for line in lines if not is_boilerplate_line(line))
    return content / total
