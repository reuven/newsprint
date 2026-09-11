# Three features, plus packet retention

## A. A durable packet directory, swept on each run

Today a packet with no `--output` lands in a temp directory the OS
eventually deletes, so a print that comes out wrong leaves nothing to
reprint - and the mail is already retired by then, because `spool()`
returning success means CUPS accepted the job, not that paper came out
right.

- [ ] `[output] directory` — where packets go with no `--output`.
      Default `~/.local/state/newsprint/packets`.
- [ ] `[output] keep_days` — remove packets older than this on each run.
      Default 30. `0` keeps them forever.
- [ ] The sweep touches only `*.pdf` directly in that directory, and only
      when the run writes there. Never touches an explicit `--output`.
- [ ] Report what was swept, the way every other removal in this tool is
      reported.

## B. Two cells a side as well as four

`Paper.cell` is a property hardcoded to a quarter of the sheet, and
`impose.py` has `CELLS_PER_SIDE = 4` with a matching four-offset table.
Two a side doubles the type size for the same paper and the same fold,
which the `font_size_pt` knob cannot do - a bigger font in an A6 cell
just means more cells.

- [ ] `Paper` carries `cells_per_side`, so `paper.cell` stays a property
      and every caller (render, trim, stamp, contents) is unchanged.
- [ ] `[print] cells_per_side`, default 4; `--cells-per-side` overrides
      it for one run, the way `--paper` overrides `[print] paper`.
- [ ] `impose()` derives its offsets rather than holding a table.
- [ ] Only 2 and 4 are offered: 1 is "don't impose" and 8 is unreadable.

## C. Several folders in one account

`mail.folder` is one string. Someone whose filters put newsletters in
more than one place cannot say so.

- [ ] `[mail] folders` — a list. `folder` stays accepted as the
      one-folder spelling, since it is in every existing config.
- [ ] Each uid is tracked with the folder it came from, because retiring
      it means selecting that folder again.
- [ ] `--unretire` already records the folder per retirement; check it
      still holds when a run spans two.

Not doing: several *accounts*. It needs a connection each and routing
every message back to its own account on retire and unretire - the code
that moves real mail - for something the one user of this has said they
do not need. `[[mail.account]]`, each holding a `folders` list, would
make this work the inner half rather than a rewrite.
