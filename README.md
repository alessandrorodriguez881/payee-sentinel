# Payee Sentinel (working name)

Accounts-payable agents that share what they see through [Meko](https://mekodata.ai), so that a vendor-account
takeover is stopped before the money leaves. Built for ContextCon: Remember Everything (theme 03, "Remember the
Attacker").

> **Status, 9 Nov 2026: work in progress.** The synthetic world, the agents, the measurement (five rounds) and a first
> demo screen exist. The demo runs offline, as a replay of a run recorded on real Meko, or live on real Meko. On empty
> datapacks the real run reaches the same outcome as offline; once earlier runs fill the datapacks, their
> near-identical memories outrank a new run's and the Meko lane misses its own signals (`docs/MEKO_SMOKE.md`). Still
> to come: Claude reading the emails, the video and a public demo link.

## The idea
A vendor takeover arrives in pieces: a password reset on the supplier portal from a new device, a changed contact
phone through the support line, an email asking to pay a new IBAN "before Friday". Each piece reaches a different
system and looks harmless on its own. Here every channel has an agent that writes what looks risky to shared memory
in Meko, and the agents that apply bank changes or release payments search that memory first.

## Why it matters
Redirecting a supplier's payments is one of the costliest frauds companies face, and it is the case this project
models (a vendor's account taken over, then a request to pay a new IBAN):
- **$2.77 billion** lost to business email compromise in the United States in 2024, across 21,442 complaints, the
  second-costliest crime type the FBI recorded (FBI IC3, *2024 Internet Crime Report*, https://www.ic3.gov/AnnualReport/Reports/2024_IC3Report.pdf).
- **79 %** of 521 organisations surveyed had attempted or actual payments fraud in 2024; business email compromise
  was the No. 1 avenue (63 %), and **vendor-imposter fraud** was cited by 45 %, up 11 points (AFP, *2025 Payments
  Fraud and Control Survey*, press release of 15 April 2025, https://www.financialprofessionals.org/about/learn-more/press-releases/Details/survey-79-percent-of-organizations-were-victims-of-attempted-or-actual-payments-fraud-activity-in-2024).
- **£450.7 million** lost to authorised push payment fraud in the UK in 2024, £84.9 million of it by businesses and
  other non-personal accounts (UK Finance, *Annual Fraud Report 2025*, press release of 28 May 2025, https://www.ukfinance.org.uk/news-and-insight/press-release/fraud-report-2025-press-release).

These figures cover more than vendor takeovers; they show the scale of the problem, not the share this project would
prevent. The project's own numbers below come from synthetic data.

## What is measured so far (synthetic data, offline Meko simulator)
20 six-week periods, 60 vendors each, 160 planted attacks and 400 legitimate look-alike cases
(`docs/HYPOTHESES.md` was written before the run; `docs/RESULTS.md` has everything, including what failed):

| | agents in silos | shared memory |
|---|---|---|
| Attacks stopped | 81 of 160 | 156 of 160 |
| Money sent to attackers | €2.27M | €0.13M |
| Cost per six weeks | none | 1.30 false holds, 4.5 step-up calls, 4.45 legitimate bank changes held for a call |

A second, independent set of 20 periods (seeds 201-220) repeats it: 158 of 160 stopped with shared memory against 82
in silos, and €7,274 lost against €3.17M.

The learning loop (approved attack patterns promoted to collective memory) did **not** improve on shared memory in
round 1, and it raised the analysts' workload. Version 2 also remembers the legitimate look-alikes the analyst
confirms: wrong freezes fall by more than half and it acts about 6 hours earlier, and it nearly pays for itself, but not
quite over six weeks (6.15 frictions per period against 5.60). Round 3 carries the collective memory over four
consecutive periods: by the fourth, v2's wrong freezes are down from 3.1 to 0.6 per period, it costs 6.0 frictions
against 5.8 for shared memory alone, and it acts 41-51 hours earlier. v1 acts as early but never stops freezing
legitimate vendors by mistake.

Round 4 makes the comparison fair, after an adversarial review called it a straw man: every lane gets Verification
of Payee and a callback that follows the payment policy, and a plain shared table runs as a lane of its own. Best
practices alone take silos from 163 to 305 of 320 attacks stopped. A shared table stops 319 and cuts the money lost
from €710,177 to €12,535 over 40 six-week periods, at about 4 frictions per period (silos with best practices have
none). Meko stops the same 319 as the table and acts about 56 hours earlier.

Round 5 joins companies: when one company confirms an attack, the accounts, phones and devices it used and the
patterns people approved go to a shared consortium. In the next companies, 41 of 160 attacks are stopped before the
attacker even asks for a bank change when the crew changes all its infrastructure, and 61 when it reuses it all,
against 27 for companies alone, with no more friction (89 and 122 against 36 when the stops at the attacker's own
email, two minutes before the change is entered, also count). All five rounds, with their failed
hypotheses and the corrections made after independent reviews, are in `docs/RESULTS.md`.

## Run it
```bash
pip install -r requirements.txt
python -m pytest -q
streamlit run app.py                                  # the demo: siloed agents vs Meko, then a consortium
python -m payee_sentinel.evaluate --seeds 1-5         # the measurement
```
The demo replays the same two weeks for two teams of agents with the same rules: week 1, a takeover of Nordwind
Logistik's supplier-portal account; week 2, the same crew on Bergmann Verpackung. After week 1 a person can promote
the confirmed attack pattern to Meko's collective memory, and week 2 shows what changes. Week 3 moves to Weller
Maschinenbau, another company of the same consortium: the crew comes back with a new device, address and phone but the
mule account of the Nordwind case. Weller runs the same agents twice, on its own and with what Hallberg's analyst
confirmed and a person approved (round 5's rule, the same weights). On its own it stops the attack at Tuesday's callback,
after the request; in the consortium, Hallberg's approved pattern freezes the vendor on Monday afternoon, 21 hours
before the attacker asks for new bank details, and on Tuesday the mule account comes up with the trace of Hallberg's
decision. Week 3 always runs on the offline simulator, so the recorded run of weeks 1-2 stays valid.
Without a `MEKO_API_KEY` everything runs on the offline simulator (`payee_sentinel/context/local.py`). With a key in
`.env` (see `.env.example`), `python scripts/meko_smoke.py` checks every Meko feature used here against the real service.
`python scripts/record_run.py --real` records one run on real Meko, call by call, into `recordings/` (do it once, on
empty datapacks); the demo then offers it as a replay, so a visitor sees what real Meko answered without a key and
without spending the account's quota (`payee_sentinel/context/replay.py`). The repo holds one, recorded on 9 Nov 2026
on emptied datapacks (`recordings/`): the same outcome as offline, and Meko's self-learning loop took the agents'
weak question from low to mid. `docs/TRACES.md` is generated from that recording.

## How Meko is used
Datapacks, per-agent conversations, decision traces for every decision (allow included, each with the query and the
memories behind it), memory with metadata, hybrid search, promotion by a person, collective memory, the knowledge base
(the company's payment policy, quoted by the auditor, and confirmed case files), artifacts (one report per case),
`context_search`, and Meko's self-learning loop: Meko grades each open question an agent asks it, the auditor adds
the missing context after a confirmed case, and Meko replays the weak question to check the fix. Postgres inside the
context: the ledger (vendor master with its history, bank-change requests, payments) is a Postgres database in a Meko
datapack, every record linked to the decision trace behind it, with a SQLite copy where that database cannot be
reached (`payee_sentinel/ledger.py`). Sharing between companies (week 3, simulated in one place): a `consortium`
datapack with the accounts, phones and devices each member's analyst confirmed, every entry with the decision trace
behind it, while approved patterns and case files go into each member's collective memory and knowledge base. Token
tracking works against the live service but is not used by the agents yet. Feature by feature, with the code that uses each:
`docs/MEKO_USAGE.md`. Five decisions explained from their traces: `docs/TRACES.md`. What the live service actually
returned: `docs/MEKO_SMOKE.md`.

## Honest notes
- All data is invented. IBANs have valid check digits around random account numbers.
- One assumption shapes the siloed results: the vendor master keeps only the *current* contact values. Who changed a
  phone number, and from where, is known only to the portal and the support line. That is why a vendor-master agent on
  its own trusts a callback to a number the attacker put on file the day before.
- The offline simulator ranks memories by shared words and exact entities; real Meko adds semantic search and
  extracts facts with an LLM. Results from each are reported separately.
- The Meko MCP client follows the reference client in
  [yugabyte/canned-agents](https://github.com/yugabyte/canned-agents) (Apache-2.0).
- Week 3 is one illustrative case, not the measurement. Its crew also asks for the open invoices on Monday, and that
  email completes the match with Hallberg's approved pattern (6 of its 10 signal types); without it, the consortium
  would freeze the vendor on Tuesday, at the attacker's own email. Over 160 attacks, round 5 measured 41-61 stopped
  before the attacker asks in the consortium, against 27 for a company on its own.
- Built with Claude Code, which wrote most of the code; the author directed the work and made the product decisions.
