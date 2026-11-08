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

## Known limits, stated now
- The same author wrote the world and the agents, so the agents may "play at home". To push against that, every
  attack kind has variants that remove signals (a VPN in the vendor's own country, no failed logins, the clerk
  calling the right number), and the legitimate cases are built to trigger the same signals.
- The large runs use the offline simulator of Meko (`context/local.py`), which ranks by shared words and exact
  entities. A smaller sample is repeated on real Meko and reported separately.
- Synthetic data only. The rates of attacks and look-alikes are chosen, not measured.
