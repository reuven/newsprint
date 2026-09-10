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
  real inputs and showing no behavioural difference - do not assume it.
- String-literal mutations (`"PNG"` -> `"png"`, `"Content-Type"` ->
  `"CONTENT-TYPE"`) that survive are usually noise: the value is passed
  to a library that does not care.
