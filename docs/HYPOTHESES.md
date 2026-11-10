# Hypotheses, written before the first evaluation run

Written on 2026-11-08 at about 18:40 CET (local commit `4832352`), before `evaluate.py` existed. The weights and thresholds in
`payee_sentinel/agents.py` were fixed at the same time, by judgement, not tuned on any run.

## What is compared
The same synthetic worlds (`scenario.py`: about 60 vendors, about 1,450 events over six weeks, 8 planted attacks of
five kinds, 14 legitimate look-alike cases) are run three times, with the same agents and the same rules:

| Mode | What each agent can read |
|---|---|
| `siloed` | only the signals it wrote itself (one tool per channel, as most companies run today) |
| `shared` | every agent's signals, through shared memory in Meko |
| `learning` | `shared`, plus attack patterns a person approved after earlier incidents, promoted to collective memory |

## Seeds
- Development seeds 1-5: may be looked at while building and debugging.
- Confirmation seeds 101-120: not run until the code and these hypotheses are frozen. They are run once, and the
  results are reported as they come out. If anything is changed after seeing them, both versions are reported.

## Definitions
- **Attack stopped**: no payment is released to the attacker's account. Stage: `early` if stopped before a bank
  change is applied (rejected, blocked or frozen), `at payment` if stopped by a hold or a step-up call.
- **False positive hold**: a hold on a payment that would have gone to the vendor's real account.
- **Friction**: a step-up call on a real vendor, or a legitimate bank change that is blocked or frozen.

## Hypotheses
- **H1, sharing stops more attacks.** Over seeds 101-120, `shared` stops more attacks in total than `siloed`, and
  fewer in no seed.
- **H2, sharing does not flood the analysts.** With `shared`, false-positive holds average 2 or fewer per world (six
  weeks, about 250 payments).
- **H3, learning stops attacks earlier.** `learning` stops at least as many attacks as `shared` in total, and a larger
  share of the stopped attacks are stopped `early`.
- **H4, learning has a bounded cost.** False-positive holds plus blocked or frozen legitimate bank changes with
  `learning` exceed those of `shared` by at most 1 per world on average.
- **E1, context size (exploratory, no threshold).** For every payment decision, compare the tokens the payment agent
  receives from Meko retrieval with the tokens of the vendor's raw 30-day history across all channels. Reported as
  measured.

## Amendment 1 (2026-11-08, ~18:45 CET, local commit `499e275`), after development seeds 1-5 and before any confirmation seed
Running seeds 1-5 showed problems in the engine and the world, not in the rules. Fixed before seeds 101-120 were run
(weights and thresholds unchanged):
1. **Bug: closed cases kept counting.** After an attack was confirmed, its signals kept holding every later payment to
   the vendor's *real* account for 30 days (about 18 "false" holds per world). Now the analyst's verdict is written to
   Meko as a resolution, and agents ignore signals up to it. A legitimate hold, step-up or blocked change is resolved
   the same way.
2. **Engine: a hold during an attack is a detection.** A hold on the real account while a takeover is being prepared
   was counted as a false positive; the analyst's call would uncover it, so it now counts as stopped `early`.
3. **Engine: a confirmed incident freezes the vendor's bank details**, as a company would.
4. **World too easy.** Shared memory stopped 40 of 40 attacks, and no legitimate case combined two signals, so the cost
   of sharing could not be measured. Added before confirmation: legitimate combinations (a new clerk followed by a real
   bank change, an office move with a new phone and then a bank change, a real new direct line given in the email, a
   skipped callback), callbacks the clerk skips (20 % of email and phone attacks), and a sixth attack kind,
   `careful_mailbox` (the real mailbox, no urgency, same-country account, right holder name). Legitimate cases per world:
   14 → 20.
5. The vendor master now treats a skipped callback as "no check" instead of "denied".

After these changes seeds 1-5 still stop 40 of 40 attacks with shared memory, now at a measured cost (holds, step-ups
and blocked legitimate bank changes). The world is not changed again before the confirmation run.

**E2 (added after seeing seeds 1-5, so exploratory): hours before the target payment run** at which each stopped
attack is first acted on. H3's "share stopped early" turned out to be saturated on seeds 1-5 (35 of 40 in both
`shared` and `learning`), so H3 is kept as written and E2 is reported next to it.

## Round 2: learning v2 (written 2026-11-08, ~23:25 CET, local commit `7e73f90`, before v2 existed)
Round 1 (seeds 101-120, `docs/RESULTS.md`) showed that learning v1 adds work without stopping more attacks. On
development seeds 1-5, its wrong pattern matches came from legitimate look-alikes: a new clerk (16 matches), an office
move (9), a real new direct line (3). v1 only learns from attacks, never from the analyst's "this was legitimate".

