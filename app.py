"""Payee Sentinel demo: the same two weeks of accounts payable, run by agents in silos and by agents that share
memory through Meko. Run it from this folder (so the theme in .streamlit/ loads):

    streamlit run app.py
"""
from __future__ import annotations

from datetime import datetime
import os

import streamlit as st

from payee_sentinel.context import load_env
from payee_sentinel.context.local import LocalMeko
from payee_sentinel.context.replay import Replayer, ReplayMismatch, latest_recording
from payee_sentinel.ledger import PgLedger, open_ledger
from payee_sentinel.story import AGENT_NAMES, BUYER, CHAPTERS, LANES, RUNS, VENDORS, Story

st.set_page_config(page_title="Payee Sentinel", page_icon=":material/shield_lock:", layout="wide")

load_env()
HAS_KEY = os.environ.get("MEKO_API_KEY", "").startswith("mko_tkn_")
RECORDING = latest_recording()
SIMULATOR, REPLAY, LIVE = "Offline simulator", "Replay of a recorded run", "Real Meko, live"
ENGINES = [SIMULATOR] + ([REPLAY] if RECORDING else []) + ([LIVE] if HAS_KEY else [])
TARGET_RUN = {"V001": RUNS["run1"], "V002": RUNS["run2"]}
ALERT = {"good": st.success, "bad": st.error, "warn": st.warning, "neutral": st.info}
ICON = {"good": ":material/verified_user:", "bad": ":material/money_off:", "warn": ":material/schedule:",
        "neutral": ":material/info:"}


def new_story(engine: str) -> Story:
    """The ledger is Postgres in Meko when this machine can reach it (its details are in .env), else SQLite."""
    if engine == LIVE:
        from payee_sentinel.context.meko import MekoClient
        return Story(MekoClient, make_ledger=open_ledger)
    if engine == REPLAY:
        replay = Replayer(RECORDING)
        return Story(lambda: replay, run_id=replay.run_id, make_ledger=open_ledger)
    return Story(LocalMeko, make_ledger=open_ledger)


def money(x: float) -> str:
    return f"€{x:,.0f}"


ss = st.session_state
if "story" not in ss:
    ss.engine = SIMULATOR
    ss.story = new_story(SIMULATOR)
story: Story = ss.story
meko_lane = story.lanes["learning2"]
replaying = isinstance(meko_lane.store, Replayer)


def promote_pattern() -> None:
    story.promote("V001")


# ---------- sidebar: the controls stay in one place ----------
with st.sidebar:
    st.header("Payee Sentinel")
    st.caption("Accounts-payable agents that share what they see through Meko, so a vendor-account takeover is "
               "stopped before the money leaves.")
    nxt = story.next_chapter
    waiting = bool(story.done and story.done[-1][0].kind == "audit"
                   and not meko_lane.candidates.get("V001", {}).get("promoted", True))
    st.progress(len(story.done) / len(CHAPTERS), text=f"Step {len(story.done)} of {len(CHAPTERS)}")
    clicked = st.button(f"Next: {nxt.title}" if nxt else "End of the story", type="secondary" if waiting else "primary",
                        disabled=nxt is None or (replaying and waiting), width="stretch", icon=":material/play_arrow:")
    if replaying and waiting:
        st.caption("In the recorded run a person promoted the pattern here: press Promote to go on.")
    restart = st.button("Start again", width="stretch", icon=":material/restart_alt:")
    engine = ss.engine
    if len(ENGINES) > 1:
        engine = st.radio("Context engine", ENGINES, index=ENGINES.index(ss.engine),
                          help="Offline simulator: the same rules, no key. Replay: the answers real Meko gave in one "
                               "recorded run, in the same order and with their timings. Real Meko, live: reliable "
                               "only on empty datapacks; after earlier runs, their near-identical memories outrank a "
                               "new run's (docs/MEKO_SMOKE.md).")
    st.caption((f":material/replay: Replay of a {meko_lane.store.engine} run recorded "
                f"{meko_lane.store.recorded_at[:10]}" if replaying else
                ":material/cloud: Real Meko, mcp.mekodata.ai" if ss.engine == LIVE else
                ":material/computer: Offline simulator of Meko") + " · all data is invented")
    st.caption((":material/database: Ledger: Postgres in Meko (AMP)" if isinstance(story.ledger, PgLedger) else
                ":material/database: Ledger: a local SQLite copy"))

if engine != ss.engine or restart:
    ss.engine = engine
    with st.status("Setting up the datapacks…" if engine == LIVE else "Starting again…"):
        ss.story = new_story(engine)
    st.rerun()

