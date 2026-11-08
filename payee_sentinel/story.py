"""The demo story: two weeks of accounts payable at Hallberg Components, run twice side by side.

The same events reach two teams of agents with the same rules (`agents.py`). In one, each agent keeps its own memory
(`siloed`, as most companies run today); in the other they share it through Meko (`learning2`).

- Week 1: someone takes over Nordwind Logistik's supplier-portal account (Monday), asks for payments to go to a new
  IBAN (Tuesday), and the payment run is on Thursday.
- Week 2: the same crew tries Bergmann Verpackung.

Each chapter is one step on screen. The analyst is simulated with what a real one could do: call the vendor on the
number that was on file 60 days earlier. Everything is invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import random
import uuid

from .agents import (BLOCK_BANK_CHANGE_AT, HOLD_AT, STEP_UP_AT, Action, InboxAgent, Memory, PaymentAgent,
                     PortalAgent, Signal, SupportAgent, VendorMasterAgent, observable, risk_of)
from .context.local import LocalMeko
from .scenario import Event, Vendor, make_iban

LANES = {"siloed": "Agents in silos (today)", "learning2": "Agents sharing memory in Meko"}
KNOWLEDGE = Path(__file__).resolve().parent / "knowledge"
BUYER = "Hallberg Components GmbH"
AGENT_NAMES = {"portal": "Portal agent", "inbox": "Inbox agent", "support": "Support agent",
               "vendor_master": "Vendor-master agent", "payments": "Payments agent", "analyst": "Analyst (a person)",
               "auditor": "Auditor agent"}


def _vendors() -> dict[str, Vendor]:
    rng = random.Random(2026)
    spec = [("V001", "Nordwind Logistik GmbH", "DE", "nordwind-logistik.de", "+49 421 5530120", 40000),
            ("V002", "Bergmann Verpackung GmbH", "DE", "bergmann-verpackung.de", "+49 711 2208840", 28000),
            ("V003", "Van Dijk Elektro B.V.", "NL", "vandijk-elektro.nl", "+31 206 1704550", 11000),
            ("V004", "Bertoli Imballaggi S.r.l.", "IT", "bertoli-imballaggi.it", "+39 028 7219300", 7000),
            ("V005", "Dupont Emballages SAS", "FR", "dupont-emballages.fr", "+33 145 2017880", 19000),
            ("V006", "Kessler Antriebe GmbH", "DE", "kessler-antriebe.de", "+49 894 4129070", 8500)]
    return {vid: Vendor(vid, name, c, dom, f"accounts@{dom}", phone, make_iban(rng, c), name, [c],
                        [f"dev-{vid.lower()}a"], 14, mean, 30) for vid, name, c, dom, phone, mean in spec}


VENDORS = _vendors()
_rng = random.Random(7)
CREW = {  # the attackers' side of each attack (hidden truth)
    "V001": dict(device="dev-7f3a91", country="NG", email="finance.nordwind@gmail.com", phone="+49 152 7781934",
                 iban=make_iban(_rng, "LT")),
    "V002": dict(device="dev-c41e02", country="RU", email="bergmann.ap@outlook.com", phone="+49 157 3304128",
                 iban=make_iban(_rng, "LT")),
}
ATTACKER_IBANS = {c["iban"]: vid for vid, c in CREW.items()}


@dataclass
class Chapter:
    key: str
    when: datetime
    title: str
    story: str
    kind: str = "events"          # events | payments | audit | summary
    events: list[Event] = field(default_factory=list)
    review_after: bool = False


def _takeover(vid: str, day: datetime, n_fails: int) -> list[Event]:
    v, c = VENDORS[vid], CREW[vid]
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


def _bank_change(vid: str, t: datetime, callback_after: timedelta) -> list[Event]:
    v, c = VENDORS[vid], CREW[vid]
    body = (f"Dear Accounts Payable team,\n\nwe have updated our bank details in your supplier portal. From now on, "
            f"please pay all open invoices to:\nAccount holder: {v.name}\nIBAN: {c['iban']}\n"
            f"Please update this before Friday's payment run.\n\nBest regards,\nFinance Department\n{v.name}")
    return [Event(f"{vid}-M1", t, "inbox", "email", vid,
                  dict(sender=c["email"], reply_to=None, subject="Updated bank details", body=body)),
            Event(f"{vid}-B1", t + timedelta(minutes=2), "vendor_master", "bank_change_request", vid,
                  dict(via="portal", new_iban=c["iban"], holder=v.name, device=c["device"], country=c["country"])),
            Event(f"{vid}-B2", t + callback_after, "vendor_master", "callback", vid,
                  dict(phone=c["phone"], phone_source="master", result="confirmed"))]


def _routine(day: datetime) -> list[Event]:
    return [Event(f"R-{vid}-{day:%d}", day + timedelta(hours=9 + i, minutes=40), "portal", "login", vid,
                  dict(device=VENDORS[vid].devices[0], country=VENDORS[vid].country, success=True))
            for i, vid in enumerate(("V003", "V005", "V006"))]


def _invoice(vid: str, no: str, amount: float, due: datetime) -> Event:
    v = VENDORS[vid]
    return Event(f"I-{no}", due - timedelta(days=30), "invoices", "invoice", vid,
                 dict(invoice_no=no, amount=amount, currency="EUR", due=due.date().isoformat(), sender=v.email))


MON1, MON2 = datetime(2026, 11, 16), datetime(2026, 11, 23)
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


def _n(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _money(x: float) -> str:
    return f"€{x:,.2f}".replace(".00", "")


def _iban(iban: str) -> str:
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


class Lane:
    """One team of agents with its own memory store and its own view of the money."""

    def __init__(self, mode: str, store, run_id: str | None = None):
        self.mode, self.store = mode, store
        self.mem = Memory(store, mode, VENDORS, run_id)
        self.agents = {"portal": PortalAgent(self.mem), "support": SupportAgent(self.mem),
                       "inbox": InboxAgent(self.mem)}
        self.vm, self.pay = VendorMasterAgent(self.mem), PaymentAgent(self.mem)
        self.current = {vid: v.iban for vid, v in VENDORS.items()}
        self.changed_at: dict[str, datetime | None] = {vid: None for vid in VENDORS}
        self.paid_ibans = set(self.current.values())
        self.payments: list[dict] = []
        self.confirmed: dict[str, datetime] = {}
        self.first_action: dict[str, datetime] = {}
        self.candidates: dict[str, dict] = {}      # vendor -> pattern candidate waiting for a person
        self.decisions: dict[str, dict] = {}       # vendor -> the decision record behind the first protective action

    # ---------- helpers ----------
    def _decision(self, agent: str, vendor_id: str, at: datetime, what: str, risk: int, reasons: list[str],
                  rule: str) -> None:
        """Write the decision to Meko as a conversation turn with its reasoning (Meko shows it as a decision trace)."""
        seen = [s for _, vid, sigs in self.mem.reads if vid == vendor_id for s in sigs]
        by_agent = sorted({s.agent for s in seen})
        reasoning = (f"Risk {risk} from {len(set(s.type for s in seen))} signal types written by "
                     f"{', '.join(AGENT_NAMES.get(a, a) for a in by_agent) or 'no other agent'}; {rule}.")
        conv = self.mem.conversation(agent)
        self.store.add_message(self.mem.signals_dp, conv, agent, f"{VENDORS[vendor_id].name}: {what}", what,
                               reasoning=reasoning, plan=[f"search Meko for {vendor_id}'s signals",
                                                          "add up the weights", rule])
        self.mem.record_decision(agent, vendor_id, at, what, risk, rule, reasoning)
        self.decisions.setdefault(vendor_id, dict(agent=agent, at=at, what=what, risk=risk, reasons=reasons,
                                                  rule=rule, reasoning=reasoning))
        self.first_action.setdefault(vendor_id, at)

    def _signal_lines(self, sigs: list[Signal]) -> list[str]:
        lines = []
        for s in sigs:
            detail = s.detail
            for vid, cand in self.candidates.items():     # name the case a pattern came from, not its memory id
                for mid in cand["ids"]:
                    detail = detail.replace(f"pattern {mid[:8]}", f"pattern from the {VENDORS[vid].name} case")
            lines.append(f"`{s.type}` (+{s.weight}) — {detail}")
        return lines

    # ---------- chapters ----------
    def run_events(self, chapter: Chapter) -> Outcome:
        steps: list[Step] = []
        total_fails = {vid: sum(1 for e in chapter.events if e.vendor_id == vid and e.kind == "login"
                                and not e.data["success"]) for vid in VENDORS}
        first_fail = {}
        for e in chapter.events:
            if e.id.startswith("R-"):
                self.agents["portal"].on_event(e)        # routine logins: no step on screen
                continue
            w0, r0, a0, f0 = len(self.mem.written), len(self.mem.reads), len(self.vm.actions), len(self.mem.freezes)
            if e.channel in self.agents:
                self.agents[e.channel].on_event(e)
            else:
                self.vm.on_event(e)
            agent = e.channel if e.channel in self.agents else "vendor_master"
            title = _event_title(e)
            if e.kind == "login" and not e.data["success"]:
                first_fail.setdefault(e.vendor_id, e.ts)
                title = (f"{first_fail[e.vendor_id]:%a %H:%M} · {total_fails[e.vendor_id]} failed portal logins "
                         f"within a minute from {e.data['device']} ({e.data['country']})")
            step = Step(agent, title, self._signal_lines(self.mem.written[w0:]))
            own_reads = [(vid, sigs) for who, vid, sigs in self.mem.reads[r0:] if who == agent]
            if e.kind == "callback" and own_reads:
                vid, sigs = own_reads[-1]
                others = sorted({AGENT_NAMES[s.agent] for s in sigs if s.agent != agent})
                step.lines.append(f"Searched memory for {VENDORS[vid].name}: {_n(len(sigs), 'signal')} in 30 days"
                                  + (f", from {', '.join(others)}" if others else ", none from other agents"))
            for f in self.mem.freezes[f0:]:
                step.tone = "good"
                step.lines.append(f"**Matches an attack pattern a person approved: bank details frozen** "
                                  f"(risk {f.risk})")
                self._decision(f.agent, f.vendor_id, f.at, "bank details frozen by an approved pattern", f.risk,
                               f.reasons, "freeze when the vendor matches an approved attack pattern")
            for act in self.vm.actions[a0:]:
                step.tone = "good"
                step.lines.append(f"**Bank change blocked** — risk {act.risk} ≥ {BLOCK_BANK_CHANGE_AT}"
                                  if act.risk else "**Bank change blocked** — the vendor's bank details are frozen")
                self._decision("vendor_master", act.vendor_id, act.at, "bank change blocked", act.risk, act.reasons,
                               f"block a confirmed bank change at risk {BLOCK_BANK_CHANGE_AT} or more")
            applied = self.vm.applied.get(e.vendor_id)
            if e.kind == "callback" and applied and applied[1] == e.ts:
                self.current[e.vendor_id], self.changed_at[e.vendor_id] = applied
                risk, _ = risk_of(own_reads[-1][1]) if own_reads else (0, [])
                step.tone = "bad"
                step.lines.append(f"Callback confirmed, risk {risk} < {BLOCK_BANK_CHANGE_AT}: "
                                  f"**new bank details applied** ({_iban(applied[0])})")
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
            v = VENDORS[vid]
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
            steps.append(Step("analyst", f"{when:%a %H:%M} · calls {v.name} on {v.phone}, the number on file "
                                         f"60 days ago", [f"{v.name}: \"We never asked to change our bank "
                                                          f"details.\" **Attack confirmed**; bank details stay "
                                                          f"frozen.", f"The auditor agent writes the case up as a "
                                                          f"pattern candidate ({len(types)} signal types)."],
                              "good"))
        if not steps and not flagged:
            steps.append(Step("analyst", "No alert to review",
                              ["No agent saw enough on its own to raise an alert."], "neutral"))
        return steps

    def run_payments(self, chapter: Chapter) -> Outcome:
        run = chapter.when
        self.mem.new_session("payments")             # every payment run starts with no conversation history
        step = Step("payments", f"{run:%a %H:%M} · new session, {len(INVOICES[chapter.key])} payments due")
        for inv in INVOICES[chapter.key]:
            v = VENDORS[inv.vendor_id]
            iban = self.current[v.id]
            act = self.pay.decide(run, v, inv, iban, self.changed_at[v.id], self.paid_ibans)
            amount = inv.data["amount"]
            to_attacker = iban in ATTACKER_IBANS
            if act is None:
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
                self._decision("payments", v.id, run, f"payment {act.kind.replace('_', '-')}", act.risk,
                               act.reasons, rule)
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
        vid = "V001" if chapter.key.endswith("1") else "V002"
        if vid in self.decisions:
            d = self.decisions[vid]
            return f"{AGENT_NAMES[d['agent']]}: {d['what']} ({d['at']:%a %H:%M})"
        if self.current[vid] != VENDORS[vid].iban:
            return f"{VENDORS[vid].name}'s payments now go to the attacker's account"
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

    def __init__(self, make_store=LocalMeko, run_id: str | None = None):
        """`make_store` builds the Meko lane's store (real Meko or the simulator). The siloed lane always runs on the
        simulator: it stands for separate tools with no shared memory."""
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self.lanes = {"siloed": Lane("siloed", LocalMeko(), f"{self.run_id}-siloed"),
                      "learning2": Lane("learning2", make_store(), f"{self.run_id}-meko")}
        self.done: list[tuple[Chapter, dict[str, Outcome]]] = []
        meko = self.lanes["learning2"]
        for doc in sorted(KNOWLEDGE.glob("*.md")):     # the company's policy and playbook, in Meko's knowledge base
            meko.store.knowledge_upload(meko.mem.patterns_dp, doc.name, doc.read_bytes())

    @property
    def next_chapter(self) -> Chapter | None:
        return CHAPTERS[len(self.done)] if len(self.done) < len(CHAPTERS) else None

    def advance(self) -> None:
        ch = self.next_chapter
        if ch is None:
            return
        out = {}
        for mode, lane in self.lanes.items():
            if ch.kind == "events":
                out[mode] = lane.run_events(ch)
            elif ch.kind == "payments":
                out[mode] = lane.run_payments(ch)
            else:
                notes = self.explain(mode, "V001", ch.when) if ch.kind == "audit" else []
                if notes and mode == "learning2":
                    notes.append(self._save_report(lane, "V001", notes))
                out[mode] = Outcome(ch.key, [], "", notes=notes)
        self.done.append((ch, out))

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
        """Plain-language account of the first protective decision on a vendor. The auditor agent rebuilds it from
        memory: the decision record it finds in `sentinel-audit` and the signals in `sentinel-signals`."""
        lane = self.lanes[mode]
        v = VENDORS[vendor_id]
        found = lane.mem.decisions("auditor", vendor_id)
        if not found:
            per_agent: dict[str, list[Signal]] = {}
            for s in lane.mem.history("auditor", vendor_id, until):
                per_agent.setdefault(AGENT_NAMES[s.agent], []).append(s)
            lines = [f"No agent stopped anything for {v.name}. Each one saw only its own part:"]
            limits = {AGENT_NAMES["vendor_master"]: f"its limit to block a bank change is {BLOCK_BANK_CHANGE_AT}",
                      AGENT_NAMES["payments"]: f"its limit to hold a payment is {HOLD_AT}"}
            for name, ss in per_agent.items():
                risk, _ = risk_of(ss)
                lines.append(f"{name}: {_n(len(ss), 'signal')}, risk {risk} — "
                             + (limits.get(name) or "it decides nothing itself, and no other agent could read them"))
            return lines
        d = found[0]
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
        return lines

    @staticmethod
    def _save_report(lane: Lane, vendor_id: str, lines: list[str]) -> str:
        """The auditor keeps the case report as an artifact in `sentinel-audit` (the same bytes give the same hash)."""
        report = f"# Case report: {VENDORS[vendor_id].name}\n\n" + "\n".join(f"- {ln}" for ln in lines) + "\n"
        digest = lane.store.artifact_put(lane.mem.audit_dp, lane.mem.conversation("auditor"), "auditor",
                                         f"case-{vendor_id}-{lane.mem.run_id}.md", report.encode("utf-8"),
                                         "text/markdown")
        return f"Case report saved in Meko as an artifact (sha256 {digest[:12]}…)"

    def summary(self) -> dict[str, dict]:
        out = {}
        for mode, lane in self.lanes.items():
            out[mode] = dict(lost=lane.lost(), first_action=dict(lane.first_action),
                             calls=len(lane.store.calls), promoted=sum(c["promoted"] for c in lane.candidates.values()))
        return out
