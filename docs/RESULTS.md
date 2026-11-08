# Results: confirmation run, seeds 101-120

Run once on 2026-11-08 at about 18:47 CET, after amendment 1 (`docs/HYPOTHESES.md`), with the offline Meko simulator.
Raw output: `runs/confirmation_101-120.json` (not committed; regenerate with
`python -m payee_sentinel.evaluate --seeds 101-120 --out runs/confirmation_101-120.json`).

20 synthetic six-week periods, 60 vendors each: 160 planted attacks, 400 legitimate look-alike cases and about 158
payments per period.

| | siloed | shared | learning |
|---|---|---|---|
| Attacks stopped (of 160) | 81 | **156** | 156 |
| ...stopped before a bank change was applied | 81 | 142 | 142 |
| Money sent to attackers | €2,811,527 | **€129,298** | €129,298 |
| False-positive holds per period | 0 | 1.45 | 2.40 |
| Step-up calls to real vendors per period | 0 | 4.50 | 4.40 |
| Legitimate bank changes blocked until a call, per period | 0 | 4.45 | 2.35 |
| Vendors frozen by a wrong pattern match, per period | 0 | 0 | 3.70 |
| Hours before the target payment run (stopped attacks, mean) | 62.2 | 86.3 | 95.3 |
| Context tokens per payment decision from retrieval | 1,350 | 1,369 | 1,722* |
| Raw 30-day vendor history, tokens per payment decision | 771 | 771 | 771 |

\* 1,705 in the first run. On the same evening the simulator was aligned with what real Meko does (promoted memories
leave `memory_search` and are read with `memory_get_all(promoted=True)`, `docs/MEKO_SMOKE.md`). Re-running seeds
101-120 gave identical decisions and numbers, except this one: reading the whole collective memory costs 1,722 tokens.

## Hypotheses
- **H1 holds.** Shared memory stops 156 attacks against 81 in silos, and fewer in no seed (per seed: shared 7-8 of 8,
  siloed 2-7 of 8).
- **H2 holds.** 1.45 false-positive holds per period (the threshold was 2). The hypothesis text said "about 250
  payments"; the worlds have about 158.
- **H3 fails.** Learning stops the same 156 attacks, and the same share early (142 of 156). It does act earlier
  (E2: 95 hours before the target run against 86), but that measure was added after the development seeds.
- **H4 fails.** Learning costs 8.45 frictions per period (holds + blocked changes + wrong freezes) against 5.90 with
  shared memory. Its patterns are sets of signal types, and legitimate vendors (a new clerk, an office move) produce
  the same sets.
- **E1 (exploratory).** Retrieval sends more tokens than the raw history of one vendor (1,369 against 771), because a
  search returns up to 30 memories of several vendors that the agent then filters. A token saving is not shown here.

## What the four misses were
All four are `careful_mailbox`: the real mailbox, no urgency, an account in the vendor's own country, the right holder
name. Two paid the attacker (€14,843 and €114,455). In the other two the change went through, but no payment fell due
before the six weeks ended: they count as missed.

## What this means for the product
- The case for **shared memory** is supported in this world: about 22 times less money lost (€2.81M against €0.13M), for about 1.5 holds,
  4.5 step-up calls and 4.5 bank-change calls per six weeks with 60 vendors.
- The **learning loop as built in round 1 does not pay for itself**. Round 2 tried the second idea: resolutions that
  teach the legitimate look-alikes (a new clerk) as well as the attacks.
- Token savings must be measured on real Meko and with a smaller retrieval window before anything is claimed.

# Round 2: learning v2, seeds 201-220

Run once on 2026-11-08 at about 23:31 CET, after the round-2 hypotheses were committed (`7e73f90`) and v2 was built
(`6a698f3`). Same world generator, same weights; four modes. Raw output: `runs/round2_201-220.json`.

**Scoring fix found in this run.** In seed 207 a `careful_mailbox` attack was paid once (€7,274) and caught at the
second invoice of the same payment run. The code counted it as stopped; the pre-registered definition says an attack is
stopped only if *no* payment reaches the attacker. The code now follows the definition and calls such a case
`contained`. Re-scoring seeds 101-120 changes nothing there (no partial losses). The table uses the corrected scoring;
before the fix, the three memory modes showed 159 stopped and slightly lower hours.

| | siloed | shared | learning (v1) | learning2 (v2) |
|---|---|---|---|---|
| Attacks stopped (of 160) | 82 | **158** | 158 | 158 |
| Contained (caught after a first payment) | 0 | 1 | 1 | 1 |
| Money sent to attackers | €4,304,980 | **€7,274** | €7,274 | €7,274 |
| False-positive holds per period | 0 | 1.15 | 2.40 | 2.15 |
| Legitimate bank changes blocked until a call | 0 | 4.60 | 2.40 | 3.40 |
| Vendors frozen by a wrong pattern match | 0 | 0 | 3.55 | **1.60** |
| Frictions per period (the three rows above) | 0 | 5.75 | 8.35 | 7.15 |
| Step-up calls to real vendors per period | 0 | 4.15 | 4.10 | 4.00 |
| Hours before the target payment run (stopped attacks) | 78.4 | 100.3 | 106.4 | 106.2 |
| Context tokens per payment decision from retrieval | 1,337 | 1,354 | 1,708 | 1,710 |

## Round-2 hypotheses
- **H5 holds.** `learning2` stops as many attacks as `shared` in every seed (it can only add a freeze).
- **H6 fails.** Its frictions are 7.15 per period against 5.75 for shared memory (the limit was 6.25). The first
  legitimate case of each kind still gets frozen before the analyst's verdict can teach the system.
- **H7 holds.** Wrong freezes fall from 3.55 (v1) to 1.60 per period, under half.
- **H8 holds, narrowly.** v2 acts 5.9 hours earlier than shared memory on average (106.2 against 100.3; the bar was 5).

## Round 1, replicated on new seeds
The round-1 comparison repeats on seeds 201-220: shared memory stops 158 attacks against 82 in silos (fewer in no seed),
money lost falls from €4.30M to €7,274, and false-positive holds stay at 1.15 per period (H2's bar was 2).

## What this means for the product (after round 2)
- **Shared memory is the result to show**: in two independent sets of 20 periods it stops about twice as many attacks
  as siloed agents and cuts the money lost by more than 20 times, at a cost of a few calls a week.
- **Learning from people works where it was aimed** (wrong freezes cut by more than half, and earlier action), but the
  learning loop still costs more friction than it saves in six weeks. To claim more, it needs a longer horizon (the
  look-alikes it learns pay off on the second and third occurrence) — to be tested, not assumed.
- The one attack that gets money out is the careful attacker in the vendor's real mailbox. No signal in this design
  catches it before the first payment; Verification of Payee at the bank, or reading the email's content more closely,
  would be the next things to try.
