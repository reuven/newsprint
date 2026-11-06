# TODO

## Say something when the printer stops answering

2026-11-05, job 8759. The tray ran dry at side 37 of 72. Paper went back
in, and nothing happened: newsprint sat on a frozen counter for **2 hours
and 9 minutes** without a word, and the only way to find out why was to go
digging at the socket:

```
tcp4  0  130160  192.168.68.107.52810 -> 192.168.68.114.631  ESTABLISHED  ipp:94074
```

A full 131,072-byte send buffer, frozen to the byte across minutes, while
the printer reported `printer-state = idle`, `printer-state-reasons = none`,
paper ready, toner at 80%, and no job of its own. The printer had discarded
the job on the paper-out and left the connection open; the `ipp` backend was
parked in a `write()` that was never going to return.

This refutes the reasoning currently written into `await_completion`:

> a printer that is switched off or out of paper will finish the job when
> someone attends to it

It does not. This one threw the job away and kept the socket. The unbounded
wait is still right - giving up early hands back exactly the uncertainty the
function exists to remove, and Ctrl-C is already safe - but silence is not.
A counter that has not moved in ten minutes is information.

- [ ] Count consecutive polls with no change in the impression count. No
      clock: at `interval` seconds a threshold of N polls is N*interval,
      and keeping time out of the loop is what made `progress.py` testable.
- [ ] `on_stall` callback, fired once when the threshold is crossed, then
      re-armed so a second stall after real progress is reported again.
- [ ] Both loops - tonight's stall was in the queueing stage, with CUPS
      still `processing`, but `_follow_on_printer` can stall the same way.
- [ ] The warning says what to check, because "is it printing or not?" was
      the actual question: paper, then whether the queue shows a frozen
      count, then that cancelling is the fix and Ctrl-C is safe.
- [ ] Once stalled, annotate the progress line itself so the condition
      stays on screen instead of scrolling away.
- [ ] Correct the `await_completion` docstring: record what a paper-out
      actually does on this printer.
- [ ] README: how to tell a wedged job from a slow one, and that a
      cancelled job can leave an orphaned backend holding the queue
      ("now printing <queue>-0"), cleared by restarting cupsd.

## Not in this branch

- [ ] `--reprint-from <pdf> --pages N-M`, and retire the mail from that
      run's recorded UIDs once it completes. Tonight that gap meant a
      manual `lp -P 37-72` and 25 messages to unstar by hand.
- [ ] `--cov-fail-under=100` lives in the CI workflow rather than
      `pyproject.toml`, so a local `make test` does not enforce it.
