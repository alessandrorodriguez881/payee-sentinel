# Hypotheses, written before the first evaluation run

Written on 2026-11-08 at about 19:00 CET, before `evaluate.py` existed. The weights and thresholds in
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

## Known limits, stated now
- The same author wrote the world and the agents, so the agents may "play at home". To push against that, every
  attack kind has variants that remove signals (a VPN in the vendor's own country, no failed logins, the clerk
  calling the right number), and the legitimate cases are built to trigger the same signals.
- The large runs use the offline simulator of Meko (`context/local.py`), which ranks by shared words and exact
  entities. A smaller sample is repeated on real Meko and reported separately.
- Synthetic data only. The rates of attacks and look-alikes are chosen, not measured.
