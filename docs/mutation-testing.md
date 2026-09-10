# Mutation testing

100% statement and branch coverage says every line *ran*. It does not say
any test depended on what the line computed. Mutation testing closes that
gap: it edits the source in small ways and reports which edits the suite
fails to notice.

Every real defect this project has found in its own tests came from here,
never from coverage.

## Running it

`mutmut` is not a project dependency; run it with `uv run --with mutmut`.
It rewrites source into a `mutants/` directory, so run it in a throwaway
git worktree, never in the working tree:

```sh
git worktree add -b mutation-run /tmp/mut main
cd /tmp/mut
```

Add a `[tool.mutmut]` block to the worktree's `pyproject.toml`.
`source_paths` must name the **whole package** - mutmut copies only what
it is pointed at, and a partial copy fails to import - so restrict the
scope with `do_not_mutate` instead:

```toml
[tool.mutmut]
source_paths = ["src/newsprint"]
do_not_mutate = ["src/newsprint/cli.py", "..."]   # everything else
```

Then:

```sh
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib   # WeasyPrint
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES        # see below
export no_proxy='*' NO_PROXY='*'
uv run --with mutmut mutmut run
uv run --with mutmut mutmut results
uv run --with mutmut mutmut show <mutant-name>
```

Remove the worktree and delete its branch when done.

## OBJC_DISABLE_INITIALIZE_FORK_SAFETY is not optional on macOS

Without it, mutants are reported as `segfault` rather than killed or
survived, and a segfault tells you nothing about your tests.

mutmut runs each mutant in a forked child. On macOS, a CoreFoundation
call after `fork()` without `exec()` aborts the process - and this suite
makes them: proxy lookup (`SCDynamicStoreCopyProxiesWithOptions`) from
the image fetcher, plus keyring and font enumeration. The crash has
nothing to do with the mutation, so it hits pure-Python modules too.

Measured on extract/render/pipeline/impose/stamp: without the variable,
732 mutants gave 472 killed, 5 survived, 255 segfaults. With it, 728
mutants gave 573 killed and **155 survived** - 150 real survivors the
segfaults had been hiding. Any score taken without it is worthless.

## Reading the results

- **Verify each new test by hand before believing it.** Apply the
  mutation, watch the test fail, restore. Tests written to kill a mutant
  have passed under it here more than once.
- **Clear `__pycache__` when restoring.** Python invalidates bytecode on
  source mtime *and size*; `[:-1]` and `[:+1]` are the same length, so a
  restored file can keep serving the mutant's bytecode. That cost an hour
  chasing a production bug that did not exist.
- **A survivor can mean dead code, not a missing test.** Two have been
  removed rather than pinned: a `host.endswith("mcsv.net")` branch whose
  every real input the following line already caught, and see
  `stamp.byline`'s substring checks, which across 101 real
  publication/author pairs never once decided anything
  `_shares_most_words` had not already decided.
- **Equivalent mutants exist.** Prove it by running both branches over
  real inputs and showing no behavioral difference - do not assume it.
- String-literal mutations (`"PNG"` -> `"png"`, `"Content-Type"` ->
  `"CONTENT-TYPE"`) that survive are usually noise: the value is passed
  to a library that does not care.

## Equivalent mutants

Some survivors cannot be killed, because the edit does not change what the
code does. Chasing one wastes an afternoon, so they are written down here
as they are confirmed - each with the reason, so a later run can re-check
the reason rather than re-derive it.

- **`get_text(" ", strip=True)` where the caller normalizes anyway.**
  `strip=True`/`False`/`None` and the omitted keyword all agree wherever
  the result is passed to something that strips or rewrites it:
  `_is_short_date_line` calls `.strip()` itself, `_normalize_title_text`
  removes every non-word character, and `" ".join(text.split())` erases
  the difference outright. The **separator** is a different matter: drop
  it and text split across inline tags runs together ("Sep7", "2.Warsh"),
  which those same callers then fail to recognize. Separator mutants are
  real; strip mutants at those call sites are not.
- **`find_all(True)` vs `find_all(None)`** - bs4 treats both as "every
  tag".
- **`_sponsor_anchor`'s `"html"` stop** - `clean_document` parses with
  lxml, which synthesizes a `<body>` for every input including a bare
  fragment, so the climb meets `body` first every time.
- **`_strip_chrome_blocks`'s `block.find("img")`** - widening it to any
  descendant is invisible, because `_prune_invisible_elements` removes the
  same textless blocks at the end of the run and spares the same `<img>`.
