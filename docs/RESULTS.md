# Results

Five rounds, each run once with the offline simulator of Meko (round 4 adds best practices to every lane; round 5 joins companies): rounds 1 and 2 on 20 synthetic six-week periods
(`python -m payee_sentinel.evaluate --seeds <seeds> --out runs/<file>.json`; `runs/` is not committed), round 3 on ten
chains of four periods that carry their collective memory over (`--chains 301-304,311-314,...`). Each period
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

## Round 3: learning carried over four periods, chains on seeds 301-394 (run on 2026-11-09, 05:00-05:02 CET)

Hypotheses committed before the chained runs existed (`85521a4`, 04:58 CET; times corrected from an estimate in
`c7c6657`), engine in `f32e8bf` (05:00), checked only on the development chain 1-4. Ten chains of four six-week
periods; each period is a new world, and only the promoted collective memory carries over. Means over the ten chains
(80 attacks per period across the chains):

| Period | 1 | 2 | 3 | 4 |
|---|---|---|---|---|
| **shared**: frictions per period | 5.70 | 5.90 | 5.60 | 5.80 |
| **learning2**: frictions per period | 6.60 | 6.10 | 5.70 | **6.00** |
| learning2: wrong freezes | **3.10** | 0.80 | 0.40 | **0.60** |
| learning (v1): wrong freezes | 5.40 | 6.90 | 6.80 | 6.50 |
| Attacks stopped, shared / learning2 (of 80) | 80 / 80 | 79 / 79 | 79 / 79 | 80 / 80 |
| Stopped before a bank change, shared / learning2 | 71 / 71 | 73 / 73 | 71 / 72 | 72 / 72 |
| Hours before the target run, shared / learning2 | 88.6 / 93.3 | 88.7 / 140.1 | 77.1 / 120.8 | 87.6 / 129.0 |
| Hours before the target run, learning (v1) | 99.6 | 141.0 | 127.9 | 139.8 |
| Collective memory, learning2: attack patterns / look-alikes | 7.8 / 8.8 | 14.2 / 10.9 | 18.6 / 12.0 | 21.9 / 12.8 |
| Collective memory, learning (v1): attack patterns | 7.9 | 15.7 | 23.6 | 31.5 |
| learning (v1): frictions per period | 8.40 | 8.80 | 8.40 | 8.10 |
| Siloed agents: attacks stopped (of 80) | 39 | 43 | 39 | 40 |

- **H9 holds.** v2's wrong freezes fall from 3.10 in period 1 to 0.60 in period 4 (the bar was half, 1.55). In
  period 4, seven of the ten chains had none.
- **H10 holds.** In period 4 v2's frictions are 6.00 against 5.80 with shared memory (the bar was 6.30); eight chains
  had the same frictions as shared memory and two had one more.
- **H11 holds.** In no chain and no period did v2 stop fewer attacks than shared memory.
- **E3 (exploratory).**
  - From period 2 on, both learning modes act earlier than shared memory on the attacks they stop: v2 by 41-51
    hours, v1 by 50-52 hours. Both carry the approved attack patterns, which is where the earlier action comes from.
  - v1, which learns only from attacks, never stops freezing legitimate vendors by mistake: 5.4-6.9 wrong freezes per
    period, and 2.3-2.9 more frictions than shared memory in every period.
  - The 20-memory window that `memory_get_all` returns: v1's attack patterns pass 20 from period 3 (23.6, then 31.5),
    so its oldest patterns fall out of view. Re-run after the review without the cap, v1's wrong freezes in periods 3
    and 4 would be 7.3 and 7.5 (reported 6.8 and 6.5) and its frictions 8.8 and 8.8; the window flatters v1. v2's
    patterns reach 21.9 in period 4, and without the cap its numbers are identical.
  - Money lost with shared memory, v1 or v2: €10,167 in period 2 (one attack, in all three), nothing in the others;
    in silos €1.2-1.9M per period.