if clicked and nxt:
    with st.status(f"Agents at work: {nxt.title}") as status:
        try:
            story.advance()
        except ReplayMismatch as exc:
            status.update(label="The replay went off script", state="error")
            st.error(f"{exc}. Press Start again.", icon=":material/error:")
            st.stop()
        status.update(label="Done", state="complete")
    st.rerun()

story_tab, meko_tab, results_tab = st.tabs(["Story", "What Meko recorded", "Measured results"])

# ---------- the story ----------
with story_tab:
    if not story.done:
        st.subheader("Two teams of agents, the same two weeks")
        st.markdown(
            f"**{BUYER}** pays its suppliers every Thursday. Two teams of agents watch the same events with "
            f"the same rules. On the left, each agent keeps its own memory, as separate tools do today. On the right, "
            f"they share it through **Meko**: what one agent writes, the others find before they act.")
        cast = st.columns(3)
        cast[0].markdown("**Portal agent** — logins, password resets and contact changes on the supplier portal\n\n"
                         "**Inbox agent** — emails to accounts payable")
        cast[1].markdown("**Vendor-master agent** — bank-change requests and the clerk's callbacks\n\n"
                         "**Payments agent** — the Thursday payment run")
        cast[2].markdown("**Auditor agent** — explains each decision from its trace\n\n"
                         "**Analyst** — a person who confirms cases and approves what becomes shared knowledge")
        st.info("Press **Next** in the sidebar to start on Monday morning.", icon=":material/arrow_back:")
    else:
        ch, outcomes = story.done[-1]
        st.subheader(ch.title)
        st.caption(f"{ch.when:%A %d %B %Y, %H:%M}")
        st.write(ch.story)
        if ch.kind in ("events", "payments"):
            for col, (mode, o) in zip(st.columns(2, gap="large"), outcomes.items()):
                with col:
                    st.markdown(f"**{LANES[mode]}**")
                    ALERT[o.tone](o.headline, icon=ICON[o.tone])
                    for step in o.steps:
                        with st.status(f"{AGENT_NAMES[step.agent]} · {step.title}", type="step", expanded=True,
                                       state="error" if step.tone == "bad" else "complete"):
                            for line in step.lines:
                                st.markdown(f"- {line}")
        elif ch.kind == "audit":
            left, right = st.columns(2, gap="large")
            with left:
                st.markdown(f"**{LANES['siloed']}**")
                st.error("Nordwind chases its €48,300 on 30 November. That is when the loss is found.",
                         icon=ICON["bad"])
                for line in outcomes["siloed"].notes:
                    st.markdown(f"- {line}")
            with right:
                st.markdown(f"**{LANES['learning2']}**")
                with st.container(border=True):
                    for line in outcomes["learning2"].notes:
                        st.markdown(f"- {line}")
                cand = meko_lane.candidates.get("V001")
                if cand:
                    with st.container(border=True):
                        st.markdown("**Pattern candidate written by the auditor agent**")
                        st.markdown(" ".join(f"`{t}`" for t in cand["types"]))
                        if cand["promoted"]:
                            st.success("Promoted to Meko's collective memory: every agent will check new vendors "
                                       "against it.", icon=":material/hub:")
                        else:
                            st.caption("A person decides. Promoting moves it into Meko's collective memory, where "
                                       "every agent finds it.")
                            st.button("Promote to collective memory", type="primary", icon=":material/hub:",
                                      on_click=promote_pattern)
                if outcomes["learning2"].learning:
                    with st.container(border=True):
                        st.markdown("**Meko's self-learning loop, run by the auditor agent**")
                        for line in outcomes["learning2"].learning:
                            st.markdown(f"- {line}")
                        st.caption("Meko grades every open question an agent asks it and lists the weak ones. The "
                                   "fix is new context; the replay checks it. Next week's request will be asked the "
                                   "same way.")
        else:
            summary = story.summary()
            cols = st.columns(2, gap="large")
            for col, mode in zip(cols, LANES):
                s = summary[mode]
                with col:
                    st.markdown(f"**{LANES[mode]}**")
                    st.metric("Sent to the attackers", money(s["lost"]), border=True)
                    for vid in ("V001", "V002"):
                        at = s["first_action"].get(vid)
                        if at is None:
                            st.markdown(f"- {VENDORS[vid].name}: never stopped")
                        else:
                            hours = (TARGET_RUN[vid] - at).total_seconds() / 3600
                            st.markdown(f"- {VENDORS[vid].name}: stopped {at:%a %d %b %H:%M}, "
                                        f"{hours:.0f} hours before the payment run")
                    if mode == "learning2":
                        st.markdown(f"- Patterns promoted by a person: {s['promoted']}")
        if len(story.done) > 1:
            with st.expander("Earlier steps", icon=":material/history:"):
                for c, out in story.done[:-1]:
                    if c.kind in ("events", "payments"):
                        st.markdown(f"**{c.title}** — silos: {out['siloed'].headline} · "
                                    f"Meko: {out['learning2'].headline}")

