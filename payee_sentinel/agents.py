"""The agents of Payee Sentinel and the rules they decide with.

Each agent watches one channel and writes what looks risky as a *signal* in Meko (`memory_add` in the
`sentinel-signals` datapack). Before an agent does something that matters (apply a bank change, release a payment)
it searches Meko for the vendor's signals. How much it can see depends on the mode:

- `siloed`:   each agent reads only the signals it wrote itself (today's tools: one system per channel);
- `shared`:   every agent reads every agent's signals (Meko's shared memory);
- `learning`: `shared`, plus attack patterns that a person approved after earlier incidents (promoted to collective
              memory in `sentinel-patterns`). A match freezes the vendor's bank details before anyone asks to change
              them.
- `learning2`: `learning`, but it also remembers the legitimate look-alikes the analyst confirmed (promoted to
              collective memory in `sentinel-legit`). A pattern match freezes the vendor only if it shows something no
              confirmed legitimate case has shown. What it learns can only add a freeze, never remove a check.

Money decisions are rules in code (weights and thresholds below, fixed on 8-nov-2026 before any evaluation run, see
`docs/HYPOTHESES.md`). A model may read emails and write explanations, never decide a payment.

Assumption stated in the README: the vendor master keeps only the current contact values. The history of who
changed a phone number, and from where, lives in the portal and the support line.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re

from .context.base import ContextStore, Hit
from .scenario import Event, Vendor

# ---------- rules (fixed before evaluating) ----------
WEIGHTS = {
    "portal.failed_login_burst": 1,
    "portal.new_device": 1,
    "portal.new_device_foreign": 2,
    "portal.reset_new_device": 2,
    "portal.contact_change": 1,
    "portal.contact_change_new_device": 3,
    "support.contact_change_weak_check": 2,
    "contact.phone_changed": 1,
    "email.lookalike_domain": 3,
    "email.freemail_sender": 2,
    "email.reply_to_mismatch": 2,
    "email.bank_change_request": 1,
    "email.urgency": 1,
    "email.new_phone_in_body": 1,
    "email.open_items_request": 1,
    "bank.foreign_iban": 2,
    "bank.holder_mismatch": 2,
    "bank.callback_to_unverified_number": 3,
    "bank.callback_to_recently_changed_phone": 4,
    "bank.callback_denied": 6,
    "bank.vop_no_match": 6,           # round 4: Verification of Payee, fixed on 10-nov before any run
    "bank.vop_close_match": 2,
    "consortium.known_infrastructure": 6,   # round 5, fixed on 10-nov before any run
    "pattern.match": 4,
    "pay.recent_bank_change": 1,
    "pay.first_payment_new_iban": 1,
    "pay.invoice_sender_mismatch": 2,
    "pay.amount_unusual": 1,
}
LOOKBACK = timedelta(days=30)
NEW_DEVICE_WINDOW = timedelta(hours=72)
BLOCK_BANK_CHANGE_AT = 6       # vendor master: block a confirmed change at this risk or more
HOLD_AT = 7                    # payments: hold for a person at this risk or more
STEP_UP_AT = 4                 # payments: call the phone on file 60+ days ago, if the bank details changed recently
PATTERN_MIN_SHARED = 3         # a pattern matches when 3+ of its signal types appear and cover 60 % of it
PATTERN_MIN_COVER = 0.6
FREEMAIL = ("gmail.com", "outlook.com", "yahoo.com", "gmx.net", "proton.me")
BANK_WORDS = re.compile(r"bank details|IBAN|account details|new account", re.I)
URGENT_WORDS = re.compile(r"urgent|as soon as possible|today if possible|before friday|being closed", re.I)
RECON_WORDS = re.compile(r"list of (all )?open invoices|next payment date", re.I)
PHONE_IN_TEXT = re.compile(r"\+\d{2} \d{3} \d{7}")
PATTERN_TYPES = re.compile(r"Signal types: \[([^\]]+)\]")
MODES = ("siloed", "shared", "learning", "learning2")
LEARNING_MODES = ("learning", "learning2")
OWN_REACTION = ("pay.", "pattern.")    # signal types that describe our own reaction, not what the vendor showed


def observable(types) -> set[str]:
    return {t for t in types if not t.startswith(OWN_REACTION)}


@dataclass
class Signal:
    type: str
    vendor_id: str
    at: datetime
    agent: str
    evidence: list[str]
    detail: str
    extra: dict = field(default_factory=dict)
    memory_id: str = ""            # set when the signal was read back from Meko

    @property
    def weight(self) -> int:
        return WEIGHTS[self.type]

    def text(self, vendor: Vendor) -> str:
        return (f"Risk signal {self.type} for vendor {self.vendor_id} {vendor.name} at {self.at:%Y-%m-%d %H:%M}, "
                f"weight {self.weight}, seen by the {self.agent} agent. {self.detail} "
                f"Evidence: {', '.join(self.evidence)}.")

    def metadata(self) -> dict:
        return dict(kind="signal", type=self.type, vendor_id=self.vendor_id, at=self.at.isoformat(),
                    weight=self.weight, agent=self.agent, evidence=self.evidence, detail=self.detail, **self.extra)

    @classmethod
    def from_hit(cls, hit: Hit) -> "Signal | None":
        m = hit.metadata
        if m.get("kind") != "signal":
            return None
        extra = {k: v for k, v in m.items() if k not in ("kind", "type", "vendor_id", "at", "weight", "agent",
                                                          "evidence", "agent_id", "detail", "run",
                                                          "meko_datapack_id")}
        return cls(m["type"], m["vendor_id"], datetime.fromisoformat(m["at"]), m["agent"], list(m["evidence"]),
                   m.get("detail", hit.text), extra, hit.id)


@dataclass
class Action:
    """Something an agent did that protects money (or slows a legitimate vendor)."""
    at: datetime
    agent: str
    kind: str              # reject_bank_change | block_bank_change | freeze_vendor | hold | step_up
    vendor_id: str
    risk: int
    reasons: list[str]
    ref: str = ""          # event or invoice the action is about


class Memory:
    """What the agents share through Meko, seen through the mode of the run."""

    def __init__(self, store: ContextStore, mode: str, vendors: dict[str, Vendor], run_id: str | None = None):
        assert mode in MODES
        self.store, self.mode, self.vendors = store, mode, vendors
        self.run_id = run_id    # on real Meko, keeps one demo run from reading the signals of earlier runs
        self.signals_dp = store.ensure_datapack("sentinel-signals", "Raw risk signals from every channel agent")
        self.patterns_dp = store.ensure_datapack("sentinel-patterns", "Confirmed attack patterns and playbooks")
        self.legit_dp = store.ensure_datapack("sentinel-legit", "Legitimate look-alikes confirmed by the analyst")
        self.audit_dp = store.ensure_datapack("sentinel-audit", "Audit reports and case files")
        self._conv: dict[str, str] = {}
        self.freezes: list[Action] = []   # vendors whose bank details a pattern match froze
        self.written: list[Signal] = []   # what this run wrote and read, for the demo screen
        self.reads: list[tuple[str, str, list[Signal]]] = []

    def frozen(self, vendor_id: str) -> bool:
        return any(a.vendor_id == vendor_id for a in self.freezes)

    def conversation(self, agent: str) -> str:
        if agent not in self._conv:
            self._conv[agent] = self.store.conversation_create(self.signals_dp, agent, f"{agent} agent session")
        return self._conv[agent]

    def new_session(self, agent: str) -> str:
        """A fresh conversation: the agent starts with no history, only what it finds in memory."""
        self._conv.pop(agent, None)
        return self.conversation(agent)

    def _tag(self, meta: dict) -> dict:
        return dict(meta, run=self.run_id) if self.run_id else meta

    def _ours(self, hit: Hit) -> bool:
        return self.run_id is None or hit.metadata.get("run") == self.run_id

    def _run_words(self) -> str:
        """Demo runs on real Meko share datapacks with earlier runs; the run id in the text and the query keeps a
        run's own memories at the top of the ranking (the metadata filter alone works only on what comes back)."""
        return f" Run {self.run_id}." if self.run_id else ""

    def write(self, sig: Signal) -> None:
        self.store.memory_add(self.signals_dp, self.conversation(sig.agent), sig.agent,
                              sig.text(self.vendors[sig.vendor_id]) + self._run_words(), self._tag(sig.metadata()))
        self.written.append(sig)

    def resolve(self, vendor_id: str, at: datetime, verdict: str) -> None:
        """A person reviewed the vendor (attack confirmed or activity found legitimate). Signals up to now are
        closed: they stay in memory for the audit, but no longer count towards new decisions."""
        v = self.vendors[vendor_id]
        self.store.memory_add(self.signals_dp, self.conversation("analyst"), "analyst",
                              f"Risk signals for vendor {vendor_id} {v.name} reviewed by the analyst at "
                              f"{at:%Y-%m-%d %H:%M}: {verdict} Earlier signals are closed." + self._run_words(),
                              self._tag(dict(kind="resolution", vendor_id=vendor_id, at=at.isoformat(),
                                             verdict=verdict)))

    def query(self, vendor_id: str) -> str:
        """The search an agent runs for a vendor's signals; each decision's trace keeps it."""
        v = self.vendors[vendor_id]
        return f"risk signals for vendor {vendor_id} {v.name}" + (f" run {self.run_id}" if self.run_id else "")

    def _vendor_hits(self, agent: str, vendor_id: str) -> list[Hit]:
        limit = 50 if self.run_id else 30     # demo runs share datapacks with earlier rehearsals
        return [h for h in self.store.memory_search(self.signals_dp, self.conversation(agent), agent,
                                                    self.query(vendor_id), limit=limit) if self._ours(h)]

    def history(self, agent: str, vendor_id: str, until: datetime) -> list[Signal]:
        """Every signal about the vendor up to `until`, closed cases included: what an auditor reads."""
        sigs = [s for h in self._vendor_hits(agent, vendor_id) if (s := Signal.from_hit(h))
                and s.vendor_id == vendor_id and s.at <= until]
        return sorted(sigs, key=lambda s: s.at)

    def record_decision(self, agent: str, vendor_id: str, at: datetime, what: str, risk: int, rule: str,
                        reasoning: str, outcome: str = "block", memory_ids: list[str] | None = None) -> None:
        """Keep the decision itself as a memory in `sentinel-audit`, so an auditor can find it later, with the ids
        of the signal memories it was based on."""
        v = self.vendors[vendor_id]
        self.store.memory_add(self.audit_dp, self.conversation(agent), agent,
                              f"Decision on vendor {vendor_id} {v.name} by the {agent} agent at {at:%Y-%m-%d %H:%M}: "
                              f"{what}. Risk {risk}; rule: {rule}. {reasoning}" + self._run_words(),
                              self._tag(dict(kind="decision", vendor_id=vendor_id, at=at.isoformat(), agent=agent,
                                             what=what, outcome=outcome, risk=risk, rule=rule, reasoning=reasoning,
                                             memory_ids=memory_ids or [])))

    def decisions(self, agent: str, vendor_id: str) -> list[dict]:
        v = self.vendors[vendor_id]
        query = f"decision on vendor {vendor_id} {v.name}" + (f" run {self.run_id}" if self.run_id else "")
        hits = self.store.memory_search(self.audit_dp, self.conversation(agent), agent, query, limit=20)
        found = [h.metadata for h in hits if self._ours(h) and h.metadata.get("kind") == "decision"
                 and h.metadata.get("vendor_id") == vendor_id]
        return sorted(found, key=lambda d: d["at"])

    def signals(self, agent: str, vendor_id: str, now: datetime) -> list[Signal]:
        hits = self._vendor_hits(agent, vendor_id)
        closed = max((datetime.fromisoformat(h.metadata["at"]) for h in hits
                      if h.metadata.get("kind") == "resolution" and h.metadata.get("vendor_id") == vendor_id
                      and datetime.fromisoformat(h.metadata["at"]) <= now), default=datetime.min)
        out = []
        for h in hits:
            s = Signal.from_hit(h)
            if s is None or s.vendor_id != vendor_id or not (max(now - LOOKBACK, closed) < s.at <= now):
                continue
            if self.mode == "siloed" and s.agent != agent:
                continue
            out.append(s)
        self.reads.append((agent, vendor_id, out))
        return out

    def _promoted_patterns(self, datapack_id: str, agent: str, kind: str) -> list[Hit]:
        """Promotion drops the metadata, so the signal types are read back from the text (`Signal types: [a, b]`).
        With a run id (demo runs on real Meko), only patterns tagged `[run <id>]` count, so earlier runs' collective
        memory does not change the story."""
        out = []
        for h in self.store.collective(datapack_id, self.conversation(agent), agent):
            if self.run_id and f"[run {self.run_id}]" not in h.text:
                continue
            m = PATTERN_TYPES.search(h.text)
            if m:
                out.append(Hit(h.id, h.text, h.score, dict(h.metadata, kind=kind,
                                                            signal_types=[t.strip() for t in m[1].split(",")]),
                               h.source))
        return out

    def patterns(self, agent: str) -> list[Hit]:
        """Attack patterns a person approved: the collective memory of `sentinel-patterns`."""
        return self._promoted_patterns(self.patterns_dp, agent, "pattern") if self.mode in LEARNING_MODES else []

    def legit_patterns(self, agent: str) -> list[Hit]:
        """Legitimate look-alikes a person confirmed (learning v2 only): the collective memory of `sentinel-legit`."""
        return self._promoted_patterns(self.legit_dp, agent, "legit") if self.mode == "learning2" else []

    def learned_match(self, types: set[str], agent: str, patterns: list[Hit] | None = None) -> Hit | None:
        """The approved attack pattern the vendor's signals match, if any. In learning v2 a match does not count when
        everything the vendor shows was also shown by a legitimate case the analyst confirmed."""
        hit = pattern_match(types, self.patterns(agent) if patterns is None else patterns)
        if hit and self.mode == "learning2":
            seen = observable(types)
            if any(seen <= set(lp.metadata["signal_types"]) for lp in self.legit_patterns(agent)):
                return None
        return hit


