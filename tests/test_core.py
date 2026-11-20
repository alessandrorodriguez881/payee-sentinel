"""Checks that need no network: the world, the Meko simulator's rules, the memory modes and the real client's parsing."""
from datetime import datetime, timedelta
import json
import random

import httpx
import pytest

from payee_sentinel.agents import Memory, Signal, pattern_match, risk_of
from payee_sentinel.context.base import Hit
from payee_sentinel.context.local import LocalMeko
from payee_sentinel.context.meko import MekoClient, MekoError
from payee_sentinel.context.replay import Recorder, Replayer, ReplayMismatch, latest_recording
from payee_sentinel.evaluate import carry_learning, run, run_chain, run_consortium, summarize
from payee_sentinel.scenario import generate, iban_is_valid, infrastructure, reuse_infrastructure
from payee_sentinel.ledger import SqliteLedger
from payee_sentinel.scenario import Event, make_iban
from payee_sentinel.story import CHAPTERS, CREW, INVOICES, VENDORS, Chapter, Lane, Story


@pytest.fixture(scope="module")
def world():
    return generate(1)


@pytest.fixture(scope="module")
def story():
    s = Story(LocalMeko)
    for ch in CHAPTERS:
        s.advance()
        if ch.key == "audit":
            s.promote("V001")
    return s


def test_world_ibans_are_valid_and_cases_have_events(world):
    ibans = [v.iban for v in world.vendors.values()] + [c.new_iban for c in world.attacks + world.benign if c.new_iban]
    assert all(iban_is_valid(i) for i in ibans)
    assert len(world.attacks) == 8 and len(world.benign) == 20
    assert all(c.event_ids for c in world.attacks + world.benign)
    assert world.events == sorted(world.events, key=lambda e: (e.ts, e.id))


def test_world_is_reproducible():
    a, b = generate(5), generate(5)
    assert [(e.id, e.kind, e.vendor_id) for e in a.events] == [(e.id, e.kind, e.vendor_id) for e in b.events]


def test_local_meko_promotion_is_one_way_and_stays_in_its_datapack():
    m = LocalMeko()
    a, b = m.ensure_datapack("a"), m.ensure_datapack("b")
    cid = m.conversation_create(a, "x", "t")
    [mid] = m.memory_add(a, cid, "x", "vendor V001 asked for a new IBAN", {"kind": "signal"})
    assert m.memory_search(b, cid, "y", "vendor V001 IBAN") == []
    assert m.promote(a, [mid]) == [mid]
    assert m.promote(a, [mid]) == []                      # already moved
    hits = m.context_search(a, cid, "y", "vendor V001 IBAN")
    assert hits["memory"] == []                           # like real Meko: promoted memories leave memory_search
    assert hits["knowledge_base"][0].id == mid           # ...and join the shared knowledge side
    [c] = m.collective(a, cid, "y")
    assert (c.id, c.source, c.metadata) == (mid, "collective", {})   # listed without metadata, as in Meko


def test_local_meko_free_plan_datapack_cap():
    m = LocalMeko()
    for i in range(10):
        m.ensure_datapack(f"dp{i}")
    with pytest.raises(RuntimeError, match="FREE_TIER_DATAPACK_LIMIT"):
        m.ensure_datapack("one-too-many")


def _signal(t, vendor, agent, at):
    return Signal(t, vendor, at, agent, ["E00001"], "test")


def test_siloed_agents_only_read_their_own_signals_and_resolutions_close_cases(world):
    now = datetime(2026, 10, 20, 12)
    for mode, expected in (("siloed", {"portal.new_device"}), ("shared", {"portal.new_device", "email.urgency"})):
        mem = Memory(LocalMeko(), mode, world.vendors)
        mem.write(_signal("portal.new_device", "V002", "portal", now - timedelta(days=1)))
        mem.write(_signal("email.urgency", "V002", "inbox", now - timedelta(days=1)))
        assert {s.type for s in mem.signals("portal", "V002", now)} == expected
        mem.resolve("V002", now - timedelta(hours=1), "legitimate.")
        assert mem.signals("portal", "V002", now) == []


def test_risk_counts_each_signal_type_once():
    at = datetime(2026, 10, 20)
    sigs = [_signal("email.lookalike_domain", "V001", "inbox", at)] * 3 + [_signal("bank.foreign_iban", "V001",
                                                                                   "vendor_master", at)]
    assert risk_of(sigs)[0] == 5


def test_pattern_needs_three_shared_types_covering_most_of_it():
    p = Hit("p1", "", 1, {"kind": "pattern", "signal_types": ["a", "b", "c", "d"]}, "collective")
    assert pattern_match({"a", "b", "c"}, [p]) is p
    assert pattern_match({"a", "b", "x"}, [p]) is None


