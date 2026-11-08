# Payee Sentinel (working name)

Accounts-payable agents that share what they see through [Meko](https://mekodata.ai), so that a vendor-account
takeover is stopped before the money leaves. Built for ContextCon: Remember Everything (theme 03, "Remember the
Attacker").

> **Status, 8 Nov 2026: early work in progress.** The synthetic world, the agents, the evaluation and the Meko client
> exist; the client has not yet been run against the real Meko service, and there is no demo screen yet.

## The idea
A vendor takeover arrives in pieces: a password reset on the supplier portal from a new device, a changed contact
phone through the support line, an email asking to pay a new IBAN "before Friday". Each piece reaches a different
system and looks harmless on its own. Here every channel has an agent that writes what looks risky to shared memory
in Meko, and the agents that apply bank changes or release payments search that memory first.

## What is measured so far (synthetic data, offline Meko simulator)
20 six-week periods, 60 vendors each, 160 planted attacks and 400 legitimate look-alike cases
(`docs/HYPOTHESES.md` was written before the run; `docs/RESULTS.md` has everything, including what failed):

| | agents in silos | shared memory |
|---|---|---|
| Attacks stopped | 81 of 160 | 156 of 160 |
| Money sent to attackers | €2.81M | €0.13M |
| Cost per six weeks | none | 1.45 false holds, 4.5 step-up calls, 4.45 legitimate bank changes held for a call |

A second, independent set of 20 periods (seeds 201-220) repeats it: 158 of 160 stopped with shared memory against 82
in silos, and €7,274 lost against €4.30M.

The learning loop (approved attack patterns promoted to collective memory) did **not** improve on shared memory in
round 1, and it raised the analysts' workload. Version 2 also remembers the legitimate look-alikes the analyst
confirms: wrong freezes fall by more than half and it acts about 6 hours earlier, but it still costs more friction than
shared memory alone over six weeks. Both rounds, with their failed hypotheses, are in `docs/RESULTS.md`.

## Run it
```bash
pip install -r requirements.txt
python -m pytest -q
python -m payee_sentinel.evaluate --seeds 1-5
```
Without a `MEKO_API_KEY` everything runs on the offline simulator (`payee_sentinel/context/local.py`). With a key in
`.env` (see `.env.example`), `python scripts/meko_smoke.py` checks every Meko feature used here against the real service.

## Honest notes
- All data is invented. IBANs have valid check digits around random account numbers.
- The offline simulator ranks memories by shared words and exact entities; real Meko adds semantic search and
  extracts facts with an LLM. Results from each are reported separately.
- The Meko MCP client follows the reference client in
  [yugabyte/canned-agents](https://github.com/yugabyte/canned-agents) (Apache-2.0).
- Built with Claude Code, which wrote most of the code; the author directed the work and made the product decisions.
