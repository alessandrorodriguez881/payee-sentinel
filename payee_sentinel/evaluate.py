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
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import statistics

from .agents import (LEARNING_MODES, LOOKBACK, MODES, Action, InboxAgent, Memory, PaymentAgent, PortalAgent, Signal,
                     SupportAgent, VendorMasterAgent, observable)
from .context.base import approx_tokens
from .context.local import LocalMeko
from .scenario import START, Event, World, generate, infrastructure, reuse_infrastructure

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
    request_at: str | None = None        # when the bank-change request arrived


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


def run(world: World, mode: str, store=None, best_practices: bool = False, compliance: float = 0.7,
        known: set | None = None) -> RunResult:
    """With `best_practices` (round 4), every lane gets Verification of Payee on bank-change requests and a callback
    that follows the payment policy (the number on file 60 days earlier) with probability `compliance`."""
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
        results[a.id].request_at = req.ts.isoformat()
    out = RunResult(world.seed, mode + ("+bp" if best_practices else ""), list(results.values()))

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
        if known is not None:                          # round 5: what an analyst confirmed joins the shared list
            case = next(a for a in world.attacks if a.id == case_id)
            known.update(v for v in infrastructure(world, case).values() if v)
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

    flagged: set[tuple[str, str]] = set()

    def check_known(e: Event, agent) -> None:
        """Round 5: a value in the event that is on the shared list of confirmed attacker infrastructure."""
        if not known:
            return
        text = " ".join(str(v) for v in e.data.values())
        for value in sorted(known):
            if value in text and (e.vendor_id, value) not in flagged:
                flagged.add((e.vendor_id, value))
                agent.emit(e, "consortium.known_infrastructure",
                           f"{value} was used in an attack confirmed earlier (the shared list).")

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
                check_known(e, by_channel[e.channel])
                if e.channel == "inbox" and sigs:
                    types = {s.type for s in sigs}
                    if types & {"email.lookalike_domain", "email.freemail_sender"} and \
                            "email.bank_change_request" in types:
                        phishing[e.vendor_id] = e.ts
            elif e.channel == "vendor_master":
                before = len(vm.actions)
                if best_practices and e.kind == "callback" and world.policy_draw.get(e.id, 1.0) < compliance:
                    # the clerk follows the policy: the number on file 60 days earlier reaches the real vendor
                    e = replace(e, data={**e.data, "phone": world.vendors[e.vendor_id].phone, "phone_source": "policy",
                                         "result": "denied" if e.attack_id else "confirmed"})
                if e.kind == "bank_change_request" and e.data["via"] == "email" and \
                        e.ts - phishing.get(e.vendor_id, datetime.min) <= timedelta(hours=1):
                    vm.rejected.add(e.id)
                    vm.actions.append(Action(e.ts, "inbox", "reject_bank_change", e.vendor_id, 0,
                                             ["phishing email"], e.id))
                check_known(e, vm)
                vm.on_event(e)
                if best_practices and e.kind == "bank_change_request" and e.id not in vm.rejected:
                    outcome = world.vop.get(e.id)
                    if outcome == "no_match":
                        vm.emit(e, "bank.vop_no_match", "Verification of Payee: the account holder does not match "
                                                          "the vendor's name.")
                    elif outcome == "close_match":
                        vm.emit(e, "bank.vop_close_match", "Verification of Payee: the account holder's name only "
                                                             "nearly matches the vendor's.")
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


def carry_learning(store: LocalMeko) -> LocalMeko:
    """What a company keeps from one period to the next (round 3): a fresh simulator holding only the collective
    memory of `store`, i.e. the promoted attack patterns and legitimate look-alikes, in the order they were promoted.
    Signals, open cases and decisions stay behind."""
    out = LocalMeko()
    names = {dp_id: name for name, dp_id in store.datapacks.items()}
    for m in store.memories.values():
        if m["state"] == "promoted":
            out.memories[m["id"]] = dict(m, datapack=out.ensure_datapack(names[m["datapack"]]))
    out.calls.clear()
    return out


def run_chain(seeds: list[int], mode: str, best_practices: bool = False, compliance: float = 0.7) -> list[RunResult]:
    """Consecutive six-week periods, one world per seed, that share only their collective memory (round 3)."""
    store, out = LocalMeko(), []
    for seed in seeds:
        out.append(run(generate(seed), mode, store, best_practices, compliance))
        store = carry_learning(store)
    return out


