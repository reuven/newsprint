# TODO

## Make the printing wait legible

The 2026-10-30 run printed correctly and looked broken. 105 sheet sides is
odd, so duplex runs 53 sheets = 106 impressions, the last one the blank back
of the final sheet. The printer reported 106, the display divided it by 105,
and the run ended on "Printed 106 sides." for a 105-side document.

Nothing was wrong with the print. Everything was wrong with the report:

- `Sent: 106/105 sides` reads as a fault.
- `Printed 106 sides.` is the printer's count, not the document's.
- `Sent:` runs ahead of paper, so "94/105" looked nearly done while the
  printer had six minutes left.
- At ~8s per side the line sat unchanged long enough to look frozen, and the
  job is absent from the print queue the whole time - CUPS marks it finished
  as soon as the printer's memory has it.

Completeness itself stays as it is: `impressions >= expected` is what makes
106 >= 105 pass, and it is correct.

- [ ] `progress.py`: pure formatting - sheets, elapsed, estimate, and the
      three lines. No clock inside, so the tests need no clock.
- [ ] Never show a fraction over 100%: clamp sides and sheets to the
      document's own totals.
- [ ] Two labeled stages: `Queueing`/`Queued` for what CUPS has sent,
      `Printing` for what the printer has put on paper.
- [ ] Elapsed time on the line so a 2-second poll visibly ticks even when
      the page count does not.
- [ ] Final line in the document's terms: `Printed 105 sides on 53 sheets.`
- [ ] Move `_sheets` out of cli.py into progress.py beside the rest of the
      duplex arithmetic.
- [ ] README: say that the print queue empties long before the paper stops.

## Not in this branch

- [ ] `--reprint-from <pdf> --pages N-M`.
- [ ] `--cov-fail-under=100` lives in the CI workflow rather than
      `pyproject.toml`, so a local `make test` does not enforce it.
- [ ] `origin/newsletter-pipeline` still exists; the local branch is gone.