def test_evaluation_is_deterministic_and_sharing_never_loses_to_silos():
    w = generate(2)
    first = summarize([run(w, "siloed"), run(w, "shared")])
    again = summarize([run(generate(2), "siloed"), run(generate(2), "shared")])
    assert first == again
    assert first["shared"]["stopped"] >= first["siloed"]["stopped"]


def test_round_five_reuse_is_lane_independent_and_the_shared_list_fires():
    first, second = generate(1), generate(2)
    earlier = [infrastructure(first, a) for a in first.attacks]
    reused = reuse_infrastructure(second, earlier, 1.0)
    assert all(a.new_iban in {i["iban"] for i in earlier} for a in reused.attacks)
    assert [e.id for e in reused.events] == [e.id for e in second.events]           # same events, same times
    assert reuse_infrastructure(second, earlier, 0.0) is second
    alone = run_consortium([1, 2], "alone", 1.0)
    shared = run_consortium([1, 2], "blacklist", 1.0)
    assert [r.mode for r in shared] == ["blacklist", "blacklist"]
    assert shared[1].stopped >= alone[1].stopped
    assert summarize([run_consortium([1, 2], "consortium", 0.5)[1]]) == summarize([run_consortium([1, 2], "consortium", 0.5)[1]])


def test_round_four_best_practices_come_from_their_own_stream(world):
    assert set(world.vop.values()) <= {"match", "close_match", "no_match", "unavailable"}
    requests = {e.id: e for e in world.events if e.kind == "bank_change_request"}
    assert set(world.vop) == set(requests) and len(world.policy_draw) == sum(e.kind == "callback" for e in world.events)
    kinds = {c.id: c.kind for c in world.attacks}
    for rid, outcome in world.vop.items():
        e = requests[rid]
        if e.attack_id and not e.data["new_iban"].startswith("PL"):
            assert outcome == ("match" if kinds[e.attack_id] == "careful_mailbox" else "no_match")
    plain, bp = run(world, "siloed"), run(world, "siloed", best_practices=True)
    assert bp.mode == "siloed+bp" and bp.stopped >= plain.stopped
    assert run(world, "siloed", best_practices=True, compliance=0.0).stopped <= bp.stopped


def test_round_three_carries_only_the_collective_memory():
    store = LocalMeko()
    run(generate(1), "learning2", store)
    carried = carry_learning(store)
    promoted = [m["id"] for m in store.memories.values() if m["state"] == "promoted"]
    assert promoted and list(carried.memories) == promoted        # only promoted memories, in promotion order
    assert set(carried.datapacks) <= set(store.datapacks)
    # nothing is promoted in `shared`, so a period in a chain is the same as the world run alone
    assert summarize([run_chain([1, 2], "shared")[1]]) == summarize([run(generate(2), "shared")])


def test_every_decision_is_traced_with_the_memories_behind_it(story):
    n_invoices = sum(len(v) for v in INVOICES.values())
    for mode, lane in story.lanes.items():
        payments = [d for d in lane.log if d["agent"] == "payments"]
        assert len(payments) == n_invoices                      # released ones too, not only holds
        turns = [m for c in lane.store.conversations.values() for m in c["messages"]]
        assert len(turns) == len(lane.log) and all(m["metadata"]["outcome"] for m in turns)
        for d in lane.log:
            assert set(d["memory_ids"]) <= set(lane.store.memories)   # each id is a memory the search returned
    silo, meko = story.lanes["siloed"], story.lanes["learning2"]
    applied = [d for d in silo.log if d["what"] == "bank change applied"]
    assert [d["vendor_id"] for d in applied] == ["V001", "V002"] and silo.first_action == {}
    block = next(d for d in meko.log if d["vendor_id"] == "V001" and d["outcome"] == "block")
    assert len(block["memory_ids"]) >= 10 and {"portal", "inbox"} <= {s.agent for s in block["signals"]}
    assert block["query"] == meko.mem.query("V001")