def risk_of(signals: list[Signal]) -> tuple[int, list[str]]:
    """Sum of weights, counting each signal type once (repeats of the same thing do not add up)."""
    best: dict[str, Signal] = {}
    for s in signals:
        best.setdefault(s.type, s)
    reasons = sorted(best, key=lambda t: -WEIGHTS[t])
    return sum(WEIGHTS[t] for t in reasons), reasons


def pattern_match(types: set[str], patterns: list[Hit]) -> Hit | None:
    for p in patterns:
        ptypes = set(p.metadata.get("signal_types", []))
        shared = types & ptypes
        if ptypes and len(shared) >= PATTERN_MIN_SHARED and len(shared) / len(ptypes) >= PATTERN_MIN_COVER:
            return p
    return None


def _domain(addr: str | None) -> str:
    return (addr or "").rpartition("@")[2].lower()


def _similar(a: str, b: str) -> bool:
    """Look-alike domain: different, but close by edit distance or one contains the other's name."""
    if a == b:
        return False
    na, nb = a.rpartition(".")[0], b.rpartition(".")[0]
    if na == nb or na.startswith(nb) or nb.startswith(na):
        return True
    prev = list(range(len(nb) + 1))
    for i, ca in enumerate(na, 1):
        cur = [i]
        for j, cb in enumerate(nb, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] <= 2


