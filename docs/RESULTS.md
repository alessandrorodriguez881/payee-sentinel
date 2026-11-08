# Results

Two rounds, each run once on 20 synthetic six-week periods with the offline simulator of Meko
(`python -m payee_sentinel.evaluate --seeds <seeds> --out runs/<file>.json`; `runs/` is not committed). Each period
has 60 vendors, 8 planted attacks, 20 legitimate look-alike cases and about 155 payments. The hypotheses were written
before each run (`docs/HYPOTHESES.md`). Two scoring defects were found afterwards and fixed; the tables below use the
corrected scoring, and the old numbers are listed under [Corrections](#corrections).

## Round 1: seeds 101-120 (run on 2026-11-08, ~18:47 CET)

| | siloed | shared | learning (v1) |
|---|---|---|---|
| Attacks stopped (of 160) | 81 | **156** | 156 |
| ...stopped before a bank change was applied | 81 | 142 | 142 |
| Money sent to attackers | €2,265,294 | **€129,298** | €129,298 |
| False-positive holds per period | 0 | 1.30 | 1.35 |
| Step-up calls to real vendors per period | 0 | 4.50 | 4.40 |
| Legitimate bank changes blocked until a call, per period | 0 | 4.45 | 2.35 |
| Vendors frozen by a wrong pattern match, per period | 0 | 0 | 3.70 |
| Attacker invoices held (true positives, all periods) | 0 | 0 | 4 |
| Holds on a vendor whose attack was already caught, per period | 0 | 0.15 | 0.90 |
| Hours before the target payment run (stopped attacks, mean) | 62.2 | 86.3 | 95.3 |
| Context tokens per payment decision from retrieval | 1,350 | 1,369 | 1,721 |
| Raw 30-day vendor history, tokens per payment decision | 771 | 771 | 771 |

- **H1 holds.** Shared memory stops 156 attacks against 81 in silos, and fewer in no seed (per seed: shared 7-8 of 8,
  siloed 2-7 of 8).
- **H2 holds.** 1.30 false-positive holds per period (the bar was 2). The hypothesis text said "about 250 payments";
  the worlds have about 155.
- **H3 fails.** Learning v1 stops the same 156 attacks, and the same share early (142 of 156). It acts earlier
  (E2: 95 hours before the target run against 86), but that measure was added after the development seeds.
- **H4 fails.** Learning v1 costs 7.40 frictions per period (holds + blocked changes + wrong freezes) against 5.75 with
  shared memory (the bar was +1). Its patterns are sets of signal types, and legitimate vendors (a new clerk, an office
  move) produce the same sets.
- **E1 (exploratory).** Retrieval sends more tokens than the raw history of one vendor (1,369 against 771), because a
  search returns up to 30 memories of several vendors that the agent then filters. A token saving is not shown here.

**The four misses** are all `careful_mailbox`: the real mailbox, no urgency, an account in the vendor's own country,
the right holder name. Two paid the attacker (€14,843 and €114,455). In the other two the change went through, but no
payment fell due before the six weeks ended: they count as missed.

## Round 2: learning v2, seeds 201-220 (run on 2026-11-08, ~23:31 CET)

Hypotheses committed before v2 existed (`7e73f90`), v2 built in `6a698f3`. Same world generator and weights; four modes.
Learning v2 also remembers the legitimate look-alikes the analyst confirms, and can only add a freeze.

| | siloed | shared | learning (v1) | learning2 (v2) |
|---|---|---|---|---|
| Attacks stopped (of 160) | 82 | **158** | 158 | 158 |
| Contained (caught after a first payment) | 0 | 1 | 1 | 1 |
| Money sent to attackers | €3,172,734 | **€7,274** | €7,274 | €7,274 |
| False-positive holds per period | 0 | 1.00 | 1.35 | 1.10 |
| Legitimate bank changes blocked until a call | 0 | 4.60 | 2.40 | 3.45 |
| Vendors frozen by a wrong pattern match | 0 | 0 | 3.55 | **1.60** |
| Frictions per period (the three rows above) | 0 | 5.60 | 7.30 | 6.15 |
| Step-up calls to real vendors per period | 0 | 4.15 | 4.10 | 4.00 |
| Attacker invoices held (true positives, all periods) | 0 | 0 | 4 | 4 |
| Holds on a vendor whose attack was already caught, per period | 0 | 0.15 | 0.95 | 0.95 |
| Hours before the target payment run (stopped attacks) | 78.4 | 100.3 | 106.4 | 106.4 |
| Context tokens per payment decision from retrieval | 1,337 | 1,354 | 1,708 | 1,708 |

- **H5 holds.** `learning2` stops as many attacks as `shared` in every seed (it can only add a freeze).
- **H6 fails, narrowly.** Its frictions are 6.15 per period against 5.60 for shared memory; the limit was 6.10. The first
  legitimate case of each kind still gets frozen before the analyst's verdict can teach the system.
- **H7 holds.** Wrong freezes fall from 3.55 (v1) to 1.60 per period, under half.
- **H8 holds.** v2 acts 6.1 hours earlier than shared memory on average (106.4 against 100.3; the bar was 5).
- **Round 1 replicated on new seeds:** shared memory stops 158 attacks against 82 in silos (fewer in no seed), money
  lost falls from €3.17M to €7,274, and false-positive holds are 1.00 per period.

## What this means for the product
- **Shared memory is the result to show.** In two independent sets of 20 periods it stops about twice as many attacks
  as siloed agents (156-158 against 81-82) and cuts the money lost from €2.27M to €0.13M (round 1) and from €3.17M to
  €7,274 (round 2), at a cost of about one false hold, four step-up calls and four or five bank-change calls per six
  weeks with 60 vendors.
- **Learning from people works where it was aimed** (wrong freezes cut by more than half, about 6 hours earlier), and
  after the scoring fixes it nearly pays for itself (6.15 against 5.60 frictions), but not quite in six weeks. A longer
  horizon, where the look-alikes it learns come back, is the next thing to test — pre-registered, not assumed.
- The attacker that gets money out is the careful one in the vendor's real mailbox. No signal in this design catches it
  before the first payment; Verification of Payee at the bank, or reading the email's content more closely, would be
  the next things to try.

## Corrections
Every number above was recomputed after each fix by re-running the same seeds (the runs are deterministic). Weights,
thresholds and the world generator were not changed by any correction.

1. **2026-11-08, ~23:31: partial losses.** In seed 207 a `careful_mailbox` attack was paid once (€7,274) and caught at
   the second invoice of the same payment run. The code counted it as stopped; the pre-registered definition says an
   attack is stopped only if no payment reaches the attacker. It now counts as `contained`. Round 1 had no such case;
   in round 2 the memory modes went from 159 to 158 stopped.
2. **2026-11-09, ~00:30: found by an independent review of the code.**
   - *A loss discovered 10 days later did not restore the real account*, so later invoices kept going to the attacker.
     Siloed losses were overstated: round 1 €2,811,527 → €2,265,294, round 2 €4,304,980 → €3,172,734. The memory modes
     were not affected. This also lowers round 1's ratio of money lost from "about 22 times" to about 17 times.
   - *Holds on a vendor whose attack had already started were counted as false positives*, including holds on the
     attacker's own fake invoice, and learning v2 then wrote some of them up as "legitimate look-alikes". Now an
     attacker invoice held is a true positive, other holds on an already-caught vendor are counted apart, and the
     auditor only uses the signals of the current case. False-positive holds, old → new: round 1 shared 1.45 → 1.30,
     learning 2.40 → 1.35; round 2 shared 1.15 → 1.00, learning 2.40 → 1.35, learning2 2.15 → 1.10. H6 now fails by
     0.05 (6.15 against 6.10) instead of 0.9 (7.15 against 6.25); H2, H4, H7 and H8 keep their verdicts.
3. **2026-11-08, evening: simulator aligned with real Meko** (promoted memories leave `memory_search` and are read with
   `memory_get_all(promoted=True)`, `docs/MEKO_SMOKE.md`). Same decisions; learning's retrieval tokens went from 1,705
   to about 1,720 because reading the whole collective memory costs more.