def test_self_learning_loop_fixes_the_weak_search_and_week_two_finds_a_similar_case(story):
    meko = story.lanes["learning2"]
    assert meko.asked["V001"]["related"] is None                # week 1: no earlier case to find
    assert meko.learning["before"]["band"] != "high" and meko.learning["after"]["band"] == "high"
    assert meko.learning["outcome"] == "improved"
    # week 2: another account and device, so only a similar case (a new account in the same country)
    assert (meko.asked["V002"]["related"], meko.asked["V002"]["link"]) == ("Nordwind Logistik GmbH",
                                                                          "a new account in Lithuania too")
    block = next(d for d in meko.log if d["vendor_id"] == "V002" and d["outcome"] == "block")
    assert block["related_case"] == "Nordwind Logistik GmbH (a new account in Lithuania too)"
    assert block["risk"] == sum(s.weight for s in {s.type: s for s in block["signals"]}.values())  # no placeholder 0
    assert "siloed" not in [m for m, lane in story.lanes.items() if lane.asked]   # silos have no case history
    # a request that shares nothing with the case (a German account, a device never seen) gets no case at all
    other = Event("X-B1", datetime(2026, 11, 25, 10), "vendor_master", "bank_change_request", "V005",
                  dict(via="portal", new_iban=make_iban(random.Random(1), "DE"),
                       holder=VENDORS["V005"].name, device="dev-000000", country="FR"))
    assert "nothing specific" in meko._ask_about_request(other) and meko.asked["V005"]["related"] is None


def test_a_recorded_run_replays_exactly_and_stops_when_it_goes_off_script(tmp_path):
    rec = Recorder(LocalMeko(), "simulator")
    first = Story(lambda: rec)
    for ch in CHAPTERS:
        first.advance()
        if ch.key == "audit":
            first.promote("V001")
    path = rec.save(tmp_path / "run.json", first.run_id, consortium_on_meko=first.week3_on_meko)
    raw = path.read_text(encoding="utf-8")
    assert "user_email" not in raw and "user_name" not in raw          # only parsed results are kept

    replay = Replayer(path)
    again = Story(lambda: replay, run_id=replay.run_id)
    for ch in CHAPTERS:
        again.advance()
        if ch.key == "audit":
            again.promote("V001")
    meko_a, meko_b = first.lanes["learning2"], again.lanes["learning2"]
    assert again.summary()["learning2"]["lost"] == first.summary()["learning2"]["lost"] == 0
    assert [(d["what"], d["outcome"], d["risk"], d["memory_ids"]) for d in meko_b.log] == \
        [(d["what"], d["outcome"], d["risk"], d["memory_ids"]) for d in meko_a.log]
    assert meko_b.learning == meko_a.learning and len(replay.calls) == len(rec.inner.calls)

    off = Story(lambda: Replayer(path), run_id=replay.run_id)
    with pytest.raises(ReplayMismatch):
        for ch in CHAPTERS:                                         # no promotion this time: week 2 differs
            off.advance()


def test_the_ledger_answers_as_of_queries_and_links_records_to_meko_traces(story):
    ledger = SqliteLedger("t")
    ledger.setup({"V001": VENDORS["V001"]}, since=datetime(2026, 7, 1))
    ledger.contact_changed("V001", "phone", "+49 152 28817386", datetime(2026, 11, 16, 9, 33), "portal")
    assert ledger.value_as_of("V001", "phone", datetime(2026, 11, 16)) == VENDORS["V001"].phone
    assert ledger.value_as_of("V001", "phone", datetime(2026, 11, 17)) == "+49 152 28817386"

    meko = story.lanes["learning2"]
    traces = {d["trace_id"] for d in meko.log}
    payments, changes = story.ledger.table("payments"), story.ledger.table("bank_changes")
    assert len(payments) == sum(len(v) for v in INVOICES.values()) and all(p["trace_id"] in traces for p in payments)
    assert [(c["vendor_id"], c["outcome"]) for c in changes] == [("V001", "blocked"), ("V002", "blocked")]
    assert all(c["trace_id"] in traces for c in changes)
    master = {v["vendor_id"]: v for v in story.ledger.table("vendors")}
    assert master["V001"]["phone"] == "+49 152 28817386"             # the attacker's number is the current one...
    analyst = [st.title for c, out in story.done if not c.act for st in out["learning2"].steps
               if st.agent == "analyst"]
    assert any(VENDORS["V001"].phone in t and "ledger" in t for t in analyst)   # ...the analyst calls the old one


def test_the_committed_real_meko_recording_still_replays():
    """The public demo replays `recordings/`. If a change to the story alters its calls, this fails: record again."""
    path = latest_recording()
    if path is None:
        pytest.skip("no real-Meko recording in recordings/")
    replay = Replayer(path)
    story = Story(lambda: replay, run_id=replay.run_id)
    for ch in CHAPTERS:
        story.advance()
        if ch.key == "audit":
            story.promote("V001")
    assert replay.pos == len(replay.entries)
    assert story.summary()["learning2"]["lost"] == 0 and story.summary()["siloed"]["lost"] == 85250
    net = story.network()                                           # week 3 runs on the simulator after the replay
    assert net["consortium"] < net["asked"] < net["alone"]