# ---------- what Meko recorded ----------
with meko_tab:
    store = meko_lane.store
    names = getattr(store, "datapacks", None) or getattr(store, "_datapacks", {})
    dp_name = {v: k for k, v in names.items()}
    calls = store.calls
    counts = st.columns(4)
    counts[0].metric("Memories written", sum(c.tool == "memory_add" for c in calls), border=True)
    counts[1].metric("Searches", sum(c.tool in ("memory_search", "memory_get_all", "context_search",
                                                "knowledgebase_search", "selflearning_verify") for c in calls),
                     border=True)
    counts[2].metric("Decisions with reasoning", sum(c.tool == "conversation_add_message" for c in calls),
                     border=True)
    counts[3].metric("Promotions by a person", sum(c.tool in ("promote_memories", "POST /promote-memories")
                                                   for c in calls), border=True)
    log = meko_lane.log
    if log:
        st.markdown("**Every decision, with the search behind it**")
        st.caption("Allow, step-up, hold, block or freeze: each one is a conversation turn in Meko with its reasoning, "
                   "its plan, the query the agent ran and the ids of the memories it found.")
        st.dataframe([dict(when=f"{d['at']:%a %d %b %H:%M}", agent=AGENT_NAMES[d["agent"]],
                           vendor=VENDORS[d["vendor_id"]].name, decision=d["what"], outcome=d["outcome"],
                           risk=d["risk"], memories=len(d["memory_ids"]), related_case=d.get("related_case", ""))
                      for d in log], hide_index=True, alt="Every decision the Meko lane made")
        first_stop = next((i for i, d in enumerate(log) if d["outcome"] != "allow"), 0)
        pick = st.selectbox("Open a decision's trace", range(len(log)), index=first_stop,
                            format_func=lambda i: f"{log[i]['at']:%a %H:%M} · {AGENT_NAMES[log[i]['agent']]} · "
                                                  f"{VENDORS[log[i]['vendor_id']].name}: {log[i]['what']}")
        d = log[pick]
        with st.container(border=True):
            st.markdown(f"**{AGENT_NAMES[d['agent']]}, {d['at']:%A %d %B %H:%M}: {d['what']}** ({d['outcome']})")
            st.markdown(f"- Reasoning saved in Meko: {d['reasoning']}")
            st.markdown(f"- Search: `{d['query']}` in `{d['datapack']}`, "
                        f"{len(d['memory_ids'])} memories found")
            if d.get("related_case"):
                st.markdown(f"- Earlier confirmed case Meko found for this request: {d['related_case']}")
            if d["signals"]:
                st.dataframe([dict(seen=f"{s.at:%a %d %b %H:%M}", agent=AGENT_NAMES[s.agent], signal=s.type,
                                   weight=s.weight, detail=s.detail, memory=s.memory_id[:8])
                              for s in sorted(d["signals"], key=lambda s: s.at)],
                             hide_index=True, alt="The memories behind the decision")
    st.markdown("**The ledger: Postgres inside the context**")
    st.caption(f"{story.ledger.kind}. The vendor master with its history, the bank-change requests and the payments of "
               f"the Meko lane; every record a decision made carries the id of that decision's trace in Meko.")
    pick_table = st.segmented_control("Ledger table", ["Payments", "Bank-change requests", "Vendor master",
                                                       "Changes on file", "SQL queries"], default="Payments",
                                      label_visibility="collapsed")
    if pick_table == "SQL queries":
        st.dataframe([dict(sql=q.sql, rows=q.rows, ms=q.ms) for q in reversed(story.ledger.queries)], hide_index=True,
                     alt="Every query the ledger ran")
    elif pick_table:
        name = {"Payments": "payments", "Bank-change requests": "bank_changes", "Vendor master": "vendors",
                "Changes on file": "vendor_history"}[pick_table]
        rows = story.ledger.table(name)
        if name == "vendor_history":
            rows = [r for r in rows if r["source"] != "onboarding"]
        st.dataframe(rows, hide_index=True, alt=f"Ledger table {name}")
    st.markdown("**Every call to Meko**")
    st.caption("Newest first. Each decision is saved twice: as a conversation turn with its reasoning, and as a "
               "decision record the auditor agent searches when it explains a case."
               + (f" Replayed from the {meko_lane.store.engine} run recorded on {meko_lane.store.recorded_at[:10]}: "
                  f"times and latencies are the recorded ones." if replaying else ""))
    st.dataframe([dict(time=datetime.fromisoformat(c.at).astimezone().strftime("%H:%M:%S"),
                       agent=AGENT_NAMES.get(c.agent_id, c.agent_id), call=c.tool,
                       datapack=dp_name.get(c.datapack, c.datapack), what=c.summary,
                       results=c.found or None, ms=c.latency_ms) for c in reversed(calls)],
                 hide_index=True, alt="Calls the Meko lane made to Meko")

