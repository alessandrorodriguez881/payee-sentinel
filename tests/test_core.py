"""Checks that need no network: the world, the Meko simulator's rules, the memory modes and the real client's parsing."""
from datetime import datetime, timedelta
import json

import httpx
import pytest

from payee_sentinel.agents import Memory, Signal, pattern_match, risk_of
from payee_sentinel.context.base import Hit
from payee_sentinel.context.local import LocalMeko
from payee_sentinel.context.meko import MekoClient, MekoError
from payee_sentinel.evaluate import run, summarize
from payee_sentinel.scenario import generate, iban_is_valid


@pytest.fixture(scope="module")
def world():
    return generate(1)


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
    assert hits["memory"][0].source == "collective"
    assert hits["knowledge_base"][0].id == mid           # collective memory is part of shared knowledge


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
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {
            "isError": True, "content": [{"type": "text", "text": "conversation_id Missing required argument"}]}})

    client = MekoClient("mko_tkn_test", transport=httpx.MockTransport(handler))
    hits = client.memory_search("dp", "c1", "payments", "vendor V001")
    assert [(h.id, h.text, h.metadata["kind"]) for h in hits] == [("m1", "vendor V001 changed IBAN", "signal")]
    assert seen[:2] == ["initialize", "notifications/initialized"]
    with pytest.raises(MekoError, match="conversation_id"):
        client.knowledge_search("dp", "c1", "payments", "policy")


def test_meko_client_refuses_a_missing_key(monkeypatch):
    monkeypatch.delenv("MEKO_API_KEY", raising=False)
    with pytest.raises(MekoError):
        MekoClient("")