## Round 4: a fair comparison, best practices in every lane (run on 2026-11-10, 20:51-20:56 CET)

Pre-registered at 15:34 (`d8010df`) after an adversarial review called the comparison a straw man; engine in `72c75bb`
(20:51), checked only on the development chain 1-4, where rounds 2 and 3 also reproduced exactly. Ten chains of four
six-week periods (seeds 501-594, 320 planted attacks). Every lane gets Verification of Payee on bank-change requests
and a callback that follows the payment policy (the number on file 60 days earlier) 70 % of the time.

| Lane | Attacks stopped (of 320) | Money sent to attackers (40 periods) | Frictions per period, periods 2-4 | Hours before the target run, periods 2-4 |
|---|---|---|---|---|
| Silos, no best practices (reference) | 163 | €7,490,207 | 0 | — |
| **Silos with best practices** | 305 | €710,177 | **0** | 61.7 |
| **Shared table** (one table every agent writes and reads; no learning) | **319** | €12,535 | 3.83 | 80.8 |
| **Meko** (shared memory plus the collective memory a person approves) | **319** | €12,535 | 4.20 | **136.7** |

- **H12 holds.** Best practices alone take silos from 163 to 305 attacks stopped: most of the old gap was the missing
  controls, as the review said.
- **H13 holds.** A shared table still stops 14 more (319 against 305), and fewer in none of the 40 chain-periods. Money
  lost falls from €710,177 to €12,535 (one careful attacker, in period 4). The price: about 3.8 frictions per period
  (legitimate changes held for a call, false holds), where silos with best practices have none.
- **H14 holds.** Meko stops the same attacks as the table, and on the 239 attacks both stop in periods 2-4 it acts
  55.9 hours earlier on average (the bar was 24); its frictions are 4.20 per period against 3.83 (the bar was 4.33).
  What Meko adds over a shared table is timing, from what people approved, not more attacks caught.
- **E4 (exploratory).** Silos with best practices miss 15 attacks: 9 by the careful attacker, whose own-name account
  passes VoP, and 6 others whose callback did not follow the policy; the shared lanes miss one careful attack. With
  callbacks following the policy half the time, silos stop 298 and the table 318; at 90 %, 314 and 319.

## Round 5: a consortium of companies against a mule network (run on 2026-11-10, 21:05-21:13 CET)

Pre-registered at 20:59 (`d90fc2b`), amended at 21:00 before any code so that every lane faces the same attackers
(`a8b2954`); engine in `bbc8000` (21:05), checked only on the development chain 1-3. Ten chains of three companies
(seeds 601-693, 240 attacks), all with round 4's best practices. With probability *r* an attack reuses the mule account,
phone and device of an earlier attack in the chain. Lanes: each company `alone` (Meko inside the company), a shared
`blacklist` of the accounts, phones and devices analysts confirmed, and a `consortium` (that list plus the attack
patterns and legitimate look-alikes people approved in any company).

Attacks stopped early in companies 2 and 3 (160 attacks). *Before the attacker asks* means before the attacker's
first message asking for new bank details (the email, or the change entered in the portal). *Before the change is
entered* is the pre-registered measure: it also counts the stops at that email, two minutes before the request reaches
the vendor master.

| Reuse *r* | Before the attacker asks: alone / blacklist / consortium | Before the change is entered (pre-registered): alone / blacklist / consortium | Frictions per period, alone / consortium |
|---|---|---|---|
| 0 | 27 / 27 / **41** | 36 / 36 / **89** | 4.17 / 4.00 |
| 0.25 | 27 / 30 / **45** | 36 / 39 / **92** | 4.17 / 4.00 |
| 0.5 | 27 / 31 / **48** | 36 / 44 / **105** | 4.10 / 3.97 |
| 0.75 | 27 / 36 / **54** | 36 / 49 / **110** | 4.13 / 3.97 |
| 1 | 27 / 38 / **61** | 36 / 54 / **122** | 4.17 / 3.97 |