# ---------- channel agents ----------

class Agent:
    name = "agent"

    def __init__(self, memory: Memory):
        self.mem = memory
        self.actions: list[Action] = []

    def emit(self, e: Event, type_: str, detail: str, **extra) -> Signal:
        sig = Signal(type_, e.vendor_id, e.ts, self.name, [e.id], detail, extra)
        self.mem.write(sig)
        if self.mem.mode in LEARNING_MODES:
            self._check_patterns(e)
        return sig

    def _check_patterns(self, e: Event) -> None:
        """Learning modes: after writing a signal, compare the vendor's signals with approved attack patterns.
        A match freezes the vendor's bank details until a person clears them."""
        if self.mem.frozen(e.vendor_id):
            return
        patterns = self.mem.patterns(self.name)
        if not patterns:                 # nothing approved yet: skip the (slow, on real Meko) signal search
            return
        sigs = self.mem.signals(self.name, e.vendor_id, e.ts)
        hit = self.mem.learned_match({s.type for s in sigs}, self.name, patterns)
        if hit:
            risk, reasons = risk_of(sigs)
            sig = Signal("pattern.match", e.vendor_id, e.ts, self.name, [e.id],
                         f"The vendor's signals match approved attack pattern {hit.id[:8]}; bank details frozen.",
                         dict(pattern_id=hit.id))
            self.mem.write(sig)
            self.mem.freezes.append(Action(e.ts, self.name, "freeze_vendor", e.vendor_id,
                                           risk + WEIGHTS["pattern.match"], ["pattern.match"] + reasons, hit.id))