def run_consortium(seeds: list[int], lane: str, reuse: float, compliance: float = 0.7) -> list[RunResult]:
    """Round 5: companies one after the other, all with best practices and Meko inside each company. `alone`: nothing
    shared between them; `blacklist`: a shared list of confirmed accounts, phones and devices; `consortium`: that list
    plus the collective memory people approved in any company. The attackers reuse earlier infrastructure the same way
    in every lane."""
    known = set() if lane in ("blacklist", "consortium") else None
    earlier, store, out = [], LocalMeko(), []
    for seed in seeds:
        world = reuse_infrastructure(generate(seed), earlier, reuse)
        result = run(world, "learning2", store if lane == "consortium" else LocalMeko(), True, compliance, known)
        result.mode = lane
        out.append(result)
        earlier += [infrastructure(world, a) for a in world.attacks]
        if lane == "consortium":
            store = carry_learning(store)
    return out


def summarize_periods(chains: dict[str, list[list[RunResult]]]) -> dict:
    """Round 3, per mode and period: means over chains. Frictions as in H6 (false-positive holds + legitimate bank
    changes blocked + wrong freezes); collective memory counted cumulatively, as it is carried."""
    table = {}
    for mode, runs in chains.items():
        rows = []
        for p in range(len(runs[0])):
            rs = [chain[p] for chain in runs]
            stopped = [a for r in rs for a in r.attacks if a.stopped]
            rows.append(dict(
                period=p + 1, attacks=sum(len(r.attacks) for r in rs), stopped=len(stopped),
                early=sum(a.stage == "early" for a in stopped),
                frictions=round(statistics.mean(r.fp_holds + r.blocked_legit_changes + r.false_freezes for r in rs), 2),
                false_freezes=round(statistics.mean(r.false_freezes for r in rs), 2),
                fp_holds=round(statistics.mean(r.fp_holds for r in rs), 2),
                blocked_legit_changes=round(statistics.mean(r.blocked_legit_changes for r in rs), 2),
                fp_step_ups=round(statistics.mean(r.fp_step_ups for r in rs), 2),
                mean_hours_before_target=round(statistics.mean([a.hours_before_target or 0 for a in stopped]), 1)
                if stopped else None,
                patterns_in_memory=round(statistics.mean(sum(x.patterns_promoted for x in chain[:p + 1])
                                                         for chain in runs), 2),
                legit_in_memory=round(statistics.mean(sum(x.legit_promoted for x in chain[:p + 1])
                                                      for chain in runs), 2)))
        table[mode] = rows
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
    ap.add_argument("--chains", default="", help="round 3: comma-separated seed ranges, one chain each, e.g. 1-4,11-14")
    ap.add_argument("--best-practices", action="store_true", help="round 4: VoP and policy callbacks in every lane")
    ap.add_argument("--compliance", type=float, default=0.7, help="round 4: share of callbacks that follow the policy")
    ap.add_argument("--consortium", action="store_true", help="round 5: chains of companies (use --chains)")
    ap.add_argument("--reuse", default="0,0.25,0.5,0.75,1", help="round 5: rates of infrastructure reuse")
    args = ap.parse_args()
    if args.consortium:
        data = {r: {lane: [[dict(asdict(x), stopped=x.stopped, early=x.early, loss=x.loss)
                            for x in run_consortium(_seeds(part), lane, float(r), args.compliance)]
                           for part in args.chains.split(",")] for lane in ("alone", "blacklist", "consortium")}
                for r in args.reuse.split(",")}
        for r, lanes in data.items():
            print(f"reuse {r}: " + ", ".join(f"{lane} stopped {sum(c['stopped'] for ch in runs for c in ch)}"
                                            for lane, runs in lanes.items()))
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(data, indent=1, default=str))
        return
    if args.chains:
        chains = {m + ("+bp" if args.best_practices else ""): [run_chain(_seeds(part), m, args.best_practices,
                                                                          args.compliance)
                                                                for part in args.chains.split(",")]
                  for m in args.modes.split(",")}
        table = summarize_periods(chains)
        for mode, rows in table.items():
            for row in rows:
                print(mode.ljust(9), json.dumps(row))
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(dict(summary=table, chains={m: [[dict(
                asdict(r), stopped=r.stopped, early=r.early, loss=r.loss) for r in chain] for chain in runs]
                for m, runs in chains.items()}), indent=1, default=str))
        return
    results = [run(generate(s), m, None, args.best_practices, args.compliance) for s in _seeds(args.seeds)
               for m in args.modes.split(",")]
    table = summarize(results)
    for mode, row in table.items():
        print(mode.ljust(9), json.dumps(row))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(dict(summary=table, runs=[
            dict(asdict(r), stopped=r.stopped, early=r.early, loss=r.loss) for r in results]), indent=1, default=str))


if __name__ == "__main__":
    main()
