"""The demo story: two weeks of accounts payable at Hallberg Components, run twice side by side.

The same events reach two teams of agents with the same rules (`agents.py`). In one, each agent keeps its own memory
(`siloed`, as most companies run today); in the other they share it through Meko (`learning2`).

- Week 1: someone takes over Nordwind Logistik's supplier-portal account (Monday), asks for payments to go to a new
  IBAN (Tuesday), and the payment run is on Thursday.
- Week 2: the same crew tries Bergmann Verpackung.
- Week 3: the crew moves on to Weller Maschinenbau, another company in the same consortium, with a new device, address
  and phone but the same mule account. Weller runs the same agents twice: on its own, and with what Hallberg's analyst
  confirmed and a person approved (round 5's rule, the same weights). This act always runs on the offline simulator,
  so a recorded run of weeks 1-2 stays valid.

Each chapter is one step on screen. The analyst is simulated with what a real one could do: call the vendor on the
number that was on file 60 days earlier. Everything is invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import random
import re
import uuid

from .agents import (BLOCK_BANK_CHANGE_AT, HOLD_AT, STEP_UP_AT, Action, InboxAgent, Memory, PaymentAgent,
                     PortalAgent, Signal, SupportAgent, VendorMasterAgent, observable, risk_of)
from .context.base import now_iso
from .context.local import LocalMeko
from .context.replay import Replayer
from .ledger import Ledger, SqliteLedger
from .scenario import Event, Vendor, fictional_iban

LANES = {"siloed": "Agents in silos (today)", "learning2": "Agents sharing memory in Meko"}
KNOWLEDGE = Path(__file__).resolve().parent / "knowledge"
CASE_TITLE = "Confirmed fraud case:"
CASE_NAME = re.compile(re.escape(CASE_TITLE) + r"\s*(.+?) \((V\d{3})\)")      # vendor name and id of a case file
CASE_ACCOUNT = re.compile(r"Bank account in the request: ([A-Z]{2}\d{2}[A-Z0-9]+)")
COUNTRIES = {"DE": "Germany", "FR": "France", "IT": "Italy", "LT": "Lithuania", "NG": "Nigeria", "NL": "Netherlands",
             "RU": "Russia"}
BUYER = "Hallberg Components GmbH"
AGENT_NAMES = {"portal": "Portal agent", "inbox": "Inbox agent", "support": "Support agent",
               "vendor_master": "Vendor-master agent", "payments": "Payments agent", "analyst": "Analyst (a person)",
               "auditor": "Auditor agent"}


def _vendors() -> dict[str, Vendor]:
    rng = random.Random(2026)
    spec = [("V001", "Nordwind Logistik GmbH", "DE", "nordwind-logistik.example", "+49 40 66969120", 40000),
            ("V002", "Bergmann Verpackung GmbH", "DE", "bergmann-verpackung.example", "+49 69 90009840", 28000),
            ("V003", "Van Dijk Elektro B.V.", "NL", "vandijk-elektro.example", "+49 221 4710550", 11000),
            ("V004", "Bertoli Imballaggi S.r.l.", "IT", "bertoli-imballaggi.example", "+49 89 99998300", 7000),
            ("V005", "Dupont Emballages SAS", "FR", "dupont-emballages.example", "+49 69 90009880", 19000),
            ("V006", "Kessler Antriebe GmbH", "DE", "kessler-antriebe.example", "+49 89 99998070", 8500)]
    return {vid: Vendor(vid, name, c, dom, f"accounts@{dom}", phone, fictional_iban(rng, c), name, [c],
                        [f"dev-{vid.lower()}a"], 14, mean, 30) for vid, name, c, dom, phone, mean in spec}


VENDORS = _vendors()
_rng = random.Random(7)
CREW = {  # the attackers' side of each attack (hidden truth)
    "V001": dict(device="dev-7f3a91", country="NG", email="finance.nordwind@mail.test", phone="+49 152 28817386",
                 iban=fictional_iban(_rng, "LT")),
    "V002": dict(device="dev-c41e02", country="RU", email="bergmann.ap@mailer.test", phone="+49 152 28895456",
                 iban=fictional_iban(_rng, "LT")),
}
ATTACKER_IBANS = {c["iban"]: vid for vid, c in CREW.items()}
BUYER2 = "Weller Maschinenbau GmbH"      # week 3: another member of the consortium
VENDORS2 = {"V101": Vendor("V101", "Brenner Stahlbau GmbH", "DE", "brenner-stahlbau.example",
                           "accounts@brenner-stahlbau.example", "+49 221 4710650",
                           fictional_iban(random.Random(2027), "DE"), "Brenner Stahlbau GmbH", ["DE"],
                           ["dev-v101a"], 14, 26000, 30)}
CREW["V101"] = dict(device="dev-2b9d57", country="NG", email="brenner.finance@mail.test", phone="+49 152 54599371",
                    iban=CREW["V001"]["iban"])   # new device, address and phone; the Nordwind case's mule account
ACT_LANES = {"alone": "Weller on its own (its agents share Meko inside the company)",
             "consortium": "Weller in the consortium (plus what Hallberg confirmed and approved)"}


@dataclass
class Chapter:
    key: str
    when: datetime
    title: str
    story: str
    kind: str = "events"          # events | payments | audit | summary
    events: list[Event] = field(default_factory=list)
    review_after: bool = False
    act: bool = False             # week 3: Weller's two teams instead of Hallberg's
    vendor: str | None = None     # the vendor the chapter's headline is about


def _takeover(vid: str, day: datetime, n_fails: int, vendors: dict[str, Vendor] = VENDORS) -> list[Event]:
    v, c = vendors[vid], CREW[vid]
    t = day + timedelta(hours=9, minutes=2)
    out = [Event(f"{vid}-F{i}", t + timedelta(seconds=11 * i), "portal", "login", vid,
                 dict(device=c["device"], country=c["country"], success=False)) for i in range(n_fails)]
    out += [Event(f"{vid}-P1", t + timedelta(minutes=10), "portal", "password_reset", vid,
                  dict(device=c["device"], country=c["country"])),
            Event(f"{vid}-P2", t + timedelta(minutes=12), "portal", "login", vid,
                  dict(device=c["device"], country=c["country"], success=True)),
            Event(f"{vid}-P3", t + timedelta(minutes=29), "portal", "contact_change", vid,
                  dict(device=c["device"], country=c["country"], field="email", old=v.email, new=c["email"])),
            Event(f"{vid}-P4", t + timedelta(minutes=31), "portal", "contact_change", vid,
                  dict(device=c["device"], country=c["country"], field="phone", old=v.phone, new=c["phone"]))]
    return out


def _bank_change(vid: str, t: datetime, callback_after: timedelta, vendors: dict[str, Vendor] = VENDORS) -> list[Event]:
    v, c = vendors[vid], CREW[vid]
    body = (f"Dear Accounts Payable team,\n\nwe have updated our bank details in your supplier portal. From now on, "
            f"please pay all open invoices to:\nAccount holder: {v.name}\nIBAN: {c['iban']}\n"
            f"Please update this before Friday's payment run.\n\nBest regards,\nFinance Department\n{v.name}")
    return [Event(f"{vid}-M1", t, "inbox", "email", vid,
                  dict(sender=c["email"], reply_to=None, subject="Updated bank details", body=body)),
            Event(f"{vid}-B1", t + timedelta(minutes=2), "vendor_master", "bank_change_request", vid,
                  dict(via="portal", new_iban=c["iban"], holder=v.name, device=c["device"], country=c["country"])),
            Event(f"{vid}-B2", t + callback_after, "vendor_master", "callback", vid,
                  dict(phone=c["phone"], phone_source="master", result="confirmed"))]


def _open_items(vid: str, t: datetime, vendors: dict[str, Vendor]) -> Event:
    """Reconnaissance from the address the crew just put on file: how much is due, and when."""
    v, c = vendors[vid], CREW[vid]
    return Event(f"{vid}-M0", t, "inbox", "email", vid, dict(
        sender=c["email"], reply_to=None, subject="Open items",
        body=f"Hello,\n\nfor our month-end reconciliation, could you send us the list of all open invoices and the "
             f"next payment date?\n\nThank you,\n{v.name}"))


def _routine(day: datetime) -> list[Event]:
    return [Event(f"R-{vid}-{day:%d}", day + timedelta(hours=9 + i, minutes=40), "portal", "login", vid,
                  dict(device=VENDORS[vid].devices[0], country=VENDORS[vid].country, success=True))
            for i, vid in enumerate(("V003", "V005", "V006"))]


def _invoice(vid: str, no: str, amount: float, due: datetime) -> Event:
    v = VENDORS[vid]
    return Event(f"I-{no}", due - timedelta(days=30), "invoices", "invoice", vid,
                 dict(invoice_no=no, amount=amount, currency="EUR", due=due.date().isoformat(), sender=v.email))


MON1, MON2, MON3 = datetime(2026, 11, 16), datetime(2026, 11, 23), datetime(2026, 11, 30)
RUNS = {"run1": MON1 + timedelta(days=3, hours=8), "run2": MON2 + timedelta(days=3, hours=8)}
INVOICES = {
    "run1": [_invoice("V001", "NW-2026-1187", 48300.00, MON1 + timedelta(days=4)),
             _invoice("V003", "VDE-88214", 12480.00, MON1 + timedelta(days=5)),
             _invoice("V004", "BI-2026-0631", 7915.40, MON1 + timedelta(days=6)),
             _invoice("V005", "DE-55102", 21060.00, MON1 + timedelta(days=4)),
             _invoice("V006", "KA-1043", 9340.00, MON1 + timedelta(days=7))],
    "run2": [_invoice("V002", "BV-2026-2290", 31750.00, MON2 + timedelta(days=4)),
             _invoice("V001", "NW-2026-1203", 5200.00, MON2 + timedelta(days=5)),
             _invoice("V006", "KA-1051", 4180.00, MON2 + timedelta(days=6)),
             _invoice("V003", "VDE-88301", 2930.00, MON2 + timedelta(days=4))],
}

CHAPTERS = [
    Chapter("mon1", MON1 + timedelta(hours=9, minutes=12), "Monday · the supplier portal",
            "Someone signs in to Nordwind Logistik's supplier-portal account from a new device in Nigeria, resets "
            "the password and changes the contact email and phone. Each step looks routine on its own.",
            events=_takeover("V001", MON1, 6) + _routine(MON1)),
    Chapter("tue1", MON1 + timedelta(days=1, hours=16, minutes=5), "Tuesday · new bank details",
            "An email asks to pay Nordwind's open invoices to a new IBAN before Friday, and the change arrives "
            "through the portal. The AP clerk does what most companies do: calls the phone number on file to confirm.",
            events=_bank_change("V001", MON1 + timedelta(days=1, hours=16, minutes=5), timedelta(minutes=35)),
            review_after=True),
    Chapter("run1", RUNS["run1"], "Thursday · the payment run",
            "The payments agent starts a new session with no conversation history and prepares five payments, "
            "including Nordwind's invoice of €48,300.", kind="payments"),
    Chapter("audit", RUNS["run1"] + timedelta(hours=1), "Thursday · why?",
            "Anyone should be able to answer \"why did the agent do that?\". The auditor agent rebuilds each answer "
            "from what the agents saved in Meko: the decision record, its reasoning and the signals behind it. A "
            "person then decides whether this attack becomes collective memory for every agent.", kind="audit"),
    Chapter("mon2", MON2 + timedelta(hours=9, minutes=12), "Week 2, Monday · Bergmann Verpackung",
            "The same steps on another vendor: failed logins, a password reset from a new device in Russia, new "
            "contact email and phone.", events=_takeover("V002", MON2, 8) + _routine(MON2)),
    Chapter("tue2", MON2 + timedelta(days=1, hours=9, minutes=5), "Week 2, Tuesday · the same request",
            "Bergmann's new contact address asks for payments to go to a new account, and the change arrives "
            "through the portal. The clerk's callback is planned for the afternoon.",
            events=_bank_change("V002", MON2 + timedelta(days=1, hours=9, minutes=5), timedelta(hours=6, minutes=15)),
            review_after=True),
    Chapter("run2", RUNS["run2"], "Week 2, Thursday · the payment run",
            "Four payments, including Bergmann's €31,750 and Nordwind's next invoice.", kind="payments"),
    Chapter("summary", RUNS["run2"] + timedelta(hours=1), "Two weeks later",
            "What reached the attackers, and when each team acted.", kind="summary"),
    Chapter("mon3", MON3 + timedelta(hours=9, minutes=12), "Week 3, Monday · another company in the consortium",
            f"The crew moves on to {BUYER2}, another company in the same consortium, which has never seen it. With a "
            "new device, a new email address and a new phone it takes over Brenner Stahlbau's supplier-portal account, "
            "and in the afternoon it asks for the list of open invoices. Weller runs the same agents twice: on its "
            "own, and with what Hallberg's analyst confirmed and a person approved.",
            events=_takeover("V101", MON3, 7, VENDORS2) + [_open_items("V101", MON3 + timedelta(hours=13, minutes=20),
                                                                        VENDORS2)],
            review_after=True, act=True, vendor="V101"),
    Chapter("tue3", MON3 + timedelta(days=1, hours=10, minutes=5), "Week 3, Tuesday · the same mule account",
            "The new address asks for payments to go to a new account, the change arrives through the portal, and the "
            "clerk calls back in the afternoon. The account is the one Hallberg confirmed in the Nordwind case.",
            events=_bank_change("V101", MON3 + timedelta(days=1, hours=10, minutes=5), timedelta(hours=5, minutes=35),
                                VENDORS2),
            review_after=True, act=True, vendor="V101"),
    Chapter("network", MON3 + timedelta(days=1, hours=17), "What the consortium shares",
            "Each member gives the consortium only what its analyst confirmed and a person approved, with the decision "
            "trace behind it, and Weller's confirmed case now goes in for the next member.", kind="network", act=True),
]


@dataclass
class Step:
    agent: str
    title: str
    lines: list[str] = field(default_factory=list)
    tone: str = "neutral"          # neutral | good | bad | warn


@dataclass
class Outcome:
    chapter: str
    steps: list[Step]
    headline: str
    tone: str = "neutral"
    notes: list[str] = field(default_factory=list)
    learning: list[str] = field(default_factory=list)   # the auditor's run of Meko's self-learning loop
    claude: list[str] = field(default_factory=list)     # Claude's plain account of the decision, checked


def _n(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _money(x: float) -> str:
    return f"€{x:,.2f}".replace(".00", "")


def _iban(iban: str) -> str:
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


class SharedMemory(Memory):
    """A member's memory in a consortium. Meko cannot delete a promoted memory, so a pattern another member shared and
    then took back stays in the collective memory, and the agents skip it (`revoked`, the ids of such patterns)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.revoked: set[str] = set()

    def patterns(self, agent: str):
        return [h for h in super().patterns(agent) if h.id not in self.revoked]