class PortalAgent(Agent):
    name = "portal"

    def __init__(self, memory: Memory):
        super().__init__(memory)
        self.first_seen: dict[tuple[str, str], datetime] = {}   # (vendor, device) -> first activity
        self.logged_in: set[tuple[str, str]] = set()
        self.fails: dict[str, list[datetime]] = {}
        for v in memory.vendors.values():
            for d in v.devices:
                self.first_seen[(v.id, d)] = datetime.min
                self.logged_in.add((v.id, d))

    def on_event(self, e: Event) -> None:
        v = self.mem.vendors[e.vendor_id]
        dev, country = e.data.get("device"), e.data.get("country")
        key = (e.vendor_id, dev)
        first = self.first_seen.setdefault(key, e.ts)
        new = e.ts - first <= NEW_DEVICE_WINDOW
        if e.kind == "login" and not e.data["success"]:
            recent = [t for t in self.fails.get(e.vendor_id, []) if e.ts - t <= timedelta(hours=1)] + [e.ts]
            self.fails[e.vendor_id] = recent
            if len(recent) == 5:
                self.emit(e, "portal.failed_login_burst", f"{len(recent)} failed portal logins within an hour.")
            return
        if e.kind == "login" and key not in self.logged_in:
            self.logged_in.add(key)
            if country not in v.usual_countries:
                self.emit(e, "portal.new_device_foreign", f"Portal login from new device {dev} in {country}.",
                          country=country)
            else:
                self.emit(e, "portal.new_device", f"Portal login from new device {dev}.")
        elif e.kind == "password_reset" and new:
            self.emit(e, "portal.reset_new_device", f"Portal password reset from new device {dev} in {country}.")
        elif e.kind == "contact_change":
            field_, newval = e.data["field"], e.data["new"]
            if new:
                self.emit(e, "portal.contact_change_new_device",
                          f"Contact {field_} changed to {newval} from a device first seen in the last 72 hours.")
            else:
                self.emit(e, "portal.contact_change", f"Contact {field_} changed to {newval}.")
            if field_ == "phone":
                self.emit(e, "contact.phone_changed", f"Phone number on file changed to {newval} via the portal.",
                          new_phone=newval)