- **`_strip_duplicate_title_block`'s `match_idx is None or end_idx is
  None`, and the initial value of `end_idx`** - the two indices are only
  ever assigned together, so `or` and `and` agree and the initial value is
  never the one that is read.
- **`cast(...)`** - `typing.cast` does nothing at runtime, so every
  mutation of its first argument survives by construction. All eight of
  `_rendered_lines`' survivors are this.
- **`_is_argument_figure`'s `before.rstrip()`** - `_nearest_rendered_text`
  strips what it returns.

- **`_is_protected_heading`'s `len(line) < SHORT_LINE`.** At 40
  characters or more, a line can only score as chrome by matching a
  phrase, and every phrase that makes a line boilerplate also makes it
  definite chrome - so the second half of that `and` is already False
  wherever the length test could have decided anything. Checked against
  the phrase list, not assumed.
- **`_normalize_title_text`'s `sub("")`.** Replacing punctuation with a
  marker instead of removing it preserves every prefix relation, because
  the pattern matches whole *runs* of punctuation: " - " and " " are one
  run each, so the two copies of a title stay aligned. Breaking it needs
  the two copies to differ in how many runs they contain, not in what the
  runs are - "What's" against "Whats" - which no real pair of a Subject
  and its rendered headline does.

- **`_figure_placeholder_text`'s `image.get("alt") or ""`.** Substituting
  any other filler for the empty string changes nothing: a filler is not a
  data term, so the alt fails the data-pattern gate a few lines later and
  the function returns None either way, exactly as the empty string does.
  The same goes for widening `rstrip(".")` to strip an X as well - no
  generic alt word ends in one.
- **`_is_argument_figure`'s `forward=False`** - the helper branches on the
  flag's truthiness, and None is as false as False.

### extract.py

- **Every header name's spelling.** `email.message.Message.get` matches
  case-insensitively, so "List-Id", "list-id" and "LIST-ID" are one
  header. Half of this module's survivors are that, in seven places.
- **Three belt-and-braces defaults that a second guard already covers.**
  `_decode`'s `or "utf-8"` is unobservable because the `except
  LookupError` below it decodes as UTF-8 anyway; `_subject`'s
  `default="(no subject)"` because the `or "(no subject)"` at the end of
  the same function catches the empty string it would otherwise return;
  and `_body_html`'s `multipart` guard because a container part's content
  type is neither text/html nor text/plain, so skipping it early and
  falling through to those tests reach the same place. All three are kept:
  each says what its line means, and none is load-bearing.
- **`partition("@")` against `rpartition("@")`** - they differ only on an
  address with two at-signs, which is not an address.
- **`strip('"')` widened to strip an X as well, and `rstrip(">")` the
  same** - no List-Id label or host ends in one.

### stamp.py

- **`fontname=FONT` anywhere.** `FONT` is `"helv"`, which is also
  PyMuPDF's default, so dropping the argument changes nothing. Six of
  this module's survivors are that one fact. (`fontsize` is a different
  matter - the default is 11pt against this module's 6pt, and dropping it
  moves every right-aligned segment.)
- **`_shares_most_words`'s `len(words_a) <= len(words_b)`.** The tie
  decides which set is called the shorter one, and on a tie it does not
  matter: the intersection is the same either way and so is the
  denominator. Checked over 20,000 random word-set pairs, not argued.
- **`_draw_footer`'s `max(0.0, ...)` floor on the left segment's width.**
  The floor exists to keep a negative width away from `_truncate`, and
  `_truncate` returns the empty string for any width that cannot fit the
  ellipsis - about 5pt at this font size. So every floor from 0 up to
  that width behaves identically, and a mutation of the constant only
  becomes visible above it.

### render.py

- **The `{"Content-Type": "image/png"}` header on a fetched image.**
  WeasyPrint identifies an image by its bytes, not by what the fetcher
  says it is, so every mutation of that dict survives - including
  replacing the whole thing with None. It is kept because the bytes handed
  back really are PNG (`_grayscale_and_cap` re-encodes every one), and
  saying so costs nothing.
- **`_grayscale_and_cap`'s `grayscale.width > max_width_px`.** Resizing an
  image to the width it already has is byte-identical to not resizing it -
  checked, not assumed - so the boundary is unobservable. (Dropping the
  Lanczos filter is a different matter and is tested: Pillow's default for
  a downscale is bicubic, which softens the hairline gridlines a chart is
  made of.)
- **The message on the cached-failure `ValueError`.** The exception exists
  to make WeasyPrint treat the image as missing; only its being raised is
  load-bearing, and that is tested. The text reaches WeasyPrint's log and
  nothing else.

- **`_cap_width_px`'s `max(1, ...)` on the pixel count.** The millimetre
  floor above it already guarantees at least 1mm of column, and 1mm at
  200dpi is eight pixels - so the pixel floor can never be the one that
  decides. (The millimetre floor itself is real, and tested against a
  config whose margin is wider than its cell.)
- **`format="PNG"` against `format="png"`** - Pillow's format lookup is
  case-insensitive, and the bytes come out identical.

### mail.py

- **`connection.select(folder, readonly=False)`** - False is imaplib's own
  default, so passing it is documentation rather than behavior. Kept: this
  is the code that opens a folder writable to move mail out of it, and the
  argument says so at the call site.
- **`uid("SEARCH", None, criteria)`** - imaplib drops a None argument
  before sending, which is how the optional charset is omitted; removing
  it sends the identical command.
- **`_move_message`'s `return str(status)`** - both callers compare the
  result against "OK" and nothing prints it, so the failing status's own
  text never reaches anyone.
- **`self._imap = None` inside `_reconnect`** - the next line reassigns
  it, so the intermediate value is never read. (The same assignment in
  `__exit__` is real, and tested: it is what makes a mailbox used after
  its block say so plainly instead of failing deeper.)

### contents.py

- **The case of every HTML tag and CSS property.** `<tr>` against `<TR>`,
  `text-align` against `TEXT-ALIGN` - both are case-insensitive to the
  parser, and the rendered page is identical. Confirmed by rendering, not
  assumed.
- **`_truncate_to_width`'s `lo, hi = 0, len(text)`.** A `mid` of 0 makes
  the candidate a bare ellipsis, which the `best == _ELLIPSIS` check
  discards a few lines later - so starting the search at 1 only skips a
  candidate that could never be kept.
- **`_truncate_to_width`'s `best.removesuffix(_ELLIPSIS).rstrip()`.**
  Neither half does anything: the candidate was already rstripped before
  the ellipsis was appended, and the ellipsis always rides on the final
  token, which the word-boundary snap discards anyway. `fitted = best`
  behaves identically for every input.
- **`_row_text`'s `if subject_budget_pt > 0`.** The guard is an
  optimization, not a decision: any budget too small for the ellipsis -
  about 5pt - makes `_truncate_to_width` return the empty string, which
  the next line already treats as "no subject".
- **The number column's `fontname` and `fontsize`.** Both are real, and
  both move the subject budget by well under a point at any packet size a
  person would print - a two-digit cell number measured in Helvetica
  rather than Times differs by 0.4pt. Left uncovered rather than given a
  ten-digit fixture that no packet would ever produce.

### trim.py, picker.py, boilerplate.py

- **`classify`'s `page_count(pdf)`.** Passing None makes PyMuPDF open an
  empty document, so the count is 0 and `last` becomes -1 - which indexes
  the last page anyway. Negative indexing makes the mutation equivalent.
- **`classify`'s `max(0.0, ...)` floor on the used height.** A page with
  under a millimetre of text is a widow under any threshold a person would
  set, so raising the floor cannot change the verdict.
- **`_registrable`'s `len(labels) > 2`.** At exactly two labels the host
  *is* the compound suffix, and `labels[-3:]` and `labels[-2:]` are then
  the same two labels.
- **`_truncate_to_width`'s `if budget <= 0` (both picker.py and
  contents.py).** A budget of exactly zero is not room for a character, so
  falling through to the search finds nothing and returns the same
  ellipsis the guard would have. The guard saves the search, not the
  answer.
- **`source_label`'s `label.rpartition(".")[0]`.** Against
  `partition(".")` this differs only for a host under a compound suffix -
  "bbc.co.uk" gives a stem of "bbc.co" rather than "bbc" - which makes the
  redundancy check miss a publication called "The BBC". No host in the
  268-fixture corpus has a compound suffix, or even three labels, so the
  difference is unreachable for every newsletter the tool has seen.
- **`build_picklist`'s `total > limit`.** At exactly the limit the trim
  keeps everything either way; the sort it does first is undone by the
  per-publication sort below.
- **`_is_delimited_chrome_row`'s `len(segments) < 2 or ...`.** The left
  side is never true - the split only runs after a delimiter matched, so
  there are always at least two segments - and a blank segment is caught
  by the per-segment chrome test below regardless.

## Known survivors that are not equivalent

- **`_iter_text_elements`'s `node.get_text(strip=True)` in the leaf
  branch.** Under `strip=False` a whitespace-only element is yielded as a
  content leaf. That is a real difference - a blank leaf scores as
  content, so it can stop a directional walk where a real one would not -
  but every attempt at a fixture put the blank leaf somewhere a walk
  never reached, because in practice blank leaves do not sit at the
  boundary of a chrome run. Left open rather than covered by a fixture
  shaped only to reach it.