class Lane:
    """One team of agents with its own memory store and its own view of the money."""

    def __init__(self, mode: str, store, run_id: str | None = None, ledger: Ledger | None = None,
                 vendors: dict[str, Vendor] | None = None):
        self.mode, self.store, self.ledger = mode, store, ledger    # the ledger: the Meko lane's system of record
        self.vendors = vendors or VENDORS
        self.mem = SharedMemory(store, mode, self.vendors, run_id)
        self.agents = {"portal": PortalAgent(self.mem), "support": SupportAgent(self.mem),
                       "inbox": InboxAgent(self.mem)}
        self.vm, self.pay = VendorMasterAgent(self.mem), PaymentAgent(self.mem)
        self.current = {vid: v.iban for vid, v in self.vendors.items()}
        self.changed_at: dict[str, datetime | None] = {vid: None for vid in self.vendors}
        self.paid_ibans = set(self.current.values())
        self.payments: list[dict] = []
        self.confirmed: dict[str, datetime] = {}
        self.first_action: dict[str, datetime] = {}
        self.candidates: dict[str, dict] = {}      # vendor -> pattern candidate waiting for a person
        self.decisions: dict[str, dict] = {}       # vendor -> the decision record behind the first protective action
        self.log: list[dict] = []                  # every decision, with the search and the memories behind it
        self.requests: dict[str, Event] = {}       # vendor -> its latest bank-change request
        self.asked: dict[str, dict] = {}           # vendor -> the vendor-master agent's question about earlier cases
        self.learning: dict = {}                   # what the auditor's run of Meko's self-learning loop found
        self.consortium: dict[str, dict] = {}      # week 3: attacker infrastructure other members confirmed
        self.flagged: set[tuple[str, str]] = set()
        self.shared_patterns: dict[str, str] = {}  # memory id of a pattern another member approved -> its case
        self.reader = None                         # Claude, in the Meko lane only: it reads, it never decides

    # ---------- helpers ----------
    def _decision(self, agent: str, vendor_id: str, at: datetime, what: str, risk: int, reasons: list[str],
                  rule: str, outcome: str, since: int) -> str:
        """Write every decision to Meko (allow, step-up, hold, block, reject or freeze) as a conversation turn with its
        reasoning, its plan and the search behind it: the query and the ids of the memories it found (Meko shows the
        turn as a decision trace). The same decision goes into `sentinel-audit` for the auditor agent to find.
        Only the agent's own search during this event counts (`since` indexes `Memory.reads`), and the reasoning
        says how the risk was reached when it is more than the weights of those memories."""
        reads = [sigs for who, vid, sigs in self.mem.reads[since:] if who == agent and vid == vendor_id]
        seen = reads[-1] if reads else []
        found, _ = risk_of(seen)
        risk = risk or found                        # a block on a frozen vendor carries no risk of its own
        if not seen:
            basis = f"No memory search for this decision; risk {risk} from its own check ({', '.join(reasons)})"
        else:
            basis = (f"Risk {found} from {_n(len(set(s.type for s in seen)), 'signal type')} written by "
                     f"{', '.join(AGENT_NAMES.get(a, a) for a in sorted({s.agent for s in seen}))}")
            if risk > found:
                extra = [t for t in reasons if t not in {s.type for s in seen}]
                basis += (f", plus {risk - found} for matching an approved attack pattern" if extra == ["pattern.match"]
                          else f", plus {risk - found} from {', '.join(extra)}")
        reasoning = f"{basis}; {rule}."
        trace = dict(outcome=outcome, risk=risk, rule=rule, query=self.mem.query(vendor_id),
                     datapack="sentinel-signals", memory_ids=[s.memory_id for s in seen if s.memory_id],
                     signal_types=sorted({s.type for s in seen}))
        if agent == "vendor_master" and self.asked.get(vendor_id, {}).get("related"):
            trace["related_case"] = f"{self.asked[vendor_id]['related']} ({self.asked[vendor_id]['link']})"
        conv = self.mem.conversation(agent)
        trace_id = self.store.add_message(self.mem.signals_dp, conv, agent, f"{self.vendors[vendor_id].name}: {what}",
                                          what,
                                          reasoning=reasoning, plan=[f"search Meko for {vendor_id}'s signals",
                                                                     "add up the weights", rule], metadata=trace)
        self.mem.record_decision(agent, vendor_id, at, what, risk, rule, reasoning, outcome, trace["memory_ids"])
        self.log.append(dict(trace, agent=agent, vendor_id=vendor_id, at=at, what=what, reasoning=reasoning,
                             signals=seen, trace_id=trace_id))
        if outcome != "allow":
            self.decisions.setdefault(vendor_id, dict(agent=agent, at=at, what=what, risk=risk, reasons=reasons,
                                                      rule=rule, reasoning=reasoning))
            self.first_action.setdefault(vendor_id, at)
        return trace_id

    def _signal_lines(self, sigs: list[Signal]) -> list[str]:
        lines = []
        for s in sigs:
            detail = s.detail
            for vid, cand in self.candidates.items():     # name the case a pattern came from, not its memory id
                for mid in cand["ids"]:
                    detail = detail.replace(f"pattern {mid[:8]}", f"pattern from the {self.vendors[vid].name} case")
            for mid, case in self.shared_patterns.items():
                detail = detail.replace(f"pattern {mid[:8]}", f"pattern {case}")
            lines.append(f"`{s.type}` (+{s.weight}) — {detail}")
        return lines

    def case_question(self, e: Event) -> str:
        iban = e.data["new_iban"]
        return (f"Has the bank account {iban} ({COUNTRIES.get(iban[:2], iban[:2])}) or the device {e.data['device']} "
                f"appeared in an earlier confirmed fraud case? Bank-change request for "
                f"{self.vendors[e.vendor_id].name}.")

    def _claude_reads(self, e: Event) -> list[str]:
        """Claude reads the email: each red flag with its quote, checked against the email's text. Nothing it says
        carries weight, so the decisions stay those of the measured rules."""
        v = self.vendors[e.vendor_id]
        conv = self.mem.conversation("inbox") if self.reader.store is not None else None
        res = self.reader.read_email(v.name, v.domain, e.data, conv)
        lines = [f"Claude reads it: {f.kind.replace('_', ' ')}, \"{f.quote}\" ({f.why})" for f in res.items]
        if res.dropped:
            lines.append(f"Claude: {_n(len(res.dropped), 'flag')} dropped, the quote is not in the email")
        if res.note:
            lines.append(f"Claude: {res.note}")
        return lines or ["Claude reads it: no red flag it can quote"]

    def _check_known(self, e: Event, agent) -> None:
        """Week 3, round 5's rule: a value in the event that the consortium lists as confirmed attacker infrastructure
        adds `consortium.known_infrastructure`, once per vendor and value, saying where it came from."""
        text = " ".join(str(v) for v in e.data.values())
        for value in sorted(self.consortium):
            if value in text and (e.vendor_id, value) not in self.flagged:
                self.flagged.add((e.vendor_id, value))
                c = self.consortium[value]
                shown = _iban(value) if c["what"] == "bank account" else value
                agent.emit(e, "consortium.known_infrastructure",
                           f"The {c['what']} {shown} was used in the {c['case']} case that {c['company']} confirmed "
                           f"on {c['confirmed']:%d %b} (shared by the consortium; decision trace {c['trace'][:8]}).")

    @staticmethod
    def _link(case_text: str, iban: str, device: str) -> tuple[int, str] | None:
        """What an earlier case file shares with a request, checked in the case's own text: the same account (3), the
        same device (2), or a new account in the same country (1). None when it shares none of them."""
        if iban in case_text:
            return 3, "the same bank account"
        if device in case_text:
            return 2, "the same device"
        m = CASE_ACCOUNT.search(case_text)
        if m and m[1][:2] == iban[:2]:
            return 1, f"a new account in {COUNTRIES.get(iban[:2], iban[:2])} too"
        return None

    def _ask_about_request(self, e: Event) -> str:
        """The vendor-master agent asks Meko in plain words, across memory, knowledge base and past conversations
        (`context_search` in `sentinel-patterns`), whether the new account or device belongs to an earlier confirmed
        case. Meko grades every such search, and the weak ones feed its self-learning loop. A case file counts only
        for what it shares with this request, checked in its text. The answer goes into the decision's trace; the
        decision itself stays with the rules."""
        q, iban, device = self.case_question(e), e.data["new_iban"], e.data["device"]
        asked_at = now_iso()
        found = self.store.context_search(self.mem.patterns_dp, self.mem.conversation("vendor_master"),
                                          "vendor_master", q, limit=5)
        quality = self.store.last_search.get("quality") or "not graded"
        best = None                                          # (strength, link, vendor id, vendor name)
        for h in found["knowledge_base"]:
            title = CASE_NAME.search(h.text)
            link = self._link(h.text, iban, device) if title else None
            if link and (best is None or link[0] > best[0]):
                best = (link[0], link[1], title[2], title[1])
        self.asked[e.vendor_id] = dict(query=q, quality=quality, related=best[3] if best else None,
                                       link=best[1] if best else None, at=e.ts, asked_at=asked_at)
        ask = "Asked Meko whether this account or device appeared in an earlier case:"
        if best and best[2] == e.vendor_id:                  # only on real Meko, after an earlier demo run's audit
            return (f"{ask} **this vendor's case is already in Meko** from an earlier demo run on the same "
                    f"datapacks (match {quality})")
        if best and best[0] >= 2:
            return f"{ask} **{best[1]} appears in a confirmed case, {best[3]}** (Meko rates the match {quality})"
        if best:
            return (f"{ask} not this account or device, but **a similar confirmed case: {best[3]}**, with {best[1]} "
                    f"(Meko rates the match {quality})")
        return (f"{ask} nothing specific (Meko rates the match {quality} and keeps the search for its self-learning "
                f"loop)")

    # ---------- chapters ----------
    def run_events(self, chapter: Chapter) -> Outcome:
        steps: list[Step] = []
        total_fails = {vid: sum(1 for e in chapter.events if e.vendor_id == vid and e.kind == "login"
                                and not e.data["success"]) for vid in self.vendors}
        first_fail = {}
        for e in chapter.events:
            if e.id.startswith("R-"):
                self.agents["portal"].on_event(e)        # routine logins: no step on screen
                continue
            w0, r0, a0, f0 = len(self.mem.written), len(self.mem.reads), len(self.vm.actions), len(self.mem.freezes)
            if e.channel in self.agents:                 # round 5's order: channel agents check after reading,
                self.agents[e.channel].on_event(e)       # the vendor master before it decides
                self._check_known(e, self.agents[e.channel])
            else:
                self._check_known(e, self.vm)
                self.vm.on_event(e)
            agent = e.channel if e.channel in self.agents else "vendor_master"
            title = _event_title(e)
            if e.kind == "login" and not e.data["success"]:
                first_fail.setdefault(e.vendor_id, e.ts)
                title = (f"{first_fail[e.vendor_id]:%a %H:%M} · {total_fails[e.vendor_id]} failed portal logins "
                         f"within a minute from {e.data['device']} ({e.data['country']})")
            step = Step(agent, title, self._signal_lines(self.mem.written[w0:]))
            if e.kind == "email" and self.reader is not None:
                step.lines += self._claude_reads(e)
            if e.kind == "bank_change_request":
                self.requests[e.vendor_id] = e
                if self.mode != "siloed":              # in silos there is no shared case history to ask
                    step.lines.append(self._ask_about_request(e))
                if self.ledger:
                    self.ledger.bank_change_requested(e.id, e.vendor_id, e.data["new_iban"], e.ts, e.data["via"])
            if e.kind == "contact_change" and self.ledger:
                self.ledger.contact_changed(e.vendor_id, e.data["field"], e.data["new"], e.ts,
                                            f"supplier portal, device {e.data['device']}")
            own_reads = [(vid, sigs) for who, vid, sigs in self.mem.reads[r0:] if who == agent]
            if e.kind == "callback" and own_reads:
                vid, sigs = own_reads[-1]
                others = sorted({AGENT_NAMES[s.agent] for s in sigs if s.agent != agent})
                step.lines.append(f"Searched memory for {self.vendors[vid].name}: {_n(len(sigs), 'signal')} in 30 days"
                                  + (f", from {', '.join(others)}" if others else ", none from other agents"))
            for f in self.mem.freezes[f0:]:
                step.tone = "good"
                step.lines.append(f"**Matches an attack pattern a person approved: bank details frozen** "
                                  f"(risk {f.risk})")
                trace_id = self._decision(f.agent, f.vendor_id, f.at, "bank details frozen by an approved pattern",
                                          f.risk, f.reasons, "freeze when the vendor matches an approved attack pattern",
                                          "freeze", r0)
                if self.ledger:
                    self.ledger.frozen(f.vendor_id, f.at, trace_id)
            for act in self.vm.actions[a0:]:
                step.tone = "good"
                if act.kind == "reject_bank_change":
                    step.lines.append("**Bank change rejected** — the vendor denied asking for it when called back")
                    trace_id = self._decision("vendor_master", act.vendor_id, act.at, "bank change rejected",
                                              act.risk, act.reasons,
                                              "reject a change the vendor denies on the phone number on file",
                                              "reject", r0)
                    if self.ledger:
                        self.ledger.bank_change_decided(act.vendor_id, "rejected", act.at, trace_id)
                    continue
                step.lines.append(f"**Bank change blocked** — risk {act.risk} ≥ {BLOCK_BANK_CHANGE_AT}"
                                  if act.risk else "**Bank change blocked** — the vendor's bank details are frozen")
                trace_id = self._decision("vendor_master", act.vendor_id, act.at, "bank change blocked", act.risk,
                                          act.reasons, f"block a confirmed bank change at risk {BLOCK_BANK_CHANGE_AT} "
                                          f"or more" if act.risk else "block every bank change while the vendor's bank "
                                          "details are frozen", "block", r0)
                if self.ledger:
                    self.ledger.bank_change_decided(act.vendor_id, "blocked", act.at, trace_id)
            applied = self.vm.applied.get(e.vendor_id)
            if e.kind == "callback" and applied and applied[1] == e.ts:
                self.current[e.vendor_id], self.changed_at[e.vendor_id] = applied
                risk, reasons = risk_of(own_reads[-1][1]) if own_reads else (0, [])
                step.tone = "bad"
                step.lines.append(f"Callback confirmed, risk {risk} < {BLOCK_BANK_CHANGE_AT}: "
                                  f"**new bank details applied** ({_iban(applied[0])})")
                trace_id = self._decision("vendor_master", e.vendor_id, e.ts, "bank change applied", risk, reasons,
                                          f"apply a confirmed bank change below risk {BLOCK_BANK_CHANGE_AT}", "allow",
                                          r0)
                if self.ledger:
                    self.ledger.bank_change_decided(e.vendor_id, "applied", e.ts, trace_id)
            if step.lines or e.kind in ("callback", "bank_change_request"):
                steps.append(step)
        if chapter.review_after:
            steps += self.review(chapter.when)
        tones = {s.tone for s in steps}
        tone = "bad" if "bad" in tones else "good" if "good" in tones else "neutral"
        return Outcome(chapter.key, steps, self._headline(chapter), tone)

    def review(self, at_chapter: datetime) -> list[Step]:
        """The analyst looks at every vendor an agent stopped, by calling the number on file 60 days earlier."""
        flagged: dict[str, datetime] = {}
        for a in list(self.vm.actions) + [f for f in self.mem.freezes if f.agent != "analyst"]:
            flagged[a.vendor_id] = max(a.at, flagged.get(a.vendor_id, a.at))   # review after the last action
        steps = []
        for vid, at in flagged.items():
            if vid in self.confirmed:
                continue
            when = max(at, at_chapter) + timedelta(minutes=50)
            self.confirmed[vid] = when
            v = self.vendors[vid]
            said = ("We never asked to change our bank details." if vid in self.requests else
                    "We never changed our contact details.")
            types = sorted(observable(s.type for s in self.mem.signals("auditor", vid, when)))
            self.mem.resolve(vid, when, "attack confirmed by calling the vendor; real bank details kept.")
            self.mem.freezes.append(Action(when, "analyst", "freeze_vendor", vid, 0, ["confirmed incident"], vid))
            text = (f"Confirmed attack pattern on vendor {vid}: these signals appeared within 30 days before an "
                    f"attempt to redirect payments to a new bank account. Signal types: [{', '.join(types)}] "
                    f"[run {self.mem.run_id}]")
            ids = self.store.memory_add(self.mem.patterns_dp, self.mem.conversation("auditor"), "auditor", text,
                                        dict(kind="pattern", signal_types=types, learning_candidate=True,
                                             vendor_id=vid))
            self.candidates[vid] = dict(ids=ids, types=types, promoted=False, at=when)
            phone, source = v.phone, ""
            if self.ledger:                          # an "as of" query on the vendor master's history
                phone = self.ledger.value_as_of(vid, "phone", when - timedelta(days=60)) or v.phone
                source = " (the ledger's history)"
                self.ledger.frozen(vid, when, None)
            steps.append(Step("analyst", f"{when:%a %H:%M} · calls {v.name} on {phone}, the number on file "
                                         f"60 days ago{source}", [f"{v.name}: \"{said}\" **Attack confirmed**; bank "
                                                          f"details stay frozen.", f"The auditor agent writes the case "
                                                          f"up as a pattern candidate ({len(types)} signal types)."],
                              "good"))
        if not steps and not flagged:
            steps.append(Step("analyst", "No alert to review",
                              ["No agent saw enough on its own to raise an alert." if self.mode == "siloed" else
                               "No agent has stopped anything yet: there is no request to block and no payment to "
                               "hold."], "neutral"))
        return steps

    def run_payments(self, chapter: Chapter) -> Outcome:
        run = chapter.when
        self.mem.new_session("payments")             # every payment run starts with no conversation history
        step = Step("payments", f"{run:%a %H:%M} · new session, {len(INVOICES[chapter.key])} payments due")
        for inv in INVOICES[chapter.key]:
            v = self.vendors[inv.vendor_id]
            iban = self.current[v.id]
            r0 = len(self.mem.reads)
            act = self.pay.decide(run, v, inv, iban, self.changed_at[v.id], self.paid_ibans)
            amount = inv.data["amount"]
            to_attacker = iban in ATTACKER_IBANS
            if act is None:
                risk, reasons = self.pay.last_risk
                trace_id = self._decision("payments", v.id, run, f"payment of {_money(amount)} released", risk,
                                          reasons, f"release below risk {HOLD_AT}, and below {STEP_UP_AT} after a bank "
                                          f"change", "allow", r0)
                if self.ledger:
                    self.ledger.payment(inv.data["invoice_no"], v.id, run, amount, iban, "paid", trace_id)
                self.paid_ibans.add(iban)
                self.payments.append(dict(run=chapter.key, vendor=v.name, invoice=inv.data["invoice_no"],
                                          amount=amount, iban=iban, to_attacker=to_attacker, decision="paid"))
                if to_attacker:
                    step.tone = "bad"
                    step.lines.append(f"**Paid {_money(amount)} to {_iban(iban)}** — {v.name}'s new account, "
                                      f"changed {self.changed_at[v.id]:%A} (it belongs to the attacker)")
                else:
                    step.lines.append(f"Paid {_money(amount)} to {v.name}")
            else:
                rule = (f"hold for a person at risk {HOLD_AT} or more" if act.kind == "hold" else
                        f"call the number on file 60 days ago before paying, at risk {STEP_UP_AT} or more after a "
                        f"bank change")
                trace_id = self._decision("payments", v.id, run, f"payment {act.kind.replace('_', '-')}", act.risk,
                                          act.reasons, rule, act.kind, r0)
                if self.ledger:
                    self.ledger.payment(inv.data["invoice_no"], v.id, run, amount, iban, act.kind, trace_id)
                self.payments.append(dict(run=chapter.key, vendor=v.name, invoice=inv.data["invoice_no"],
                                          amount=amount, iban=iban, to_attacker=False, decision=act.kind))
                step.tone = "good" if to_attacker else "warn"
                step.lines.append(f"**{act.kind.replace('_', '-').capitalize()} {_money(amount)} to {v.name}** — "
                                  f"risk {act.risk}: {', '.join(act.reasons[:3])}")
        return Outcome(chapter.key, [step], self._headline(chapter), self._tone(chapter))

    def lost(self) -> float:
        return sum(p["amount"] for p in self.payments if p["to_attacker"])

    def _tone(self, chapter: Chapter) -> str:
        return "bad" if any(p["to_attacker"] and p["run"] == chapter.key for p in self.payments) else "neutral"

    def _headline(self, chapter: Chapter) -> str:
        if chapter.kind == "payments":
            lost = sum(p["amount"] for p in self.payments if p["to_attacker"] and p["run"] == chapter.key)
            return f"{_money(lost)} sent to the attacker" if lost else "Nothing sent to the attacker"
        vid = chapter.vendor or ("V001" if chapter.key.endswith("1") else "V002")
        if vid in self.decisions:
            d = self.decisions[vid]
            return f"{AGENT_NAMES[d['agent']]}: {d['what']} ({d['at']:%a %H:%M})"
        if self.current[vid] != self.vendors[vid].iban:
            return f"{self.vendors[vid].name}'s payments now go to the attacker's account"
        return "Signals written to memory; nothing stopped yet"


