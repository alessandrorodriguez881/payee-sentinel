"""Runs the agents over a synthetic world and counts what happened to the money.

The world (`scenario.py`) only holds what happens outside the buyer. This engine adds the buyer's side, in time
order: the agents read each event, the vendor master applies or blocks bank changes, and every Thursday the payment
agent releases, holds or double-checks each payment that is due.

A simulated analyst plays the person in the loop, with only what a real analyst could learn:
- a held payment or a blocked bank change is reviewed by calling the vendor on the number on file 60+ days ago,
  which tells a real request from an attack;
- a payment that went to an attacker is discovered 10 days later, when the real vendor chases its money.
Each confirmed attack becomes a pattern candidate in `sentinel-patterns` (the auditor writes it). In the learning modes
the analyst approves it and it is promoted to collective memory. In `learning2`, each case the analyst finds legitimate
is also written up, as a legitimate look-alike in `sentinel-legit`, and promoted.

    python -m payee_sentinel.evaluate --seeds 1-5
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import json
from pathlib import Path
import statistics

from .agents import (LEARNING_MODES, LOOKBACK, MODES, Action, InboxAgent, Memory, PaymentAgent, PortalAgent, Signal,
                     SupportAgent, VendorMasterAgent, observable)
from .context.base import approx_tokens
from .context.local import LocalMeko
from .scenario import START, Event, World, generate

DISCOVERY_DELAY = timedelta(days=10)
PAY_HORIZON = timedelta(days=7)          # a run pays what falls due in the next 7 days
RETRIEVALS = ("memory_search", "memory_get_all", "context_search", "knowledgebase_search")  # calls that fill context


@dataclass
class CaseResult:
    id: str
    kind: str
    vendor_id: str
    stopped: bool = False
    stage: str | None = None             # early | at_payment | contained (caught after a payment had gone out)
    first_action_at: str | None = None
    hours_before_target: float | None = None
    loss_eur: float = 0.0
    actions: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    seed: int
    mode: str
    attacks: list[CaseResult]
    payments: int = 0
    fp_holds: int = 0
    fp_step_ups: int = 0
    blocked_legit_changes: int = 0
    false_freezes: int = 0
    fake_invoices_held: int = 0          # the attacker's own invoice held: a true positive
    holds_on_attacked_vendors: int = 0   # real invoices of a vendor whose attack was already caught: counted apart
    patterns_promoted: int = 0
    legit_promoted: int = 0
    meko_calls: dict = field(default_factory=dict)
    retrieval_tokens: list[int] = field(default_factory=list)
    stuffing_tokens: list[int] = field(default_factory=list)

    @property
    def stopped(self) -> int:
        return sum(a.stopped for a in self.attacks)

    @property
    def early(self) -> int:
        return sum(a.stage == "early" for a in self.attacks)

    @property
    def loss(self) -> float:
        return round(sum(a.loss_eur for a in self.attacks), 2)


def _target_run(world: World, request_ts: datetime) -> datetime:
    return next((r for r in world.payment_runs if r > request_ts), world.payment_runs[-1] + timedelta(weeks=1))


def run(world: World, mode: str, store=None) -> RunResult:
    store = store or LocalMeko()
    mem = Memory(store, mode, world.vendors)
    portal, support, inbox = PortalAgent(mem), SupportAgent(mem), InboxAgent(mem)
    vm, pay = VendorMasterAgent(mem), PaymentAgent(mem)
    by_channel = {"portal": portal, "support": support, "inbox": inbox}

    attack_of = {e.id: e.attack_id for e in world.events if e.attack_id}
    attack_ibans = {a.new_iban: a for a in world.attacks}
    attack_vendors = {a.vendor_id: a for a in world.attacks}
    results = {a.id: CaseResult(a.id, a.kind, a.vendor_id) for a in world.attacks}
    targets = {}
    for a in world.attacks:
        req = next(e for e in world.events if e.attack_id == a.id and e.kind == "bank_change_request")
        targets[a.id] = _target_run(world, req.ts)
    out = RunResult(world.seed, mode, list(results.values()))

    current = {v.id: v.iban for v in world.vendors.values()}
    changed_at: dict[str, datetime | None] = {v.id: None for v in world.vendors.values()}
    paid_ibans = set(current.values())
    pending: list[Event] = []
    phishing: dict[str, datetime] = {}       # vendor -> when the inbox flagged a phishing bank-change email
    discoveries: list[tuple[datetime, str]] = []
    reviewed: set[str] = set()               # attack ids already confirmed by the analyst

    def protect(case_id: str, at: datetime, stage: str, label: str) -> None:
        r = results[case_id]
        r.actions.append(f"{at:%Y-%m-%d %H:%M} {label}")
        if r.first_action_at is None:
            r.first_action_at = at.isoformat()
            r.hours_before_target = round(max(0.0, (targets[case_id] - at).total_seconds() / 3600), 1)
        if not r.stopped:
            r.stopped, r.stage = True, stage

    def vendor_types(vendor_id: str, at: datetime) -> list[str]:
        """What the vendor showed in the 30 days before `at`, as the auditor finds it in shared memory, counting only
        the current case: signals up to the vendor's last resolution belong to a case already closed."""
        hits = store.memory_search(mem.signals_dp, mem.conversation("auditor"), "auditor",
                                   f"risk signals for vendor {vendor_id} {world.vendors[vendor_id].name}", limit=30)
        closed = max((datetime.fromisoformat(h.metadata["at"]) for h in hits
                      if h.metadata.get("kind") == "resolution" and h.metadata.get("vendor_id") == vendor_id
                      and datetime.fromisoformat(h.metadata["at"]) <= at), default=datetime.min)
        sigs = [s for h in hits if (s := Signal.from_hit(h)) and s.vendor_id == vendor_id
                and max(at - LOOKBACK, closed) < s.at <= at]
        return sorted(observable(s.type for s in sigs))

    def confirm_attack(vendor_id: str, at: datetime, case_id: str) -> None:
        """The analyst confirms an attack, restores the real bank account and freezes it; the auditor writes the case
        up as a pattern candidate."""
        if case_id in reviewed:
            return
        reviewed.add(case_id)
        current[vendor_id] = world.vendors[vendor_id].iban   # also after a loss found 10 days later
        types = vendor_types(vendor_id, at)
        mem.resolve(vendor_id, at, "attack confirmed by calling the vendor; real bank details restored.")
        mem.freezes.append(Action(at, "analyst", "freeze_vendor", vendor_id, 0, ["confirmed incident"], case_id))
        if len(types) < 2:
            return
        if mode == "learning2" and any(set(p.metadata["signal_types"]) == set(types)
                                       for p in mem.patterns("auditor")):
            return                                     # the same pattern is already in collective memory
        text = (f"Confirmed attack pattern on vendor {vendor_id}: these signals appeared within 30 days before an "
                f"attempt to redirect payments to a new bank account. Signal types: [{', '.join(types)}]")
        ids = store.memory_add(mem.patterns_dp, mem.conversation("auditor"), "auditor", text,
                               dict(kind="pattern", signal_types=types, learning_candidate=True, case=case_id))
        if mode in LEARNING_MODES:
            out.patterns_promoted += len(store.promote(mem.patterns_dp, ids))

    def learn_legit(vendor_id: str, at: datetime, what: str) -> None:
        """Learning v2: the analyst found a flagged case legitimate; the auditor writes it up and it is promoted."""
        if mode != "learning2":
            return
        types = vendor_types(vendor_id, at)
        if len(types) < 2 or any(set(p.metadata["signal_types"]) == set(types) for p in mem.legit_patterns("auditor")):
            return
        text = (f"Legitimate look-alike confirmed by the analyst on vendor {vendor_id} ({what}): these signals appeared "
                f"within 30 days before a change the vendor really made. Signal types: [{', '.join(types)}]")
        ids = store.memory_add(mem.legit_dp, mem.conversation("auditor"), "auditor", text,
                               dict(kind="legit_pattern", signal_types=types, learning_candidate=True))
        out.legit_promoted += len(store.promote(mem.legit_dp, ids))

    def under_attack(vendor_id: str, at: datetime) -> str | None:
        """An attack on this vendor that has started and is not stopped yet (hidden truth, used only to score)."""
        a = attack_vendors.get(vendor_id)
        if a and not results[a.id].stopped and any(x.ts <= at for x in world.events if x.attack_id == a.id):
            return a.id
        return None

    def apply_vm_outcome(e: Event, before_actions: int) -> None:
        for act in vm.actions[before_actions:]:
            case = attack_of.get(act.ref)
            if case:
                protect(case, act.at, "early", f"{act.kind} (risk {act.risk}: {', '.join(act.reasons[:3])})")
                confirm_attack(act.vendor_id, act.at, case)
            elif act.kind == "block_bank_change":
                out.blocked_legit_changes += 1
                req = next(x for x in world.events if x.id == act.ref)
                current[act.vendor_id] = req.data["new_iban"]          # the analyst clears it after a call
                changed_at[act.vendor_id] = act.at + timedelta(days=1)
                mem.freezes[:] = [f for f in mem.freezes if f.vendor_id != act.vendor_id]
                learn_legit(act.vendor_id, act.at, "a bank change that was blocked")
                mem.resolve(act.vendor_id, act.at, "bank change confirmed as legitimate by a call to the number "
                                                    "on file; applied.")
        if e.vendor_id in vm.applied and vm.applied[e.vendor_id][1] == e.ts:
            current[e.vendor_id], changed_at[e.vendor_id] = vm.applied[e.vendor_id]

    timeline = [(e.ts, 0, e) for e in world.events] + [(r, 1, r) for r in world.payment_runs]
    for ts, _, item in sorted(timeline, key=lambda x: (x[0], x[1])):
        while discoveries and discoveries[0][0] <= ts:
            at, case_id = discoveries.pop(0)
            confirm_attack(results[case_id].vendor_id, at, case_id)
        n_freezes = len(mem.freezes)
        if isinstance(item, Event):
            e = item
            if e.channel in by_channel:
                sigs = by_channel[e.channel].on_event(e)
                if e.channel == "inbox" and sigs:
                    types = {s.type for s in sigs}
                    if types & {"email.lookalike_domain", "email.freemail_sender"} and \
                            "email.bank_change_request" in types:
                        phishing[e.vendor_id] = e.ts
            elif e.channel == "vendor_master":
                before = len(vm.actions)
                if e.kind == "bank_change_request" and e.data["via"] == "email" and \
                        e.ts - phishing.get(e.vendor_id, datetime.min) <= timedelta(hours=1):
                    vm.rejected.add(e.id)
                    vm.actions.append(Action(e.ts, "inbox", "reject_bank_change", e.vendor_id, 0,
                                             ["phishing email"], e.id))
                vm.on_event(e)
                apply_vm_outcome(e, before)
            elif e.channel == "invoices" and datetime.fromisoformat(e.data["due"]) >= START:
                pending.append(e)
        else:
            run_at = item
            due_now = [i for i in pending if datetime.fromisoformat(i.data["due"]) <= run_at + PAY_HORIZON]
            for inv in due_now:
                pending.remove(inv)
                v = world.vendors[inv.vendor_id]
                iban = current[v.id]
                before = len(store.calls)
                act = pay.decide(run_at, v, inv, iban, changed_at[v.id], paid_ibans)
                searches = [c for c in store.calls[before:] if c.tool in RETRIEVALS]
                out.retrieval_tokens.append(sum(c.tokens_sent for c in searches))
                history = [asdict(x) for x in world.events_of(v.id) if run_at - LOOKBACK <= x.ts <= run_at]
                out.stuffing_tokens.append(approx_tokens(json.dumps(history, default=str)))
                out.payments += 1
                case = attack_ibans.get(iban)
                if act is None:
                    paid_ibans.add(iban)
                    if case:
                        results[case.id].loss_eur += inv.data["amount"]
                        results[case.id].actions.append(f"{run_at:%Y-%m-%d} PAID {inv.data['amount']:,.2f} to attacker")
                        discoveries.append((run_at + DISCOVERY_DELAY, case.id))
                        discoveries.sort()
                elif case:
                    protect(case.id, run_at, "at_payment", f"{act.kind} (risk {act.risk}: {', '.join(act.reasons[:3])})")
                    confirm_attack(v.id, run_at, case.id)
                    current[v.id] = v.iban                     # the analyst restores the real account
                elif active := under_attack(v.id, run_at):
                    # held while the takeover is still being prepared: the analyst's call uncovers it
                    protect(active, run_at, "early", f"{act.kind} on the real account (risk {act.risk}: "
                                                     f"{', '.join(act.reasons[:3])})")
                    confirm_attack(v.id, run_at, active)
                    paid_ibans.add(iban)
                elif inv.attack_id:
                    out.fake_invoices_held += 1                # the attacker's own invoice: held, never paid
                elif v.id in attack_vendors and any(x.ts <= run_at for x in world.events
                                                    if x.attack_id == attack_vendors[v.id].id):
                    out.holds_on_attacked_vendors += 1         # a real invoice after a caught attack: not a new alarm
                    paid_ibans.add(iban)
                    mem.resolve(v.id, run_at, f"payment {inv.data['invoice_no']} to the real account after the "
                                              f"confirmed attack; released.")
                else:
                    if act.kind == "hold":
                        out.fp_holds += 1
                    else:
                        out.fp_step_ups += 1
                    paid_ibans.add(iban)                       # released after the analyst's call
                    learn_legit(v.id, run_at, f"a payment that got a {act.kind.replace('_', '-')}")
                    mem.resolve(v.id, run_at, f"payment {inv.data['invoice_no']} confirmed as legitimate by a call "
                                              f"to the number on file; released.")
        for f in mem.freezes[n_freezes:]:
            case = attack_vendors.get(f.vendor_id)
            req_done = case and any(x.kind == "callback" for x in world.events
                                    if x.attack_id == case.id and x.ts <= f.at)
            if case and not req_done:
                protect(case.id, f.at, "early", f"freeze_vendor (pattern match, risk {f.risk})")
                confirm_attack(f.vendor_id, f.at, case.id)
            elif not case:
                out.false_freezes += 1                         # the analyst's call clears it
                mem.freezes.remove(f)
                learn_legit(f.vendor_id, f.at, "a freeze from a pattern match")
                mem.resolve(f.vendor_id, f.at, "pattern match reviewed: the vendor's activity is legitimate.")
    for r in results.values():
        if r.loss_eur > 0 and r.stopped:    # caught only after money had left: contained, not stopped (HYPOTHESES.md)
            r.stopped, r.stage = False, "contained"
    out.meko_calls = dict(Counter(c.tool for c in store.calls))
    return out


