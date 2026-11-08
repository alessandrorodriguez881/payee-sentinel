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
- The **learning loop as built today does not pay for itself**. Next version (to be pre-registered before running new
  seeds 201-220): patterns that remember the order and timing of signals, and resolutions that teach the legitimate
  look-alikes (a new clerk) as well as the attacks.
- Token savings must be measured on real Meko and with a smaller retrieval window before anything is claimed.