def _event_title(e: Event) -> str:
    d, t = e.data, f"{e.ts:%a %H:%M}"
    if e.kind == "login":
        return f"{t} · portal login {'failed' if not d['success'] else 'succeeded'} from {d['device']} ({d['country']})"
    if e.kind == "password_reset":
        return f"{t} · portal password reset from {d['device']} ({d['country']})"
    if e.kind == "contact_change":
        return f"{t} · contact {d['field']} changed to {d['new']}"
    if e.kind == "email":
        return f"{t} · email from {d['sender']}: \"{d['subject']}\""
    if e.kind == "bank_change_request":
        return f"{t} · bank-change request via the {d['via']}: {_iban(d['new_iban'])}"
    if e.kind == "callback":
        return f"{t} · the clerk calls {d['phone']} (the number on file) — \"yes, that's right\""
    return f"{t} · {e.kind}"


class Story:
    """Both lanes, advanced one chapter at a time."""

    def __init__(self, make_store=LocalMeko, run_id: str | None = None, make_ledger=SqliteLedger, reader=None):
        """`make_store` builds the Meko lane's store (real Meko or the simulator), `make_ledger` its ledger (Postgres in
        Meko, or SQLite). The siloed lane always runs on the simulator, with no ledger: it stands for separate tools
        with no shared memory."""
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self.ledger = make_ledger(f"{self.run_id}-meko")
        self.ledger.setup(VENDORS, since=MON1 - timedelta(days=120))
        self.lanes = {"siloed": Lane("siloed", LocalMeko(), f"{self.run_id}-siloed"),
                      "learning2": Lane("learning2", make_store(), f"{self.run_id}-meko", self.ledger)}
        self.done: list[tuple[Chapter, dict[str, Outcome]]] = []
        self.act: dict[str, Lane] = {}                 # week 3: Weller on its own and in the consortium
        meko = self.lanes["learning2"]
        self.reader = meko.reader = reader              # Claude, when there is a key (`reader.Reader.from_env`)
        if reader is not None and not isinstance(meko.store, Replayer):   # a replay holds only recorded calls
            reader.store, reader.datapack = meko.store, meko.mem.signals_dp
        for doc in sorted(KNOWLEDGE.glob("*.md")):     # the company's policy and playbook, in Meko's knowledge base
            meko.store.knowledge_upload(meko.mem.patterns_dp, doc.name, doc.read_bytes())

    @property
    def next_chapter(self) -> Chapter | None:
        return CHAPTERS[len(self.done)] if len(self.done) < len(CHAPTERS) else None

    def advance(self) -> None:
        ch = self.next_chapter
        if ch is None:
            return
        if ch.act and not self.act:
            self._start_act()
        out = {}
        for mode, lane in (self.act if ch.act else self.lanes).items():
            if ch.kind == "events":
                out[mode] = lane.run_events(ch)
            elif ch.kind == "payments":
                out[mode] = lane.run_payments(ch)
            elif ch.kind == "network":
                if mode == "consortium":                   # Weller's confirmed case goes in for the next member
                    self._share_list(lane, BUYER2, lane)
                out[mode] = Outcome(ch.key, [], "")
            else:
                notes = self.explain(mode, "V001", ch.when) if ch.kind == "audit" else []
                learning = []
                if notes and mode == "learning2":
                    notes.append(self._save_report(lane, "V001", notes))
                    learning = self.self_learning(lane, "V001")
                claude = self._claude_explains(lane, "V001") if notes and lane.reader is not None else []
                out[mode] = Outcome(ch.key, [], "", notes=notes, learning=learning, claude=claude)
        self.done.append((ch, out))

    @staticmethod
    def _claude_explains(lane: Lane, vendor_id: str) -> list[str]:
        """Claude's plain account of the first protective decision on a vendor, from its trace; sentences citing
        something the trace does not hold are dropped before they are shown."""
        d = next((x for x in lane.log if x["vendor_id"] == vendor_id and x["outcome"] != "allow"), None)
        if d is None:
            return []
        conv = lane.mem.conversation("auditor") if lane.reader.store is not None else None
        res = lane.reader.explain(d, lane.vendors[vendor_id].name, conv)
        lines = [s["text"] for s in res.items]
        if res.dropped:
            lines.append(f"({_n(len(res.dropped), 'sentence')} dropped: it cited something the trace does not hold)")
        if res.note:
            lines.append(f"Claude: {res.note}")
        return lines

    def _start_act(self) -> None:
        """Week 3: Weller's two teams, both with Meko inside the company. They run on the offline simulator whatever
        engine Hallberg's lane uses, so a recorded run of weeks 1-2 stays valid. The consortium team first receives
        what Hallberg shares, then reads the shared list back from its `consortium` datapack."""
        joined = Lane("learning2", LocalMeko(), f"{self.run_id}-weller-consortium", vendors=VENDORS2)
        self.share(self.lanes["learning2"], BUYER, joined)
        joined.consortium = self._consortium_list(joined)
        self.act = {"alone": Lane("learning2", LocalMeko(), f"{self.run_id}-weller", vendors=VENDORS2),
                    "consortium": joined}

    @staticmethod
    def infrastructure(lane: Lane, vendor_id: str) -> dict:
        """The account, device and phone of a confirmed attack, as the company's own systems logged them (the
        bank-change request and the phone change), and the decision trace behind the first stop."""
        req = lane.requests.get(vendor_id)
        phones = [s.extra.get("new_phone") for s in lane.mem.written
                  if s.vendor_id == vendor_id and s.type == "contact.phone_changed" and s.extra.get("new_phone")]
        stop = next((d for d in lane.log if d["vendor_id"] == vendor_id and d["outcome"] != "allow"), None)
        values = [("bank account", req.data["new_iban"]), ("device", req.data["device"])] if req else []
        return dict(values=values + ([("phone", phones[-1])] if phones else []),
                    trace=stop["trace_id"] if stop else "")

    def _share_list(self, source: Lane, company: str, target: Lane) -> None:
        """The infrastructure of every attack `source`'s analyst confirmed goes into the `consortium` datapack of
        `target`'s store (one datapack shared by the members; here, simulated in one place)."""
        store, conv = target.store, target.mem.conversation("auditor")
        dp = store.ensure_datapack("consortium", "Attacker infrastructure the member companies confirmed")
        for vid, at in source.confirmed.items():
            case, info = source.vendors[vid].name, self.infrastructure(source, vid)
            for what, value in info["values"]:
                store.memory_add(dp, conv, "auditor", f"Confirmed attacker {what} shared by {company}: {value}, used "
                                 f"in the {case} case confirmed on {at:%d %b %Y}; decision trace {info['trace']}.",
                                 dict(kind="infrastructure", what=what, value=value, company=company, case=case,
                                      confirmed=at.isoformat(), trace=info["trace"]))

    def share(self, source: Lane, company: str, target: Lane) -> None:
        """What one member gives the consortium, and only that: the infrastructure of each attack its analyst
        confirmed, the attack patterns a person approved (into the receiving company's collective memory) and the
        confirmed case files (into its knowledge base). Raw signals and vendor data stay at home."""
        self._share_list(source, company, target)
        store, mem = target.store, target.mem
        conv = mem.conversation("auditor")
        for vid in source.confirmed:
            case, cand = source.vendors[vid].name, source.candidates.get(vid)
            if cand and cand["promoted"]:
                ids = store.memory_add(mem.patterns_dp, conv, "auditor",
                                       f"Attack pattern approved at {company} after the {case} case, shared by the "
                                       f"consortium: these signals appeared within 30 days before an attempt to "
                                       f"redirect payments to a new bank account. Signal types: "
                                       f"[{', '.join(cand['types'])}] [run {mem.run_id}]",
                                       dict(kind="pattern", company=company, case=case))
                store.promote(mem.patterns_dp, ids)
                target.shared_patterns[ids[0]] = f"from the {case} case at {company}"
            if vid in source.requests:
                key = store.knowledge_upload(mem.patterns_dp, f"consortium-case-{vid}.md",
                                             self._case_file(source, vid).encode("utf-8"), conversation_id=conv)
                store.knowledge_ready(mem.patterns_dp, key, conv)

    @staticmethod
    def _consortium_hits(lane: Lane) -> tuple[list[dict], set[tuple[str, str]]]:
        """The consortium's entries and its revocations, read back from its datapack: (entries, revoked cases)."""
        dp = lane.store.ensure_datapack("consortium", "Attacker infrastructure the member companies confirmed")
        hits = lane.store.memory_search(dp, lane.mem.conversation("auditor"), "auditor",
                                        "confirmed attacker bank account device phone shared by revocation case",
                                        limit=100)
        found = [h.metadata for h in hits]
        return ([m for m in found if m.get("kind") == "infrastructure"],
                {(m["company"], m["case"]) for m in found if m.get("kind") == "revocation"})

    def _consortium_list(self, lane: Lane) -> dict[str, dict]:
        """The shared list as Weller's agents read it from Meko: value -> where it came from (first confirmation that
        its member has not taken back)."""
        entries, revoked = self._consortium_hits(lane)
        out: dict[str, dict] = {}
        for m in sorted(entries, key=lambda m: m["confirmed"]):
            if (m["company"], m["case"]) not in revoked:
                out.setdefault(m["value"], dict(m, confirmed=datetime.fromisoformat(m["confirmed"])))
        return out

    def revoke(self, company: str, case: str, reason: str) -> list[str]:
        """A member takes back everything it shared about one case. Nothing is deleted: a revocation record goes into
        the consortium datapack (the audit trail stays), the shared list is read again without the case, and the
        pattern from that case stops counting in the members' checks. Returns the values that left the list."""
        lane = self.act["consortium"]
        store, conv = lane.store, lane.mem.conversation("auditor")
        dp = store.ensure_datapack("consortium", "Attacker infrastructure the member companies confirmed")
        at = next(ch.when for ch in CHAPTERS if ch.key == "network")
        store.memory_add(dp, conv, "auditor", f"Revocation by {company}: everything shared about the {case} case is "
                         f"taken back on {at:%d %b %Y}. Reason: {reason}",
                         dict(kind="revocation", company=company, case=case, at=at.isoformat(), reason=reason))
        before = set(lane.consortium)
        lane.consortium = self._consortium_list(lane)
        lane.mem.revoked |= {mid for mid, src in lane.shared_patterns.items()
                             if src == f"from the {case} case at {company}"}
        return sorted(before - set(lane.consortium))

    def network(self) -> dict:
        """The consortium after week 3, read back from its datapack, and when each of Weller's teams acted."""
        lane, rows = self.act["consortium"], {}
        entries, revoked = self._consortium_hits(lane)
        for m in sorted(entries, key=lambda m: (m["confirmed"], m["what"])):
            row = rows.setdefault(m["value"], dict(what=m["what"], value=m["value"], by=[], active=False))
            gone = (m["company"], m["case"]) in revoked
            row["by"].append(f"{m['company']} ({m['case']} case, {datetime.fromisoformat(m['confirmed']):%d %b}, "
                             f"trace {m['trace'][:8]})" + (", taken back" if gone else ""))
            row["active"] = row["active"] or not gone
        cases = list(dict.fromkeys((m["company"], m["case"]) for m in sorted(entries, key=lambda m: m["confirmed"])))
        ask = min(e.ts for ch in CHAPTERS if ch.key == "tue3" for e in ch.events)
        return dict(rows=list(rows.values()), asked=ask, cases=cases, revoked=sorted(revoked),
                    patterns=[src for mid, src in lane.shared_patterns.items() if mid not in lane.mem.revoked],
                    alone=self.act["alone"].first_action.get("V101"), consortium=lane.first_action.get("V101"))

    def promote(self, vendor_id: str) -> bool:
        """A person approves the pattern: it becomes collective memory for every agent (Meko's promotion)."""
        lane = self.lanes["learning2"]
        cand = lane.candidates.get(vendor_id)
        if not cand or cand["promoted"]:
            return False
        lane.store.promote(lane.mem.patterns_dp, cand["ids"])
        cand["promoted"] = True
        return True

    def explain(self, mode: str, vendor_id: str, until: datetime) -> list[str]:
        """Plain-language account of the first protective decision on a vendor, or, when nothing was stopped, of the
        decisions that let the money through. The auditor agent rebuilds it from memory: the decision records it
        finds in `sentinel-audit` and the signals in `sentinel-signals`."""
        lane = self.lanes[mode]
        v = VENDORS[vendor_id]
        found = [d for d in lane.mem.decisions("auditor", vendor_id) if datetime.fromisoformat(d["at"]) <= until]
        stopped = [d for d in found if d.get("outcome", "block") != "allow"]
        if not stopped:
            lines = [f"No agent stopped anything for {v.name}."]
            for d in found:
                lines.append(f"{AGENT_NAMES[d['agent']]}, {datetime.fromisoformat(d['at']):%a %H:%M}: "
                             f"**{d['what']}**. Its decision record: {d['reasoning']}")
            per_agent: dict[str, list[Signal]] = {}
            for s in lane.mem.history("auditor", vendor_id, until):
                per_agent.setdefault(AGENT_NAMES[s.agent], []).append(s)
            lines.append("Each agent saw only its own part:")
            limits = {AGENT_NAMES["vendor_master"]: f"its limit to block a bank change is {BLOCK_BANK_CHANGE_AT}",
                      AGENT_NAMES["payments"]: f"its limit to hold a payment is {HOLD_AT}"}
            for name, ss in per_agent.items():
                risk, _ = risk_of(ss)
                lines.append(f"{name}: {_n(len(ss), 'signal')}, risk {risk} — "
                             + (limits.get(name) or "it decides nothing itself, and no other agent could read them"))
            return lines
        d = stopped[0]
        at = datetime.fromisoformat(d["at"])
        lines = [f"**{AGENT_NAMES[d['agent']]}, {at:%A %H:%M}: {d['what']}.** Risk {d['risk']}; rule: {d['rule']}.",
                 "The signals it found in Meko before it acted:"]
        for s in lane.mem.history("auditor", vendor_id, at):
            lines.append(f"{s.at:%a %H:%M} · {AGENT_NAMES[s.agent]} · `{s.type}` (+{s.weight}) — {s.detail}")
        lines.append(f"Reasoning saved with the decision: {d['reasoning']}")
        policy = lane.store.knowledge_search(lane.mem.patterns_dp, lane.mem.conversation("auditor"), "auditor",
                                             "bank-change request confirmed by calling the phone number on file "
                                             "60 days before, blocking a change", limit=1)
        if policy:
            text = " ".join(policy[0].text.replace("#", "").split())
            lines.append(f"Policy behind the rule, found in Meko's knowledge base: \"{text[:320]}\"")
        if lane.ledger:
            lines += self._ledger_lines(lane.ledger, vendor_id, until)
        return lines

    @staticmethod
    def _ledger_lines(ledger: Ledger, vendor_id: str, until: datetime) -> list[str]:
        """What the ledger holds about the case, each record with the id of the decision trace behind it in Meko."""
        v, out = VENDORS[vendor_id], []
        for b in ledger.table("bank_changes"):
            if b["vendor_id"] == vendor_id and b["decided_at"] and datetime.fromisoformat(b["decided_at"]) <= until:
                out.append(f"Ledger ({ledger.kind}): the bank-change request of "
                           f"{datetime.fromisoformat(b['requested_at']):%a %H:%M} to {_iban(b['new_iban'])} is recorded "
                           f"as **{b['outcome']}** at {datetime.fromisoformat(b['decided_at']):%a %H:%M} "
                           f"(decision trace `{str(b['trace_id'])[:8]}`)")
        for p in ledger.table("payments"):
            if p["vendor_id"] == vendor_id and datetime.fromisoformat(p["run_at"]) <= until:
                account = "the vendor's own account" if p["iban"] == v.iban else "a new account"
                out.append(f"Ledger: invoice {p['invoice_no']} for {_money(p['amount'])}, "
                           f"{datetime.fromisoformat(p['run_at']):%a %H:%M}, **{p['decision']}** to {account} "
                           f"({_iban(p['iban'])}; decision trace `{str(p['trace_id'])[:8]}`)")
        return out

    @staticmethod
    def _save_report(lane: Lane, vendor_id: str, lines: list[str]) -> str:
        """The auditor keeps the case report as an artifact in `sentinel-audit` (the same bytes give the same hash)."""
        report = f"# Case report: {VENDORS[vendor_id].name}\n\n" + "\n".join(f"- {ln}" for ln in lines) + "\n"
        digest = lane.store.artifact_put(lane.mem.audit_dp, lane.mem.conversation("auditor"), "auditor",
                                         f"case-{vendor_id}-{lane.mem.run_id}.md", report.encode("utf-8"),
                                         "text/markdown")
        return f"Case report saved in Meko as an artifact (sha256 {digest[:12]}…)"

    def self_learning(self, lane: Lane, vendor_id: str) -> list[str]:
        """Meko's self-learning loop, run by the auditor agent once a case is confirmed. Meko grades and logs every
        `context_search` (`selflearning_status` gives the counts); the auditor finds the vendor-master agent's question
        about this case in Meko's search log, adds what was missing (the confirmed case file) to the knowledge base,
        and asks Meko to replay the question (`selflearning_verify`) to check the fix."""
        if vendor_id not in lane.confirmed:
            return []
        mem, store = lane.mem, lane.store
        conv = mem.conversation("auditor")
        status = store.selflearning_status(mem.patterns_dp, conv)
        q = status.get("quality") or {}
        n = q.get("searches") or 0
        lines = [f"Meko's self-learning check of `sentinel-patterns`: {q.get('high') or 0} of {n} "
                 f"{'search' if n == 1 else 'searches'} today found a strong match"]
        asked = lane.asked.get(vendor_id)
        row = store.search_log(mem.patterns_dp, conv, asked["query"], since=asked["asked_at"]) if asked else None
        band = (row or {}).get("band") or (asked or {}).get("quality")
        if asked:
            lines.append(f"{'Strong' if band == 'high' else 'Weak'} search: the vendor-master agent's question on "
                         f"{asked['at']:%A %H:%M}, \"{asked['query']}\" (match {band})")
        key = store.knowledge_upload(mem.patterns_dp, f"case-{vendor_id}-{mem.run_id}.md",
                                     self._case_file(lane, vendor_id).encode("utf-8"), conversation_id=conv)
        store.knowledge_ready(mem.patterns_dp, key, conv)
        lines.append("The auditor agent adds what was missing to Meko's knowledge base: the confirmed case file, with "
                     "the account, the device and the order of the attacker's steps")
        if not asked or row is None or band == "high":
            lane.learning = dict(quality=q)
            reason = ("no question about this case was asked" if not asked else
                      "Meko's search log does not list the question yet" if row is None else
                      "the question already found a strong match")
            return lines + [f"Nothing to replay: {reason}."]
        v = store.selflearning_verify(mem.patterns_dp, conv, row["id"])
        before, after = v.get("before") or {}, v.get("after") or {}
        score = {k: "none" if v.get("best_score") is None else f"{v['best_score']:.2f}"
                 for k, v in (("before", before), ("after", after))}
        lines.append(f"Meko replays the question: **{before.get('band')} → {after.get('band')}** (best match "
                     f"{score['before']} → {score['after']})")
        lane.learning = dict(quality=q, before=before, after=after, outcome=v.get("outcome"))
        return lines

    @staticmethod
    def _case_file(lane: Lane, vendor_id: str) -> str:
        """The confirmed case as one knowledge-base section, written from what the company's own systems logged."""
        v, req = lane.vendors[vendor_id], lane.requests[vendor_id]
        stop = next((d for d in lane.log if d["vendor_id"] == vendor_id and d["outcome"] != "allow"), None)
        sigs = sorted(stop["signals"] if stop else [], key=lambda s: s.at)
        iban, device, country = req.data["new_iban"], req.data["device"], req.data["country"]
        return "\n".join([
            f"# {CASE_TITLE} {v.name} ({vendor_id}), {req.ts:%B %Y}",
            f"Vendor-account takeover confirmed by the analyst on {lane.confirmed[vendor_id]:%A %d %B %H:%M}, by "
            f"calling the vendor on the phone number on file 60 days earlier: the vendor never asked to change its bank "
            f"details. [run {lane.mem.run_id}]",
            f"- Bank account in the request: {iban} ({COUNTRIES.get(iban[:2], iban[:2])}), account holder given as "
            f"{req.data['holder']}, entered through the {req.data['via']}",
            f"- Device of the portal session: {device}, from {COUNTRIES.get(country, country)}",
            "- What the agents saw, in order: " + "; ".join(f"{s.at:%a %d %b %H:%M} {s.type} ({s.detail.rstrip('.')})"
                                                            for s in sigs),
            f"- Signal types: [{', '.join(sorted(observable(s.type for s in sigs)))}]", ""])

    def summary(self) -> dict[str, dict]:
        out = {}
        for mode, lane in self.lanes.items():
            out[mode] = dict(lost=lane.lost(), first_action=dict(lane.first_action),
                             calls=len(lane.store.calls), promoted=sum(c["promoted"] for c in lane.candidates.values()))
        return out