**Learning v2** (`learning2` mode): when the analyst confirms that a hold, a step-up, a blocked bank change or a freeze
was legitimate, the auditor writes that case's signal types as a *legitimate look-alike* in a fourth datapack
(`sentinel-legit`) and the analyst promotes it. An attack-pattern match then freezes the vendor only if the vendor
shows at least one signal type that no promoted legitimate look-alike contains. What v2 learns can only add a freeze;
it never removes a shared-memory check (bank-change blocks, holds and step-ups work as in `shared`).

Confirmation seeds 201-220 (never run before). Seeds 101-120 have been seen, so they do not count for round 2. All four
modes are run; weights and thresholds are unchanged.

- **H5, v2 never loses an attack that sharing stops.** `learning2` stops at least as many attacks as `shared` in every
  seed.
- **H6, v2 nearly pays for itself.** Frictions per period (false-positive holds + legitimate bank changes blocked +
  wrong freezes) with `learning2` are at most those of `shared` + 0.5.
- **H7, v2 cuts wrong freezes.** `learning2` has at most half the wrong freezes of `learning` (v1).
- **H8, v2 acts earlier.** Over stopped attacks, the mean hours before the target payment run are at least 5 hours more
  with `learning2` than with `shared`.

## Round 3: learning carried over four periods (written 2026-11-09, ~04:58 CET, local commit `85521a4`, before the chained runs existed)
Rounds 1 and 2 ran every six-week period with an empty memory. Learning v2 nearly paid for itself there (H6 failed by
0.05 frictions per period). A company keeps its collective memory from one period to the next, so round 3 measures
learning with experience.

**Design.** Ten chains of four consecutive six-week periods. Within a chain, each period is a new world (a new seed:
new vendors, events and cases) and only the collective memory is carried to the next period: the promoted attack
patterns and, in `learning2`, the promoted legitimate look-alikes. Signals, open cases and decisions stay behind.
Agents, rules, weights and thresholds are unchanged. All four modes are run (in `siloed` and `shared` nothing is
promoted, so their periods are independent).

**Seeds.** Chain *k* (1-10) uses seeds 3*k*1-3*k*4, that is 301-304, 311-314, ..., 391-394, never run before. One
development chain on seeds 1-4 (already seen) may be run to check the code, and nothing else before the confirmation
run. The confirmation chains are run once.

- **H9, v2's wrong freezes fall with experience.** Averaged over the ten chains, `learning2`'s wrong freezes in
  period 4 are at most half of those in period 1.
- **H10, with experience v2 pays for itself.** In period 4, `learning2`'s frictions (false-positive holds +
  legitimate bank changes blocked + wrong freezes) are at most `shared`'s + 0.5 (H6's bound), averaged over chains.
- **H11, never worse on attacks.** In every period of every chain, `learning2` stops at least as many attacks as
  `shared`.
- **E3 (exploratory, no threshold).** Per period and mode: attacks stopped early, mean hours before the target
  payment run, patterns and look-alikes in collective memory, and `learning` (v1)'s wrong freezes.

**Limit stated now.** The agents read collective memory with `memory_get_all(promoted=True)`, which Meko documents as
a recent window of about 20 memories; the simulator keeps the same cap. Over four periods older patterns can fall out
of view. That is part of the system as built, and it is measured as it is (E3 reports the counts).

*Note added on 2026-11-09 at 05:26, after the run, the text above left as registered:* the seed formula "chain *k*
(1-10) uses seeds 3*k*1-3*k*4" does not give the seeds it lists. The listed seeds, 301-304, 311-314, ..., 391-394
(chain *k* = 0-9 uses 300 + 10*k* + 1 to 300 + 10*k* + 4), are the ones that ran.

## Round 4: a fair comparison, with best practices in every lane (written 2026-11-10, 15:34 CET, local commit `d8010df`, before any code)
An adversarial review (10-Nov) called the comparison a straw man: the siloed agents have neither Verification of
Payee, mandatory for euro credit transfers in the EU since 9 October 2025, nor a callback that follows the payment
policy. It also asked what Meko adds over a plain shared SQL table. Round 4 gives every lane the same best practices and
adds that table as a lane of its own.