# ---------- measured results ----------
with results_tab:
    st.markdown("Twenty synthetic six-week periods per round, 60 vendors each, 160 planted attacks and 400 legitimate "
                "look-alikes. Hypotheses were written before each run; the offline simulator of Meko was used.")
    cols = ["Round 1 · silos", "Round 1 · shared memory", "Round 2 · silos", "Round 2 · shared memory",
            "Round 2 · learning v2 (the demo's Meko lane)"]
    rows = {"Attacks stopped (of 160)": ["81", "156", "82", "158", "158"],
            "Money sent to attackers": ["€2.27M", "€0.13M", "€3.17M", "€7,274", "€7,274"],
            "False holds per six weeks": ["0", "1.30", "0", "1.00", "1.10"],
            "Step-up calls to real vendors": ["0", "4.50", "0", "4.15", "4.00"],
            "Legitimate bank changes held for a call": ["0", "4.45", "0", "4.60", "3.45"],
            "Vendors frozen by a wrong pattern match": ["0", "0", "0", "0", "1.60"]}
    st.table([{"": name, **dict(zip(cols, values))} for name, values in rows.items()])
    st.caption("Learning from people (version 2) cut wrong freezes by more than half and acted about 6 hours earlier, "
               "and it nearly pays for itself, but not quite over six weeks (6.15 frictions per period against "
               "5.60).")
    st.markdown("**With experience (round 3):** ten chains of four six-week periods, each a new world, carrying only "
                "Meko's collective memory from one period to the next.")
    periods = ["Period 1", "Period 2", "Period 3", "Period 4"]
    rows3 = {"Wrong freezes, learning v2": ["3.10", "0.80", "0.40", "0.60"],
             "Frictions, learning v2": ["6.60", "6.10", "5.70", "6.00"],
             "Frictions, shared memory alone": ["5.70", "5.90", "5.60", "5.80"],
             "Hours earlier than shared memory, learning v2": ["4.7", "51.4", "43.7", "41.4"],
             "Hours earlier than shared memory, learning v1 (attacks only)": ["11.0", "52.3", "50.8", "52.2"],
             "Wrong freezes, learning v1": ["5.40", "6.90", "6.80", "6.50"],
             "Frictions, learning v1": ["8.40", "8.80", "8.40", "8.10"]}
    st.table([{"": name, **dict(zip(periods, values))} for name, values in rows3.items()])
    st.markdown("**A fair comparison (round 4):** every lane gets Verification of Payee and a callback that follows the "
                "payment policy; a plain shared table runs as a lane of its own. Ten chains of four periods, 320 attacks.")
    st.table([{"": "Attacks stopped (of 320)", "Silos, no best practices": "163", "Silos with best practices": "305",
               "Shared table": "319", "Meko": "319"},
              {"": "Money sent to attackers", "Silos, no best practices": "€7,490,207",
               "Silos with best practices": "€710,177", "Shared table": "€12,535", "Meko": "€12,535"},
              {"": "Frictions per period (periods 2-4)", "Silos, no best practices": "0", "Silos with best practices": "0",
               "Shared table": "3.83", "Meko": "4.20"},
              {"": "Hours before the target run (periods 2-4)", "Silos, no best practices": "—",
               "Silos with best practices": "61.7", "Shared table": "80.8", "Meko": "136.7"}])
    st.markdown("**A consortium of companies (round 5):** what one company confirms (accounts, phones, devices) and the "
                "patterns people approve go to a shared consortium. Attacks stopped before the attacker even asks for "
                "a bank change, in the next companies (160 attacks):")
    st.table([{"": f"Attackers reuse {r} of their infrastructure", "Each company alone": a, "Shared blacklist": b,
               "Consortium (Meko)": c}
              for r, a, b, c in (("0 %", "27", "27", "41"), ("50 %", "27", "31", "48"), ("100 %", "27", "38", "61"))])
    st.caption("Counting also the attacks stopped at the attacker's own email, two minutes before the change is entered "
                "(the pre-registered measure): 36, 36-54 and 89-122. The consortium adds no friction for real vendors.")
    st.caption("All three hypotheses of round 3 held. Both learning versions act earlier because they carry the attack "
               "patterns; only v2, which also learns the legitimate look-alikes, stops freezing vendors by mistake. "
               "Every hypothesis, the ones that failed, and the corrections made after independent reviews are in "
               "docs/RESULTS.md.")
