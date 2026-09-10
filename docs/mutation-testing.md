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

## Known survivors that are not equivalent

- **`_iter_text_elements`'s `node.get_text(strip=True)` in the leaf
  branch.** Under `strip=False` a whitespace-only element is yielded as a
  content leaf. That is a real difference - a blank leaf scores as
  content, so it can stop a directional walk where a real one would not -
  but every attempt at a fixture put the blank leaf somewhere a walk
  never reached, because in practice blank leaves do not sit at the
  boundary of a chrome run. Left open rather than covered by a fixture
  shaped only to reach it.
