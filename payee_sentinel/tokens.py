"""Tokens with and without Meko: what Claude has to read to explain the same decision.

The auditor agent asks Claude "why did the agent do that?" about every decision of the demo that stopped something
(Hallberg's Meko lane in weeks 1-2, Weller's consortium lane in week 3). Claude gets what it needs in one of three ways:

- **With Meko:** the decision's trace, that is its rule, its risk and the signals the agent found in Meko, each with its
  memory id. This is exactly what the demo sends (`Reader.facts`).
- **Without Meko, the vendor's records:** there are no signals and no traces, only each system's own records. Claude
  reads the decision and its rule, plus every record of that vendor from every channel up to the decision, as if
  someone had joined the logs by vendor.
- **Without Meko, all records:** the same with nothing to filter by: every record of the company up to the decision.

Input tokens come from Anthropic's token-counting endpoint, which generates nothing. With `--run`, Claude Opus 5.5 also
explains each decision once per condition, under a spending cap, and the table adds the tokens billed, the time, the
cost and how many sentences passed the code's checks (`Reader.explain_facts`). The demo's world is small (six
suppliers), so the script also counts what 30 days of records weigh at the size of the measured worlds (60 suppliers,
`scenario.generate`).

It needs ANTHROPIC_API_KEY and stops before doing anything without it. It writes `docs/TOKENS.md` and a JSON copy in
`runs/`:

    python scripts/token_table.py              # token counts only
    python scripts/token_table.py --run        # plus one real explanation per decision, with Meko and without
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import time

from .context import load_env
from .context.local import LocalMeko
from .context.replay import latest_recording
from .reader import EXPLAIN_SCHEMA, EXPLAIN_SYSTEM, MAX_TOKENS, MODEL, RECORDS_SYSTEM, Reader
from .scenario import START, Event, generate
from .story import BUYER, BUYER2, CHAPTERS, INVOICES, VENDORS, VENDORS2, Story

ROOT = Path(__file__).resolve().parents[1]
PRICE = {"input": 4.0, "output": 20.0}            # $ per million tokens, Claude Opus 5.5 (Nov 2026)
CONDITIONS = {"meko": "With Meko: the trace", "vendor": "Without Meko: the vendor's records",
              "all": "Without Meko: all records"}
SCHEMA = {"format": {"type": "json_schema", "schema": EXPLAIN_SCHEMA}}
WORLD_SEED, WORLD_DAYS = 101, 30                  # the first world of round 1, its first 30 days


def record_line(e: Event) -> str:
    return (f"- {e.id} | {e.ts:%a %d %b %Y %H:%M} | {e.channel} | {e.kind} | vendor {e.vendor_id} | "
            f"{json.dumps(e.data, ensure_ascii=False, default=str)}")


def records_facts(d: dict, vendor: str, vendors: dict, records: list[Event]) -> str:
    """What the auditor could hand Claude without Meko: the decision, its rule and the raw records."""
    lines = [f"Decision: {d['what']} (outcome {d['outcome']}) by the {d['agent']} agent on "
             f"{d['at']:%A %d %B %Y %H:%M}, about the supplier {vendor}.",
             f"Rule: {d['rule']}.",
             "Suppliers: " + "; ".join(f"{vid} = {v.name}" for vid, v in vendors.items()) + ".",
             f"Records from the company's systems up to the decision ({len(records)}; record id | when | channel | "
             f"what | vendor | details):"]
    return "\n".join(lines + [record_line(e) for e in records])


def company_records(week3: bool) -> list[Event]:
    """Every record of the company in the demo: Hallberg's in weeks 1-2 (with the invoices it received), Weller's in
    week 3."""
    events = [e for c in CHAPTERS if c.act == week3 for e in c.events]
    if not week3:
        events += [e for run in INVOICES.values() for e in run]
    return sorted(events, key=lambda e: e.ts)


def demo_items() -> list[dict]:
    """The demo's decisions that stopped something, each with the three versions of what Claude would read. The story
    runs on the offline simulator with no reader, so the decisions are the measured ones."""
    story = Story(LocalMeko)
    for ch in CHAPTERS:
        story.advance()
        if ch.key == "audit":
            story.promote("V001")
    items = []
    for company, lane, week3, vendors in ((BUYER, story.lanes["learning2"], False, VENDORS),
                                          (BUYER2, story.act["consortium"], True, VENDORS2)):
        records = company_records(week3)
        for d in lane.log:
            if d["outcome"] == "allow":
                continue
            vendor = vendors[d["vendor_id"]].name
            upto = [e for e in records if e.ts <= d["at"]]
            mine = [e for e in upto if e.vendor_id == d["vendor_id"]]
            items.append(dict(
                label=f"{company.split()[0]}, {d['at']:%a %d %b %H:%M}: {d['what']} ({vendor})",
                facts={"meko": Reader.facts(d, vendor),
                       "vendor": records_facts(d, vendor, {d["vendor_id"]: vendors[d["vendor_id"]]}, mine),
                       "all": records_facts(d, vendor, vendors, upto)},
                ids={"meko": {s.memory_id for s in d["signals"] if s.memory_id},
                     "vendor": {e.id for e in mine}, "all": {e.id for e in upto}},
                records={"vendor": len(mine), "all": len(upto)}, signals=len(d["signals"]),
                about=f"{vendor}: {d['what']}"))
    return items


def count(client, system: str, user: str) -> tuple[int, bool]:
    """Input tokens of one request as Reader would send it; False when the answer's schema could not be counted."""
    kwargs = dict(model=MODEL, system=system, messages=[{"role": "user", "content": user}])
    try:
        return client.messages.count_tokens(**kwargs, output_config=SCHEMA).input_tokens, True
    except Exception as exc:                        # an endpoint that refuses output_config: count without it
        if getattr(exc, "status_code", None) != 400:
            raise
        return client.messages.count_tokens(**kwargs).input_tokens, False