*Correction, 2026-11-10 21:22 CET.* The first write-up said "before any bank-change request was even sent", and the
README "before the attacker even asks", for the pre-registered count. Checked in the run data: in the email attacks the
attacker's email comes two minutes before the request is entered, and about half of the consortium's early stops are
at that email (48 of 89 at *r* = 0, 61 of 122 at *r* = 1). The stricter count is now the first column. More than a day
before the attacker asks: alone 24, blacklist 24-34, consortium 31-51.

- **H15 holds under both readings.** At *r* of 0.5 and more, the blacklist and the consortium stop more attacks than
  companies alone before the change is entered (44-54 and 105-122 against 36), and also before the attacker asks
  (31-38 and 48-61 against 27).
- **H16 holds.** With no reuse at all, the consortium acts 23.5 hours earlier than the blacklist on the 158 attacks both
  stop in companies 2 and 3 (the bar was 12): what people approved in one company protects the next one even when the
  attackers change accounts, phones and devices.
- **H17 holds.** The consortium's frictions are 3.97-4.00 per period, slightly fewer than companies alone (4.10-4.17).
- **E5 (exploratory).** Attacks stopped in all: 238-240 of 240 in every lane, because best practices already stop
  nearly all of them; what the network changes is how early. Money lost: €62,840 in most cells (two attacks by the careful attacker,
  neither stopped: €7,179 and €55,661), €0 at *r* = 0.25, and at *r* = 1 €0 with the blacklist or the consortium against €55,661 alone.

## What this means for the product
- **Between companies, the network acts earlier** (round 5): with a consortium, 41-61 of 160 attacks in the next companies are stopped before the attacker even asks for a bank change, against 27 for companies alone (89-122 against 36 counting the stops at the attacker's own email), with no more friction.
- **Against best practices, the honest claim is narrower** (round 4): VoP and policy callbacks close most of the gap on their own; sharing still stops the rest (319 of 320 against 305) and cuts losses from €710,177 to €12,535 over 40 periods, at about 4 frictions per period; Meko matches a shared table on attacks and acts about 56 hours earlier.
- **Shared memory is the result to show.** In two independent sets of 20 periods it stops about twice as many attacks
  as siloed agents (156-158 against 81-82) and cuts the money lost from €2.27M to €0.13M (round 1) and from €3.17M to
  €7,274 (round 2), at a cost of about one false hold, four step-up calls and four or five bank-change calls per six
  weeks with 60 vendors.
- **Learning from people works where it was aimed** (wrong freezes cut by more than half, about 6 hours earlier), and
  after the scoring fixes it nearly pays for itself (6.15 against 5.60 frictions), but not quite in six weeks.
- **With experience it does** (round 3): after three periods its wrong freezes are down to 0.6, its cost is within 0.2
  frictions of shared memory alone, and it acts 41-51 hours earlier. Learning only from attacks (v1) acts as early but
  keeps freezing legitimate vendors, so it never gets there.
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
4. **2026-11-09, 05:26: round 3's write-up, after an independent review.** The review re-ran rounds 2 and 3 and found
   every number in the round 3 table correct, and H9-H11 evaluated as registered. Two claims were wrong and are
   corrected above:
   - "The 20-memory window did not show in the numbers yet" held for v2 only. It does change v1's wrong freezes in
     periods 3 and 4.
   - "v1 never improves" was too broad: v1 acts earlier too (by 50-52 hours, more than v2). What it never does is stop
     freezing legitimate vendors.
   - "About two days earlier" became "41-51 hours".
   - The pre-registration's seed formula ("chain *k* (1-10) uses seeds 3*k*1-3*k*4") does not give the seeds it lists.
     The listed seeds, 301-304 to 391-394, are the ones that ran; `docs/HYPOTHESES.md` says so in a note.