def summarize(results: list[RunResult]) -> dict:
    by_mode: dict[str, list[RunResult]] = {}
    for r in results:
        by_mode.setdefault(r.mode, []).append(r)
    table = {}
    for mode, rs in by_mode.items():
        n_att = sum(len(r.attacks) for r in rs)
        stopped = [a for r in rs for a in r.attacks if a.stopped]
        table[mode] = dict(
            worlds=len(rs), attacks=n_att, stopped=len(stopped), early=sum(a.stage == "early" for a in stopped),
            contained=sum(a.stage == "contained" for r in rs for a in r.attacks),
            loss_eur=round(sum(r.loss for r in rs), 2),
            fp_holds_per_world=round(statistics.mean(r.fp_holds for r in rs), 2),
            fp_step_ups_per_world=round(statistics.mean(r.fp_step_ups for r in rs), 2),
            blocked_legit_changes_per_world=round(statistics.mean(r.blocked_legit_changes for r in rs), 2),
            false_freezes_per_world=round(statistics.mean(r.false_freezes for r in rs), 2),
            fake_invoices_held=sum(r.fake_invoices_held for r in rs),
            holds_on_attacked_vendors_per_world=round(statistics.mean(r.holds_on_attacked_vendors for r in rs), 2),
            mean_hours_before_target=round(statistics.mean([a.hours_before_target or 0 for a in stopped]), 1)
            if stopped else None,
            retrieval_tokens_per_decision=round(statistics.mean(t for r in rs for t in r.retrieval_tokens), 1),
            stuffing_tokens_per_decision=round(statistics.mean(t for r in rs for t in r.stuffing_tokens), 1),
            stopped_by_kind=dict(Counter(a.kind for a in stopped)),
            missed_by_kind=dict(Counter(a.kind for r in rs for a in r.attacks if not a.stopped)),
        )
    return table


def _seeds(text: str) -> list[int]:
    out = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="1-5")
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    results = [run(generate(s), m) for s in _seeds(args.seeds) for m in args.modes.split(",")]
    table = summarize(results)
    for mode, row in table.items():
        print(mode.ljust(9), json.dumps(row))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(dict(summary=table, runs=[
            dict(asdict(r), stopped=r.stopped, early=r.early, loss=r.loss) for r in results]), indent=1, default=str))


if __name__ == "__main__":
    main()
