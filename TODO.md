# TODO

## Count sides the printer printed, not sides CUPS sent

On 2026-10-02 job `Brother_MFC_L2700DW_series-8732` stopped short and
newsprint reported 66 of 116 sides. The printer's own record of the same
job (its job 10210) says 60: it ran out of memory and cancelled the job
(`job-canceled-at-device`, "Out of Memory" in its error history). CUPS
counts a side when the filter finishes *sending* it, so its
`job-impressions-completed` runs ahead of paper by however many pages the
printer had buffered - six here. Resuming from side 67 would have lost
61-66. The same lag means a job CUPS calls complete can still jam on its
last few sheets, after newsprint has retired the mail.

- [x] `cupsjob.py`: Get-Printer-Attributes for a queue's `device-uri`, and
      Get-Jobs against the printer itself, decoding one record per job.
- [x] `device.py`: turn a device URI into the printer's IPP URI - ipp/ipps
      as-is, dnssd:// resolved with `ippfind` (by uuid, else by name),
      anything else (usb, socket, lpd) has no printer-side record.
      `print.printer_uri` in the config overrides discovery.
- [x] `printer.await_completion()`: once CUPS stops, follow the printer's
      job (matched by job name, newest id) until it stops too, and judge
      the outcome on the printer's count. An IPP printer that cannot be
      reached or whose job cannot be found is a PrintError: mail kept.
      A non-IPP device falls back to the CUPS count, labeled as sent.
- [x] `cli.py`: progress and the short-print message use the printer's
      count, give sheets for duplex, name the printer's reason, and say
      which side to resume from. The run log keeps both counts.
- [ ] README and config.example.toml: `printer_uri`, and what the two
      counts mean.
- [ ] Release 0.20.0.

## Wait for the print job to finish before retiring mail

`spool()` returns when `lp` exits, which means CUPS accepted the job — not
that paper came out right. `config.py`'s `OutputConfig` docstring has said
so since the packets directory was added; on 2026-09-18 it cost a real run.
Job `Brother_MFC_L2700DW_series-8727` printed 67 of 107 sides and CUPS still
reported `job-state = completed`, so newsprint retired all 36 messages
eleven seconds after handoff, while the printer had seven minutes left.

`job-state` alone cannot catch this. `job-impressions-completed` can.

- [x] `cupsjob.py`: minimal IPP client — encode `Get-Job-Attributes`, decode
      `job-state`, `job-state-reasons`, `job-impressions-completed`,
      `job-media-sheets-completed`. No new dependency; `lpstat` cannot report
      impressions and `ipptool` is not installed by default on Linux.
- [x] Capture real IPP responses as byte fixtures, including the truncated
      job that exposed this.
- [x] `printer.await_completion()`: poll until terminal state, with injected
      `fetch` and `sleep` so tests stay instant, and a progress callback.
- [x] `cli.py`: retire only on a full print. On a short job, record
      `print-short`, keep every message starred, keep the PDF, exit non-zero.
- [x] `--no-wait` to restore fire-and-forget. The wait is otherwise unbounded
      by design; Ctrl-C is safe and leaves mail untouched.
- [x] README: document `--no-wait` and what a short print does.

## Done in this branch, beyond the list above

- [x] `mutmut` added to the dev group. `pyproject.toml` already referred to
      `mutants/` but the tool itself was never declared, so the audit
      CLAUDE.md asks for could not run. Scoped with `source_paths` to the two
      modules under audit; `also_copy` is required or the copied tree has no
      `__init__.py` and will not build.

## Not in this branch

- [ ] `--reprint-from <pdf> --pages N-M`, so a truncated run doesn't mean
      reprinting by hand from Preview.
- [ ] `--cov-fail-under=100` lives in the CI workflow rather than
      `pyproject.toml`, so a local `make test` does not enforce it. Moving it
      would make the gate the same in both places. Unrelated to this bug.
