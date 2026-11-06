# TODO

## Finish a job that stopped partway

2026-11-05, job 8759: the tray ran dry at side 37 of 72 and the printer
discarded the job. The watchdog shipped in 0.22.0 now says so. What it
cannot do is finish the packet, and the recovery that followed was all by
hand: read the last cell number off the paper, work out which side that
was, work out that the resume point has to be odd, run `lp -P 37-72`, and
then unstar 25 messages one at a time in Thunderbird.

Three facts make that worth automating rather than documenting.

**CUPS re-imposes an extracted range from scratch.** Page 1 of whatever
range it is given becomes the front of a sheet, whatever it was in the
original. Resume on an even side and every back prints as a front: the
whole remainder comes out inside-out. The resume point must be the first
side of the first sheet that is not already whole - `2S + 1` - which at
worst reprints one sheet instead of ruining 36.

**The paper is read in cells, not sides.** "The last cell printed was
144" is what a person can see. Sides, sheets and page ranges are all
derived from it, and deriving them by hand at the end of a failed run is
exactly when it will go wrong.

**A uid means nothing without its folder.** The run log records `uids` as
one flat list, so nothing can safely retire from it: uid 4 in two folders
is two different messages. That is a gap in the record, not in the
retirement code, and it has to be closed first.

- [ ] `reprint.py`: `Pages`, `parse_pages`, `resume_at`, and
      `resume_after_cell`. Pure arithmetic, no I/O - the sheet rule is
      the part that must never be wrong, so it gets known-answer tests.
- [ ] Reject an even first page on a duplex job, with a message that
      names the odd page to use instead. Silently printing 36 inside-out
      sheets is the one outcome worse than refusing.
- [ ] `page-ranges` through `build_command`/`spool`.
- [ ] Record `messages` (identifier, uid and folder together), `trash`
      and `packet` on every run that holds mail. The flat `uids` and
      `documents` stay beside them: every record already written has
      those and nothing else, including the ones in the author's own log.
- [ ] `runlog.pending_mail`: runs that queued a packet and never retired
      it, newest first. Walk the log in time order, adding on `retired`
      and removing on `unretired`, so a run restored by `--unretire` is
      pending again.
- [ ] `--reprint-from <pdf>`, with `--pages N-M` or `--after-cell N`.
      Waits on the printer exactly as a normal run does, watchdog and
      all, then offers to retire the mail that run recorded.
- [ ] Fix `_wait_for_paper`'s short-print advice: it currently says to
      print sides `count+1`-`expected`, which is even half the time and
      so advises an inside-out reprint. It should name the sheet
      boundary, and the `--reprint-from` command that does it.
- [ ] Retire from a record written before `messages` existed, where the
      config names a single folder - tonight's own record is one of
      those, and 25 messages are still starred behind it.
- [ ] README: the reprint recipe, in place of the hand arithmetic.

## Not in this branch

- [ ] `--cov-fail-under=100` lives in the CI workflow rather than
      `pyproject.toml`, so a local `make test` does not enforce it.
- [ ] Versions 0.19.0 through 0.22.0 are tagged but were never published
      to PyPI, which stops at 0.18.0. Only the newest matters to anyone
      installing, so this is a decision to make rather than a gap to fill.