class SupportAgent(Agent):
    name = "support"

    def on_event(self, e: Event) -> None:
        if e.kind == "call" and e.data["request"] in ("update_phone", "update_email"):
            self.emit(e, "support.contact_change_weak_check",
                      f"Caller {e.data['caller']} changed the {e.data['request'][7:]} on file; identity checked only "
                      f"with an invoice number.")
            if e.data["request"] == "update_phone":
                self.emit(e, "contact.phone_changed", f"Phone number on file changed to {e.data['new']} by phone.",
                          new_phone=e.data["new"])


class InboxAgent(Agent):
    name = "inbox"

    def on_event(self, e: Event) -> list[Signal]:
        v = self.mem.vendors[e.vendor_id]
        d = e.data
        sender, reply = _domain(d["sender"]), _domain(d.get("reply_to"))
        body = d["subject"] + "\n" + d["body"]
        out = []
        if sender != v.domain and _similar(sender, v.domain):
            out.append(self.emit(e, "email.lookalike_domain", f"Email from {sender}, which imitates {v.domain}."))
        elif sender in FREEMAIL:
            out.append(self.emit(e, "email.freemail_sender", f"Email claiming to be {v.name} from {sender}."))
        if reply and reply != sender:
            out.append(self.emit(e, "email.reply_to_mismatch", f"Replies would go to {reply}, not {sender}."))
        bank = BANK_WORDS.search(body)
        if bank:
            out.append(self.emit(e, "email.bank_change_request", "Email asks to pay to new bank details."))
            if URGENT_WORDS.search(body):
                out.append(self.emit(e, "email.urgency", "The bank-change request pushes for speed."))
            for phone in PHONE_IN_TEXT.findall(body):
                if phone != v.phone:
                    out.append(self.emit(e, "email.new_phone_in_body", f"Email gives a new phone number {phone}.",
                                         phone=phone))
        if RECON_WORDS.search(body):
            out.append(self.emit(e, "email.open_items_request", "Email asks for the list of open invoices."))
        return out


