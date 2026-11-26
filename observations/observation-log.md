# Observation log

Newest first. Status: **open** (needs a test), **supported** (data agrees, more welcome), **confirmed**, **refuted**. Confidence is about this one servo.

---

## OBS-006 · 2026-11-26 · RoboNine legacy mode looks like the stock firmware (open)
* **Seen:** in legacy control, RoboNine v9 trials (25 Nov 16:03, 16:04; 26 Nov 11:32) all stopped at **+4 counts** and were flagged stalled against the 3-count tolerance. The stock firmware did the same in its first full run (+4, 89 of 97 stalled).
* **Evidence:** `data/T4.1a/…160318…`, `…160435…`, `…113245…` (RoboNine legacy); `…151700…` (Feetech).
* **Not shown:** only 3-cycle trials exist; no 100-cycle RL run.
* **Next:** 100-cycle T4.1a in RL, same target / limits / gauge zero.

## OBS-005 · 2026-11-26 · The 1.06 mm gauge offset on F is not backlash (supported)
* **Seen:** the Feetech runs show the gauge 1.06 mm (1.2°) from where it was zeroed, while the encoder says +3 counts (0.26°). The gauge zero is arbitrary and was re-set between days (2.53 mm on the RE day), so the number carries no physical meaning. Do not compare it with the 0.87° backlash of the article.
* **Evidence:** `03-results-feetech-factory.md`, `04-results-robonine-v9.md`.
* **Next:** backlash only from T3 (span between two loaded sides) or from a zero-independent difference such as T4.2b.

## OBS-004 · 2026-11-26 · The extension-mode outliers are real gauge readings, not obvious misreads (supported)
* **Seen:** the 21 RE readings beyond ±0.1 mm have plausible raw digits ("230", "298", "227", "26.7"); values repeat (2.26-2.27 seven times), which a random misread would not do. Encoder error on those cycles: 0 (±1, 2).
* **Evidence:** `data/T4.1a/2026-11-26_113458…/samples.csv`, frames `c0285`, `c0303`, `c0506`, `c0801`, `c0848`…
* **Checked by eye (1 of 21):** cycle 1104, read 2.24 mm: the gauge LCD in the saved frame shows **2.24 mm**, encoder error 0 counts, 40 °C. See `img/re-run-outlier-hover.png`.
* **Not shown:** the other 20 frames were not inspected yet (8↔0 confusion is the known vision error).
* **Next:** open the interactive report, hover each outlier and check the picture.

## OBS-003 · 2026-11-26 · RE output moves 0.2-0.45 mm while the encoder reads zero error (open, interesting)
* **Seen:** 1.6 % of RE cycles (21 of 1,299) put the gauge 0.2-0.45 mm (0.25-0.5°) away from the usual 2.53 mm, with encoder error 0. F had 0.2 % (3 of 1,406, all flagged stalled, +5 counts).
* **Reading:** consistent with gear play after the encoder (RoboNine's article says the encoder is before the gearing stage), taken up on one side or the other. Also possible: a loose jig part, thermal effects (38-40 °C), camera / gauge contact, or extension-mode behaviour at the end of a move. Most outliers come after cycle 1063 (after 15:56).
* **Evidence:** `04-results-robonine-v9.md`; run `…113458…`.
* **Next:** T3 push test for the real play; check whether RL / F2 show the same count of excursions in 1,300 cycles; look at the jig (tighten everything) before the next long run.

## OBS-002 · 2026-11-26 · Extension mode removes the steady +3-count offset (supported)
* **Seen:** F stops at **+3.00** counts on 1,403 of 1,406 cycles (0.26°). RE: **0.00 ± 0.08** counts over 1,299 cycles, 0 stalls.
* **Reading:** the offset equals the stock arrival behaviour (a dead zone of about 3 counts on the approach from above, much smaller than the 10 counts reported by RoboNine). RoboNine's controller has no such offset.
* **Evidence:** `03-…`, `04-…`.
* **Not shown:** whether the dead zone for reversing steps is 3, 10 or something else; that needs T3.1.
* **Next:** T3.1 in F, RL, RE.

## OBS-001 · 2026-11-26 · The typical spread is identical in F and RE (supported)
* **Seen:** robust SD (1.4826 × MAD) is 0.0148 mm in the F 1,406-cycle run and in the RE 1,299-cycle run; both are one or two 0.01 mm gauge steps. The difference between the runs is in the tails (OBS-003), not in the core.
* **Limit:** the 0.01 mm gauge step (0.011° at 50 mm) cannot show a smaller difference.
* **Evidence:** analysis of `samples.csv` of `…183449…` and `…113458…`.

---

## Earlier facts worth keeping (not numbered)

* 2026-11-25: the 100-cycle F run with wobble had no stalls; without wobble (first run) 92 % stalled → the wobble step and a recalibration fixed arrival (see 06-3).
* 2026-11-25: long F run, 3 stalls at cycles 729 / 1158 / 1333 (+5 counts, 0.82-0.87 mm) and a disturbance near cycles 940-1010 (1.00 mm for about 18 cycles).
* 2026-11-26: temperature spikes of 150 °C / 76 °C are sensor glitches (06-2).