def test_week_three_the_consortium_freezes_before_the_crew_asks():
    s = Story(LocalMeko)
    while s.next_chapter.key != "mon3":
        ch = s.next_chapter
        s.advance()
        if ch.key == "audit":
            s.promote("V001")
    before = s.summary()
    while s.next_chapter:
        s.advance()
    assert s.summary() == before                                    # weeks 1-2 untouched, not one more Meko call
    net = s.network()
    assert net["consortium"] == datetime(2026, 11, 30, 13, 20) < net["asked"] == datetime(2026, 12, 1, 10, 5)
    assert net["alone"] == datetime(2026, 12, 1, 15, 40)            # on its own: at the callback, after the ask
    joined, alone = s.act["consortium"], s.act["alone"]
    monday = [ln for st in s.done[-3][1]["consortium"].steps for ln in st.lines]
    assert any("pattern from the Nordwind Logistik GmbH case at Hallberg Components GmbH" in ln for ln in monday)
    known = [w for w in joined.mem.written if w.type == "consortium.known_infrastructure"]
    assert [(w.at, w.agent) for w in known] == [(datetime(2026, 12, 1, 10, 5), "inbox")]   # the mule account
    assert "LT32 0000" in known[0].detail and "Hallberg Components GmbH" in known[0].detail
    assert not [w for w in alone.mem.written if w.type.startswith("consortium.")]
    assert joined.asked["V101"]["related"] == "Nordwind Logistik GmbH"   # the case file shared by Hallberg
    shared = {r["value"]: r["by"] for r in net["rows"]}
    assert len(shared[CREW["V001"]["iban"]]) == 2 and "dev-2b9d57" in shared   # Weller's case went back in

    gone = s.revoke("Hallberg Components GmbH", "Nordwind Logistik GmbH", "test")   # taken back, nothing deleted
    assert gone == ["+49 152 28817386", "dev-7f3a91"]                 # the account stays: Weller confirmed it too
    assert CREW["V001"]["iban"] in joined.consortium and not joined.mem.patterns("inbox")
    rows = {r["value"]: r for r in s.network()["rows"]}
    assert not rows["dev-7f3a91"]["active"] and rows[CREW["V001"]["iban"]]["active"]
    assert s.network()["patterns"] == [] and len(s.network()["rows"]) == len(net["rows"])


def test_a_denied_callback_is_traced_as_a_rejection_without_an_older_search():
    lane, t = Lane("learning2", LocalMeko(), "t"), datetime(2026, 11, 18, 10)
    v = VENDORS["V003"]

    def change(tag, at, result):
        return [Event(f"{tag}1", at, "vendor_master", "bank_change_request", v.id,
                      dict(via="portal", new_iban=make_iban(random.Random(tag), "NL"), holder=v.name,
                           device=v.devices[0], country=v.country)),
                Event(f"{tag}2", at + timedelta(minutes=30), "vendor_master", "callback", v.id,
                      dict(phone=v.phone, phone_source="master", result=result))]

    lane.run_events(Chapter("a", t, "", "", events=change("A", t, "confirmed")))      # searches memory, applies
    lane.run_events(Chapter("b", t, "", "", events=change("B", t + timedelta(days=2), "denied")))
    d = lane.log[-1]
    assert (d["outcome"], d["what"], d["memory_ids"]) == ("reject", "bank change rejected", [])
    assert d["reasoning"].startswith("No memory search for this decision; risk 6")


def _sse(rid, result):
    return f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': rid, 'result': result})}\n\n"