class VendorMasterAgent(Agent):
    name = "vendor_master"

    def __init__(self, memory: Memory):
        super().__init__(memory)
        self.pending: dict[str, Event] = {}        # vendor -> request waiting for its callback
        self.rejected: set[str] = set()            # request event ids rejected (e.g. phishing email)
        self.applied: dict[str, tuple[str, datetime]] = {}   # vendor -> (iban, applied at)

    def on_event(self, e: Event) -> None:
        v = self.mem.vendors[e.vendor_id]
        if e.kind == "bank_change_request":
            self.pending[e.vendor_id] = e
            if not e.data["new_iban"].startswith(v.country):
                self.emit(e, "bank.foreign_iban", f"New IBAN is in {e.data['new_iban'][:2]}, vendor is in {v.country}.")
            if e.data["holder"] != v.name:
                self.emit(e, "bank.holder_mismatch", f"Account holder '{e.data['holder']}' is not '{v.name}'.")
            return
        req = self.pending.pop(e.vendor_id, None)   # a callback
        if req is None or req.id in self.rejected:
            return
        if e.data["result"] == "denied":
            self.emit(e, "bank.callback_denied", "The vendor denied asking for the change when called back.")
            self.actions.append(Action(e.ts, self.name, "reject_bank_change", v.id, WEIGHTS["bank.callback_denied"],
                                       ["bank.callback_denied"], req.id))
            return
        if e.data["phone_source"] not in ("master", "none", "policy"):   # "none": skipped; "policy": on file 60 days ago
            self.emit(e, "bank.callback_to_unverified_number",
                      f"The callback went to {e.data['phone']}, a number taken from the request itself.")
        changed = [s for s in self.mem.signals(self.name, v.id, e.ts)
                   if s.type == "contact.phone_changed" and s.extra.get("new_phone") == e.data["phone"]]
        if changed:
            self.emit(e, "bank.callback_to_recently_changed_phone",
                      f"The callback went to {e.data['phone']}, which was put on file {changed[0].at:%d %b}.")
        if self.mem.frozen(v.id):
            self.actions.append(Action(e.ts, self.name, "block_bank_change", v.id, 0, ["vendor frozen by pattern"],
                                       req.id))
            return
        risk, reasons = risk_of(self.mem.signals(self.name, v.id, e.ts))
        if risk >= BLOCK_BANK_CHANGE_AT:
            self.actions.append(Action(e.ts, self.name, "block_bank_change", v.id, risk, reasons, req.id))
            return
        self.applied[v.id] = (req.data["new_iban"], e.ts)


class PaymentAgent(Agent):
    name = "payments"
    last_risk: tuple[int, list[str]] = (0, [])     # risk and reasons behind the latest decision, a payment included

    def decide(self, run: datetime, v: Vendor, invoice: Event, iban: str, changed_at: datetime | None,
               paid_ibans: set[str]) -> Action | None:
        own = []
        if changed_at and run - changed_at <= LOOKBACK:
            own.append(Signal("pay.recent_bank_change", v.id, run, self.name, [invoice.id],
                              f"Bank details changed on {changed_at:%d %b}."))
        if iban not in paid_ibans:
            own.append(Signal("pay.first_payment_new_iban", v.id, run, self.name, [invoice.id],
                              f"First payment ever to {iban}."))
        if _domain(invoice.data.get("sender")) != v.domain:
            own.append(Signal("pay.invoice_sender_mismatch", v.id, run, self.name, [invoice.id],
                              f"Invoice came from {invoice.data.get('sender')}."))
        if invoice.data["amount"] > 2 * v.mean_amount:
            own.append(Signal("pay.amount_unusual", v.id, run, self.name, [invoice.id],
                              f"Amount {invoice.data['amount']:,.2f} EUR is over twice the usual."))
        for s in own:
            self.mem.write(s)
        sigs = self.mem.signals(self.name, v.id, run)
        types = {s.type for s in sigs}
        if (self.mem.mode in LEARNING_MODES and "pattern.match" not in types
                and (hit := self.mem.learned_match(types, self.name))):
            own.append(Signal("pattern.match", v.id, run, self.name, [invoice.id],
                              f"The vendor's signals match approved attack pattern {hit.id[:8]}.",
                              dict(pattern_id=hit.id)))
            self.mem.write(own[-1])
        risk, reasons = risk_of(sigs + own)
        self.last_risk = (risk, reasons)
        ref = invoice.data["invoice_no"]
        if risk >= HOLD_AT:
            return Action(run, self.name, "hold", v.id, risk, reasons, ref)
        if risk >= STEP_UP_AT and changed_at is not None:
            return Action(run, self.name, "step_up", v.id, risk, reasons, ref)
        return None
