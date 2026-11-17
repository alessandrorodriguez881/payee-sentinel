"""One pass over every Meko feature Payee Sentinel uses, against the real service, printing the raw replies.

Run once the key is in `.env`:   python scripts/meko_smoke.py
It works in its own datapack, `sentinel-smoke` (the Free plan allows 10 datapacks and deleting one does not give the
slot back), makes about 15 calls, and never prints the key. Some replies include the account's name and e-mail:
summarise them in docs/MEKO_SMOKE.md, do not paste them.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from payee_sentinel.context import load_env  # noqa: E402
from payee_sentinel.context.meko import MekoClient, MekoError, _items  # noqa: E402


def show(label: str, value) -> None:
    text = json.dumps(value, default=lambda o: getattr(o, "__dict__", str(o)), indent=1)
    print(f"\n=== {label}\n{text[:1500]}")


def main() -> None:
    load_env()
    m = MekoClient()
    dp = m.ensure_datapack("sentinel-smoke", "Payee Sentinel smoke test")
    show("datapack id", dp)
    cid = m.conversation_create(dp, "inbox", "smoke test")
    show("conversation id", cid)
    steps = [
        ("add_message with reasoning and plan", lambda: m.add_message(
            dp, cid, "inbox", "Email from accounts@nordwind-logistik.example asks to change the IBAN.",
            "Flagged: bank-change request, urgency.", reasoning="Bank words and urgency in the same email.",
            plan=["search memory for the vendor", "write signals"])),
        ("memory_add with metadata", lambda: m.memory_add(
            dp, cid, "inbox", "Risk signal email.bank_change_request for vendor V001 Nordwind Logistik GmbH, weight 1.",
            {"kind": "signal", "type": "email.bank_change_request", "vendor_id": "V001", "at": "2026-10-13T16:05:00",
             "weight": 1, "agent": "inbox", "evidence": ["E00001"]})),
        ("memory_search (raw, another agent)", lambda: m._tool("payments", "memory_search", dict(
            datapack_id=dp, conversation_id=cid, agent_id="payments",
            query="risk signals for vendor V001 Nordwind Logistik GmbH", limit=10))),
        ("context_search (raw)", lambda: m._tool("payments", "context_search", dict(
            datapack_id=dp, conversation_id=cid, agent_id="payments", query="Nordwind IBAN change", limit=5))),
        ("knowledge upload", lambda: m.knowledge_upload(dp, "payment-policy.md", (
            b"# Payment policy\n\nA bank-change request is confirmed by calling the phone number that was on file "
            b"at least 60 days before the request.\n"), cid)),
        ("collective memory (promoted)", lambda: [h.__dict__ for h in m.collective(dp, cid, "payments")]),
        ("learnings waiting", lambda: [h.__dict__ for h in m.list_learnings(dp)]),
        ("artifact_put", lambda: m.artifact_put(dp, cid, "auditor", "case.md", b"# Case\nheld", "text/markdown")),
        ("track_token_usage", lambda: m.track_tokens(dp, cid, "inbox", 120, 40, "claude-sonnet-5-5")),
    ]
    for label, step in steps:
        try:
            show(label, step())
        except (MekoError, Exception) as exc:  # keep going: the point is to see every reply
            show(label + " FAILED", repr(exc)[:800])
    try:
        raw = m._tool("payments", "memory_search", dict(datapack_id=dp, conversation_id=cid, agent_id="payments",
                                                         query="Nordwind", limit=5))
        ids = [i["id"] for i in _items(raw) if i.get("id")][:1]
        show("promote one memory", m.promote(dp, ids) if ids else "no memory id found to promote")
    except Exception as exc:
        show("promote FAILED", repr(exc)[:800])
    show("calls made", [c.__dict__ for c in m.calls])


if __name__ == "__main__":
    main()