def world_scale(client) -> dict:
    """30 days of records at the size of the measured worlds, all suppliers and channels together."""
    world = generate(WORLD_SEED)
    until = START + timedelta(days=WORLD_DAYS)
    records = [e for e in world.events if START <= e.ts < until]
    text = (f"Records from the company's systems ({len(records)}; record id | when | channel | what | vendor | "
            f"details):\n" + "\n".join(record_line(e) for e in records))
    tokens, _ = count(client, RECORDS_SYSTEM, text)
    return dict(seed=WORLD_SEED, days=WORLD_DAYS, vendors=len(world.vendors), records=len(records), tokens=tokens)


def meko_estimate() -> list[dict]:
    """Meko's own `token_savings`, as its context_search returned them in the run recorded on real Meko."""
    path = latest_recording()
    if path is None:
        return []
    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    return [e["last_search"]["token_savings"] for e in entries
            if (e.get("last_search") or {}).get("token_savings")]


def cost(usage: dict) -> float:
    return (usage.get("input_tokens", 0) * PRICE["input"] + usage.get("output_tokens", 0) * PRICE["output"]) / 1e6


def measure(client, run: bool, everything: bool, max_dollars: float) -> dict:
    items, schema_counted = demo_items(), True
    for it in items:
        it["tokens"] = {}
        for cond, facts in it["facts"].items():
            n, ok = count(client, EXPLAIN_SYSTEM if cond == "meko" else RECORDS_SYSTEM, facts)
            it["tokens"][cond], schema_counted = n, schema_counted and ok
    runs, spent = [], 0.0
    if run:
        reader = Reader(client)                     # no store: this script writes nothing to Meko
        for it in items:
            for cond in ("meko", "vendor") + (("all",) if everything else ()):
                worst = (it["tokens"][cond] * PRICE["input"] + MAX_TOKENS * PRICE["output"]) / 1e6
                row = dict(item=it["label"], condition=cond)
                if spent + worst > max_dollars:
                    runs.append(dict(row, note=f"skipped: it could take the spending over ${max_dollars:.2f}"))
                    continue
                t0 = time.perf_counter()
                res = reader.explain_facts(it["facts"][cond], it["ids"][cond], it["about"],
                                           system=EXPLAIN_SYSTEM if cond == "meko" else RECORDS_SYSTEM,
                                           exact_ids=cond != "meko")
                seconds = time.perf_counter() - t0
                spent += cost(res.usage)
                runs.append(dict(row, seconds=round(seconds, 1), cost=round(cost(res.usage), 4), kept=len(res.items),
                                 dropped=len(res.dropped), note=res.note, sentences=res.items, **res.usage))
    return dict(when=datetime.now().strftime("%d %b %Y %H:%M"), model=MODEL, schema_counted=schema_counted,
                items=[{k: v for k, v in it.items() if k not in ("facts", "ids")} for it in items],
                scale=world_scale(client), meko_estimate=meko_estimate(), runs=runs, spent=round(spent, 4))


