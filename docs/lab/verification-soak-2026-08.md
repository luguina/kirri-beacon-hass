# Verification soak — window closed 2026-08-17 19:46 AEST

The re-soak [#14 called for](soak-2026-08.md#reading-the-re-soak), run to settle one
question: **does #35's retry actually rescue a missing echo on real hardware?**

72.0 hours exactly, no intervention inside the window. Every figure below comes from a report
bounded at **both** ends, so they are final and will not drift as the recorder ages rows out:

```
~/.venvs/ha/bin/python tools/soak/soak.py report --curlrc <path-to-curlrc> \
  --since 2026-08-14T09:46:41Z --until 2026-08-17T09:46:41Z
```

Cadence was kept at 2-hourly deliberately, matching #14. Raising it would only have doubled a
sample that was never going to be large enough; the counter is what answers the question, and
[#14's write-up](soak-2026-08.md) argued that case before this run started.

---

## What this run can and cannot be compared against

The proxy board was replaced on 2026-08-14 after its antenna path failed, so this soak ran on
**different hardware in the same position** as #14. That constrains the comparison hard, and
the constraint was set before the numbers arrived rather than after:

| Measure | Comparable to #14? |
|---|---|
| `echo_timeouts` and its attributes | ✅ **Yes** — a defect count, independent of link quality |
| Verified write rate | ⚠️ Loosely — same protocol path, different radio |
| Dropouts, latency, RSSI | ❌ **No** — different radio |

---

## Results

```
leg 1 (verified writes)  25/25 = 100.0%      (24/24 excluding the manual smoke test)
leg 2 (restore)          25 ok, 0 left inverted
skipped (unavailable)    0
round-trip latency       min 1.04s  median 1.65s  max 8.49s
missing echoes           11  (11 rescued by the retry, 0 still failed)
  on reads 10   on writes 1
failed polls 0 · unavailable periods 0 · proxy reboots 0 · 3 sub-5s API reconnects
WiFi RSSI  min −23  mean −19.2  max −16 dBm   (n=1730)
BLE  RSSI  min −81  mean −67.6  max −58 dBm   (n=193446)
```

Side by side with #14, with the comparability caveat above applied:

| | #14 (2026-08-09→12) | Verification (2026-08-14→17) |
|---|---|---|
| Duration | 72.0 h | 72.0 h |
| Verified writes | 19/22 = **86.4%** | 25/25 = **100.0%** |
| Missing echoes | not instrumented | **11** — 11 rescued, 0 failed |
| `unavailable` periods | 13 (longest 903 s) | **0** |
| Failed polls | not instrumented | **0** |
| Proxy reboots | 0 | 0 |
| Proxy API drops | 1 (sub-5 s) | 3 (all sub-5 s) |
| Latency median | 1.65 s | 1.65 s |
| WiFi RSSI mean | −26.8 dBm | −19.2 dBm |
| BLE RSSI mean | −53.4 dBm | −67.6 dBm |

---

## The verdict on #35: proven

**11 faults, 11 rescued, 0 failures.** With zero failures in 11 trials the 95% lower bound on
the rescue rate is **76%** (Clopper–Pearson, one-sided); folding in the 2/2 from the 60-cycle
probe on 2026-08-13 gives 13/13 and **79%**.

Before this window the retry had never executed against hardware once — every counter read 0,
honestly, because the diffuser had been off air. Eleven executions, eleven rescues.

**The write rate is not what proves it, and claiming otherwise would be wrong.** 86.4% → 100%
is Fisher's exact one-tailed p ≈ 0.095 on 3/22 against 0/25 — not significant, on top of a
radio change that makes the comparison loose anyway. What proves it is the **mechanism**: one
of the eleven was a *write* echo timeout, and it was rescued. That is a causal account of why
the write rate moved, not a correlation between two windows.

This is precisely the outcome [#14](soak-2026-08.md) predicted when it argued the instrumentation was
worth more than the cadence: *"the retry counter is what actually answers the question."* The
end-to-end rate improved and could not carry the claim; the counter could, and did.

### The fault itself has not changed, as designed

The retry hides the defect, it does not repair it — so the fault rate should be unchanged, and
it is:

| | #14 | Verification |
|---|---|---|
| Read faults | 13 / ~288 polls = **4.5%** | 10 / ~288 polls = **3.5%** (95% CI ≈ 1.7–6.3%) |
| Write faults | 3 / 22 = **13.6%** | 1 / 25 = **4.0%** (95% CI ≈ 0.1–20.4%) |

Both sit inside #14's intervals. The ~288 denominator is **derived** (72 h ÷ 900 s), not
measured, and carries #14's caveat unchanged: `DataUpdateCoordinator` drifts by each refresh's
duration, so the true poll count is lower and the real read rate slightly higher.

### The shared-defect claim survives its first real test

`on_reads` / `on_writes` existed so a later window could **falsify** the one-defect claim rather
than only re-assert it. Reads 3.5% (CI 1.7–6.3%) against writes 4.0% (CI 0.1–20.4%) overlap
heavily. Not falsified. Still one defect spanning both paths.

These remain **counts, not rates** — the read denominator is still unmeasurable for the reason
above.

---

## Answering the four numbers #14 asked for

[*Reading the re-soak*](soak-2026-08.md#reading-the-re-soak) named exactly four things to read,
and warned that a re-soak reporting zero of everything is
more likely a wrong entity id than a perfect box.

| Number | Result | Reading |
|---|---|---|
| `echo_timeouts` state | **11** | The fault rate. Non-zero, which is also what proves the entity ids were right |
| `recovered` / `failures` | **11 / 0** | What #35 is judged on. It passes |
| `on_reads` / `on_writes` | **10 / 1** | Shared-defect claim not falsified |
| `failed_polls` state | **0** | Every read fault was caught upstream by the retry |

The `failed_polls: 0` is only believable *because* `echo_timeouts` is non-zero. Read alone it
would have been indistinguishable from the wrong-entity-id failure mode that section warned
about.

---

## What the results do NOT support

**Do not claim the 13 → 0 dropout improvement for #35.** It is triple-confounded: the board
swap moved the RF baseline, #38 landed, and #35 landed, all before this window opened.
Dropouts were ruled not-comparable across the board swap before any of these numbers existed,
and that ruling stands.

**Do not claim #38 was verified.** `failed_polls` stayed 0 for the entire window because #35
caught every read fault upstream of it, so the tolerance logic never fired. #38 remains
unit-tested only. It is the backstop, not the mechanism — a later window with a fault the retry
cannot rescue is what will exercise it.

**Do not read the 3 API drops as degradation.** All three were sub-5-second reconnects against
#14's 1, on a different board. None coincided with a poll (`failed_polls` is 0), and uptime ran
straight through all of them — one boot epoch, zero reboots. Different radio, small counts,
no observed cost.

**Do not read the BLE mean as a regression.** −67.6 dBm against #14's −53.4 dBm is the
replacement board, not a deteriorating link: it was −64 dBm at swap and moved 3.6 dB over three
days, with a mean that has been flat to within 0.2 dB across every intermediate reading.

---

## Incidental: the boot-timestamp drift reproduced

The same boot printed as **19:35:38** in the full window and **19:35:57** in a window starting
three minutes later — 19 s apart, after an earlier 44 s sighting during #14.

Filed as **#50**. The cause is `boot_epochs()` keeping the *first* per-sample estimate it sees
rather than the best one, so the printed value is whichever sample the window happened to start
on. The reboot **count** is unaffected — `BOOT_TOLERANCE_S` already absorbs the jitter — and
both discrepancies fall inside the uptime sensor's 60 s default reporting interval, which is
what confirms the mechanism rather than merely fitting it.

**Fixed 2026-08-17.** `boot_epochs()` now reports the *earliest* estimate in each cluster, the
staleness being one-sided. Both windows above re-run to the same **19:35:17**, and #14's window
to **23:18:01** — each earlier than every value previously printed for the same boot, which is
the direction the one-sided-bias model requires and would have falsified it had it gone the
other way. Reboot counts are unchanged (0 in both windows, 1 boot epoch each). The report now
prints how many estimates back each epoch, because the residual error is the smallest staleness
in the window and that is only small when the window is wide.

---

## Verdict

**#35 is fixed, measured, and shippable.** The integration held 100% of 25 verified writes and
zero unavailable periods across three unattended days, on infrastructure that logged no reboots
and no unrecovered faults.

What is left is honest to state: the retry is proven to ≥76% at 95%, not to certainty; #38 is
still unexercised on hardware; and the defect that makes the retry necessary is still there,
firing roughly four times a day and being caught every time. The counter stays in place because
that rate is the thing worth watching — a window where `recovered` stops tracking
`echo_timeouts` is the signal that something changed.

**#17 is unblocked.** It was gated on #35 being proven, on the grounds that fetching records
2–4 every poll quadruples exposure to exactly this defect. The defect is now measured and
rescued, so the gate is satisfied.
