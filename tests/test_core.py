"""Checks that need no network: the world, the Meko simulator's rules, the memory modes and the real client's parsing."""
from datetime import datetime, timedelta
import json

import httpx
import pytest

from payee_sentinel.agents import Memory, Signal, pattern_match, risk_of
from payee_sentinel.context.base import Hit
from payee_sentinel.context.local import LocalMeko
from payee_sentinel.context.meko import MekoClient, MekoError
from payee_sentinel.evaluate import carry_learning, run, run_chain, summarize
from payee_sentinel.scenario import generate, iban_is_valid
from payee_sentinel.story import CHAPTERS, INVOICES, Story


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


def test_self_learning_loop_fixes_the_weak_search_and_week_two_finds_the_case(story):
    meko = story.lanes["learning2"]
    assert meko.asked["V001"]["related"] is None                # week 1: no earlier case to find
    assert meko.learning["before"]["band"] != "high" and meko.learning["after"]["band"] == "high"
    assert meko.learning["outcome"] == "improved"
    assert meko.asked["V002"]["related"] == "Nordwind Logistik GmbH"
    block = next(d for d in meko.log if d["vendor_id"] == "V002" and d["outcome"] == "block")
    assert block["related_case"] == "Nordwind Logistik GmbH"
    assert "siloed" not in [m for m, lane in story.lanes.items() if lane.asked]   # silos have no case history


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