def markdown(m: dict) -> str:
    lines = ["# Tokens with and without Meko", "",
             f"Generated by `scripts/token_table.py` on {m['when']} (local time), with `{m['model']}`"
             + (", including one real explanation per decision and condition." if m["runs"] else
                ": token counts only."), "",
             "What Claude reads when the auditor agent asks it why an agent stopped something in the demo. Input "
             "tokens from Anthropic's token-counting endpoint, instructions "
             + ("and the answer's schema included." if m["schema_counted"] else
                "included (the answer's schema could not be counted, the same for every condition)."), "",
             f"| Decision | {CONDITIONS['meko']} | {CONDITIONS['vendor']} | {CONDITIONS['all']} |", "|---|---|---|---|"]
    for it in m["items"]:
        t, r = it["tokens"], it["records"]
        lines.append(f"| {it['label']} | {t['meko']:,} ({it['signals']} signals) | {t['vendor']:,} ({r['vendor']} "
                     f"records, {t['vendor'] / t['meko']:.1f}×) | {t['all']:,} ({r['all']} records, "
                     f"{t['all'] / t['meko']:.1f}×) |")
    s = m["scale"]
    lines += ["", f"The demo's companies are small: six suppliers and a few dozen records. At the size of the measured "
                  f"worlds ({s['vendors']} suppliers, seed {s['seed']}), {s['days']} days of records from every "
                  f"channel are {s['records']:,} records and **{s['tokens']:,} tokens**. The trace Meko returns holds "
                  f"one vendor's signals, whatever the size of the company."]
    if m["runs"]:
        lines += ["", "## Real explanations", "",
                  "Each decision explained once per condition. Kept: sentences that passed the code's checks (every id "
                  "and number they use is in what Claude was given).", "",
                  "| Decision | Condition | Input tokens | Output tokens | Seconds | Cost | Sentences kept / dropped |",
                  "|---|---|---|---|---|---|---|"]
        for r in m["runs"]:
            if "input_tokens" not in r:
                lines.append(f"| {r['item']} | {CONDITIONS[r['condition']]} | | | | | {r['note']} |")
                continue
            lines.append(f"| {r['item']} | {CONDITIONS[r['condition']]} | {r['input_tokens']:,} | "
                         f"{r['output_tokens']:,} | {r['seconds']} | ${r['cost']:.4f} | {r['kept']} / {r['dropped']}"
                         + (f" ({r['note']})" if r["note"] else "") + " |")
        lines += ["", f"Spent: ${m['spent']:.2f} at ${PRICE['input']:g} per million input tokens and "
                      f"${PRICE['output']:g} per million output tokens (output includes thinking)."]
    if m["meko_estimate"]:
        lines += ["", "Meko's own estimate, as its `context_search` returned it in the run recorded on real Meko "
                      "(field `token_savings`): " + "; ".join(json.dumps(x) for x in m["meko_estimate"]) + "."]
    lines += ["", "## How to read it", "",
              "- *With Meko* is what the demo sends: the decision's trace with the signals the agent found.",
              "- *Without Meko* there is no trace: Claude gets the decision, its rule and raw records, as if the logs "
              "had been joined by vendor (the vendor's records) or not at all (all records).",
              "- Meko's own work (writing, extracting and searching memories) runs on Meko's side and is not counted "
              "here. All data is invented; the demo's decisions are those of the offline simulator.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None, client=None) -> int:
    ap = argparse.ArgumentParser(description="Tokens Claude reads to explain a decision, with and without Meko.")
    ap.add_argument("--run", action="store_true", help="also ask Claude for each explanation (costs money)")
    ap.add_argument("--everything", action="store_true", help="with --run, also explain from all records")
    ap.add_argument("--max-dollars", type=float, default=1.0, help="spending cap for --run (default 1.00)")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "TOKENS.md")
    ap.add_argument("--json", type=Path, default=None, help="where to keep the raw numbers (default runs/)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")    # a console that cannot show "×" prints "?" instead of failing
    if client is None:
        load_env()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("This script needs ANTHROPIC_API_KEY (in .env or in the environment). Nothing was run.")
            return 2
        import anthropic
        client = anthropic.Anthropic()
    m = measure(client, args.run, args.everything, args.max_dollars)
    args.out.write_text(markdown(m), encoding="utf-8", newline="\n")
    raw = args.json or ROOT / "runs" / f"tokens-{datetime.now():%Y%m%d-%H%M}.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(m, indent=1, ensure_ascii=False, default=str), encoding="utf-8", newline="\n")
    print(markdown(m))
    print(f"Written: {args.out} and {raw}")
    return 0