**Best practices, in all three lanes.**
- *Verification of Payee (VoP)* when a bank-change request arrives. The receiving bank compares the claimed holder with
  the account's real holder. The world gets a hidden outcome per request: an attacker's mule account gives `no_match`;
  the careful attacker (`careful_mailbox`, an account in the vendor's own name) gives `match`; a legitimate change gives
  `match`, or `close_match` in 10 % of cases (a trading name). Non-euro IBANs (PL) give no VoP result. New signal types,
  with weights fixed now by judgement: `bank.vop_no_match` +6 and `bank.vop_close_match` +2. Both are written by the
  vendor-master agent, so silos see them too.
- *Policy callback:* the clerk calls the number that was on file 60 days before the request, as the payment policy
  says, with probability 0.7 per callback (fixed now; 0.5 and 0.9 are reported as exploratory). When the clerk does, the
  real vendor answers: an attack is denied and a legitimate change is confirmed. When the clerk does not, the callback
  happens as in rounds 1-3.
- Both draws come from a random stream of their own (seeded by the world's seed), so the worlds of rounds 1-3, and
  their results, do not change.

**Lanes.** `siloed+bp` (each agent reads only its own signals); `shared+bp`, a shared table (every agent writes to one
table and reads a vendor's rows before deciding: exact lookup by vendor, no promotion, no learning, no recall of other
cases; the simulator's `shared` mode is exactly that); `learning2+bp` (Meko: shared memory plus the collective memory a
person approves). `siloed` without best practices is run as a reference.

**Design.** As round 3: ten chains of four six-week periods that carry only the promoted collective memory; seeds
501-504, 511-514, ..., 591-594 (chain *k* = 0-9 uses 500 + 10*k* + 1 to 500 + 10*k* + 4), never run before. Development
seeds 1-4 may be used to check the code. The confirmation chains are run once.

- **H12, best practices matter.** Over all 40 chain-periods, `siloed+bp` stops more attacks than `siloed`.
- **H13, sharing still adds to best practices.** `shared+bp` stops more attacks than `siloed+bp` in total, and fewer in
  no chain-period.
- **H14, what Meko adds over a shared table.** In periods 2-4, on attacks both lanes stop, `learning2+bp` acts at least
  24 hours earlier on average than `shared+bp`, and its frictions (false-positive holds + legitimate bank changes
  blocked + wrong freezes) are at most those of `shared+bp` + 0.5 per period.
- **E4 (exploratory).** What each best practice stops on its own; the careful attacker; frictions from VoP close
  matches; callback compliance of 0.5 and 0.9 for `siloed+bp` and `shared+bp`.

If H13 or H14 fails, the README and the video say so: the claim then becomes what the data supports.

## Round 5: a consortium of companies against a mule network (written 2026-11-10, 20:59 CET, before any code)
The adversarial review of 10-Nov suggested a final act: several companies share what one of them confirmed. Round 5
measures it, with best practices (round 4) in every lane.

**World.** Chains of three companies, each a six-week world of its own (seeds 601-603, 611-613, ..., 691-693: chain
*k* = 0-9 uses 600 + 10*k* + 1 to 600 + 10*k* + 3), run one after the other. The attackers reuse infrastructure: with
probability *r*, an attack in a later company reuses the mule account, the phone and the device of an attack confirmed
earlier in the chain (drawn from a random stream of its own). *r* is swept: 0, 0.25, 0.5, 0.75 and 1.

**Lanes,** all with best practices:
- `alone`: each company on its own, with Meko inside the company (learning v2), nothing shared between companies;
- `blacklist`: as `alone`, plus an exact list of confirmed accounts, phones and devices shared by all companies (a match
  is the signal `consortium.known_infrastructure`, weight 6, fixed now);
- `consortium`: as `blacklist`, plus the attack patterns and legitimate look-alikes people approved in any company,
  carried as collective memory (as round 3 carries it between periods).
Only infrastructure from cases an analyst confirmed enters the shared list, and only patterns a person approved.

- **H15, the network pays when crews reuse.** At *r* ≥ 0.5, `blacklist` and `consortium` stop more attacks before any
  bank-change request than `alone`, summed over companies 2 and 3.
- **H16, what Meko adds over a blacklist.** At *r* = 0, `consortium` acts at least 12 hours earlier on average than
  `blacklist` on attacks both stop in companies 2 and 3.
- **H17, the network does not burden legitimate vendors.** At every *r*, `consortium`'s frictions per period are at
  most `alone`'s + 0.5.
- **E5 (exploratory).** The whole sweep for every lane: attacks stopped, stopped before any request, hours before the
  target run, money lost and frictions.

## Known limits, stated now
- The same author wrote the world and the agents, so the agents may "play at home". To push against that, every
  attack kind has variants that remove signals (a VPN in the vendor's own country, no failed logins, the clerk
  calling the right number), and the legitimate cases are built to trigger the same signals.
- The large runs use the offline simulator of Meko (`context/local.py`), which ranks by shared words and exact
  entities. A smaller sample is repeated on real Meko and reported separately.
- Synthetic data only. The rates of attacks and look-alikes are chosen, not measured.
