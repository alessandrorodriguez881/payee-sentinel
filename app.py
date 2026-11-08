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
from payee_sentinel.story import AGENT_NAMES, BUYER, CHAPTERS, LANES, RUNS, VENDORS, Story

st.set_page_config(page_title="Payee Sentinel", page_icon=":material/shield_lock:", layout="wide")

load_env()
HAS_KEY = os.environ.get("MEKO_API_KEY", "").startswith("mko_tkn_")
TARGET_RUN = {"V001": RUNS["run1"], "V002": RUNS["run2"]}
ALERT = {"good": st.success, "bad": st.error, "warn": st.warning, "neutral": st.info}
ICON = {"good": ":material/verified_user:", "bad": ":material/money_off:", "warn": ":material/schedule:",
        "neutral": ":material/info:"}


def new_story(real: bool) -> Story:
    if real:
        from payee_sentinel.context.meko import MekoClient
        return Story(MekoClient)
    return Story(LocalMeko)


def money(x: float) -> str:
    return f"€{x:,.0f}"


ss = st.session_state
if "story" not in ss:
    ss.real = False
    ss.story = new_story(False)
story: Story = ss.story
meko_lane = story.lanes["learning2"]


def promote_pattern() -> None:
    story.promote("V001")


# ---------- sidebar: the controls stay in one place ----------
with st.sidebar:
    st.header("Payee Sentinel")
    st.caption("Accounts-payable agents that share what they see through Meko, so a vendor-account takeover is "
               "stopped before the money leaves.")
    nxt = story.next_chapter
    waiting = (story.done and story.done[-1][0].kind == "audit"
               and not meko_lane.candidates.get("V001", {}).get("promoted", True))
    st.progress(len(story.done) / len(CHAPTERS), text=f"Step {len(story.done)} of {len(CHAPTERS)}")
    clicked = st.button(f"Next: {nxt.title}" if nxt else "End of the story", type="secondary" if waiting else "primary",
                        disabled=nxt is None, width="stretch", icon=":material/play_arrow:")
    restart = st.button("Start again", width="stretch", icon=":material/restart_alt:")
    real = st.toggle("Use real Meko", value=ss.real, disabled=not HAS_KEY,
                     help="The whole story took 73 s on real Meko (searches: 1.7 s median, up to 16 s in earlier "
                          "tests). Off: an offline simulator with the same rules.")
    st.caption((":material/cloud: Real Meko, mcp.mekodata.ai" if ss.real else
                ":material/computer: Offline simulator of Meko") + " · all data is invented")

if real != ss.real or restart:
    ss.real = real
    with st.status("Setting up the datapacks…" if real else "Starting again…"):
        ss.story = new_story(real)
    st.rerun()

if clicked and nxt:
    with st.status(f"Agents at work: {nxt.title}") as status:
        story.advance()
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
                for line in story.explain("siloed", "V001"):
                    st.markdown(f"- {line}")
            with right:
                st.markdown(f"**{LANES['learning2']}**")
                with st.container(border=True):
                    for line in story.explain("learning2", "V001"):
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
    counts[1].metric("Searches", sum(c.tool in ("memory_search", "memory_get_all", "context_search")
                                     for c in calls), border=True)
    counts[2].metric("Decisions with reasoning", sum(c.tool == "conversation_add_message" for c in calls),
                     border=True)
    counts[3].metric("Promotions by a person", sum(c.tool == "promote_memories" for c in calls), border=True)
    st.caption("Every call the Meko lane made, newest first. On real Meko each decision also appears in the portal "
               "as a decision trace.")
    st.dataframe([dict(time=datetime.fromisoformat(c.at).astimezone().strftime("%H:%M:%S"),
                       agent=AGENT_NAMES.get(c.agent_id, c.agent_id), call=c.tool,
                       datapack=dp_name.get(c.datapack, c.datapack[:8]), what=c.summary,
                       results=c.found or None, ms=c.latency_ms) for c in reversed(calls)],
                 hide_index=True, alt="Calls the Meko lane made to Meko")

# ---------- measured results ----------
with results_tab:
    st.markdown("Twenty synthetic six-week periods per round, 60 vendors each, 160 planted attacks and 400 legitimate "
                "look-alikes. Hypotheses were written before each run; the offline simulator of Meko was used.")
    st.table([
        {"": "Attacks stopped", "Round 1 · silos": "81 of 160", "Round 1 · shared memory": "156 of 160",
         "Round 2 · silos": "82 of 160", "Round 2 · shared memory": "158 of 160"},
        {"": "Money sent to attackers", "Round 1 · silos": "€2.81M", "Round 1 · shared memory": "€0.13M",
         "Round 2 · silos": "€4.30M", "Round 2 · shared memory": "€7,274"},
        {"": "False holds per six weeks", "Round 1 · silos": "0", "Round 1 · shared memory": "1.45",
         "Round 2 · silos": "0", "Round 2 · shared memory": "1.15"},
        {"": "Bank changes held for a call", "Round 1 · silos": "0", "Round 1 · shared memory": "4.45",
         "Round 2 · silos": "0", "Round 2 · shared memory": "4.60"},
    ])
    st.caption("Learning from people (version 2) cut wrong freezes by more than half and acted about 6 hours earlier, "
               "but over six weeks it cost more analyst work than shared memory alone. All hypotheses, including "
               "the ones that failed, are in docs/RESULTS.md.")