def test_meko_client_speaks_mcp_and_parses_search_results():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer mko_tkn_test"
        assert request.headers["user-agent"].startswith("payee-sentinel")
        body = json.loads(request.content)
        seen.append(body.get("method"))
        if body.get("method") == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "s1", "content-type": "text/event-stream"},
                                  text=_sse(body["id"], {"protocolVersion": "2025-06-18"}))
        if "id" not in body:
            return httpx.Response(202)
        assert request.headers["mcp-session-id"] == "s1"
        name = body["params"]["name"]
        if name == "memory_search":
            payload = {"results": [{"id": "m1", "memory": "vendor V001 changed IBAN", "score": 0.9,
                                    "metadata": {"kind": "signal"}}]}
            text = json.dumps(json.dumps(payload))          # double-encoded, as some Meko tools do
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text=_sse(body["id"], {"content": [{"type": "text", "text": text}]}))
        if name in ("memory_add", "memory_get_all"):
            args = body["params"]["arguments"]
            if name == "memory_add":
                assert isinstance(args["metadata"], str)       # real Meko rejects an object here (8-nov)
                payload = {"results": [{"id": "m2", "memory": args["text"], "event": "ADD"}]}
            else:
                payload = {"promoted_memories": [{"id": "p1", "data": "Signal types: [a, b, c]",
                                                  "promoted_to_kb_at": "2026-11-08T18:21:36Z"}]}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {
                "content": [{"type": "text", "text": json.dumps(payload)}]}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {
            "isError": True, "content": [{"type": "text", "text": "conversation_id Missing required argument"}]}})

    client = MekoClient("mko_tkn_test", transport=httpx.MockTransport(handler))
    hits = client.memory_search("dp", "c1", "payments", "vendor V001")
    assert [(h.id, h.text, h.metadata["kind"]) for h in hits] == [("m1", "vendor V001 changed IBAN", "signal")]
    assert seen[:2] == ["initialize", "notifications/initialized"]
    assert client.memory_add("dp", "c1", "inbox", "signal text", {"kind": "signal"}) == ["m2"]
    [p] = client.collective("dp", "c1", "payments")
    assert (p.id, p.text, p.source) == ("p1", "Signal types: [a, b, c]", "collective")
    with pytest.raises(MekoError, match="conversation_id"):
        client.knowledge_search("dp", "c1", "payments", "policy")


def test_meko_client_sends_trace_metadata_as_text_and_keeps_the_search_grade():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})
        if "id" not in body:
            return httpx.Response(202)
        name, args = body["params"]["name"], body["params"]["arguments"]
        if name == "conversation_add_message":
            assert isinstance(args["metadata"], str) and json.loads(args["metadata"])["outcome"] == "block"
            payload = {"message_id": "msg1"}
        else:
            payload = {"results": {"memory": [], "knowledge_base": [{"id": "k1", "chunk_text": "case file",
                                                                     "rerank_score": 0.97}],
                                   "conversation": {"hits": [], "thread": []}},
                       "match_quality": "mid", "agent_hint": "Partial match: use these results.",
                       "token_savings": {"tokens_saved": 12400}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {
            "content": [{"type": "text", "text": json.dumps(payload)}]}})

    client = MekoClient("mko_tkn_test", transport=httpx.MockTransport(handler))
    assert client.add_message("dp", "c1", "vendor_master", "in", "out", "why", ["a", "b"],
                              metadata={"outcome": "block", "memory_ids": ["m1"]}) == "msg1"
    found = client.context_search("dp", "c1", "vendor_master", "earlier case?")
    assert [h.text for h in found["knowledge_base"]] == ["case file"] and found["memory"] == []
    assert client.last_search["quality"] == "mid" and client.last_search["token_savings"]["tokens_saved"] == 12400


def test_meko_client_refuses_a_missing_key(monkeypatch):
    monkeypatch.delenv("MEKO_API_KEY", raising=False)
    with pytest.raises(MekoError):
        MekoClient("")


def test_week_three_runs_on_the_same_meko_when_recorded_and_replays_exactly(tmp_path):
    """B1: while recording (here on the simulator), Weller's consortium team uses the same store as Hallberg's, with
    its own patterns datapack and the shared one; the replay goes through week 3 call by call and ends the same way."""
    from payee_sentinel.context.replay import Recorder, Replayer

    def run(story):
        for ch in CHAPTERS:
            story.advance()
            if ch.key == "audit":
                story.promote("V001")
        return story

    recorder = Recorder(LocalMeko(), "simulator")
    story = run(Story(lambda: recorder))
    assert story.week3_on_meko and story.act["consortium"].store is recorder
    assert not isinstance(story.act["alone"].store, Recorder)                 # the comparison stays apart
    made = {e["key"] for e in recorder.entries if e["method"] == "ensure_datapack"}
    assert {"consortium", "weller-patterns", "sentinel-signals"} <= made
    net = story.network()
    assert net["consortium"] == datetime(2026, 11, 30, 13, 20) < net["asked"] == datetime(2026, 12, 1, 10, 5)
    path = recorder.save(tmp_path / "run.json", story.run_id, consortium_on_meko=story.week3_on_meko)
    replay = Replayer(path)
    again = run(Story(lambda: replay, run_id=replay.run_id))
    assert again.week3_on_meko and replay.pos == len(replay.entries)
    assert again.network() == net and again.summary() == story.summary()
    at = replay.pos
    gone = again.revoke("Hallberg Components GmbH", "Nordwind Logistik GmbH", "a test")
    assert gone and replay.pos == at                     # applied to what Meko returned: no unrecorded calls
    assert any("taken back" in by for row in again.network()["rows"] for by in row["by"])

