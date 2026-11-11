"""Claude in the agents, tested without a key: a fake client stands in for `anthropic.Anthropic`."""
import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from payee_sentinel.context.local import LocalMeko
from payee_sentinel.reader import MODEL, Reader
from payee_sentinel.story import CHAPTERS, VENDORS, Story


class FakeClient:
    """Answers every request with `payload` as JSON (after an empty thinking block, as Claude Opus 5.5 does)."""

    def __init__(self, payload=None, stop_reason="end_turn", error=None):
        self.payload, self.stop_reason, self.error, self.requests = payload, stop_reason, error, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(model=kwargs["model"], stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="thinking", thinking=""),
                                        SimpleNamespace(type="text", text=json.dumps(self.payload))],
                               usage=SimpleNamespace(input_tokens=812, output_tokens=164))


def _email():
    tue1 = next(c for c in CHAPTERS if c.key == "tue1")
    return next(e for e in tue1.events if e.kind == "email").data


def test_read_email_keeps_only_quotes_that_are_in_the_email():
    flags = [
        dict(kind="payment_change", quote="please pay all open invoices to:", why="New bank details."),
        dict(kind="pressure", quote="Please   update this before Friday's payment run.", why="A deadline."),
        dict(kind="impersonation", quote="We are your new finance partner", why="Invented."),
        dict(kind="other", quote="Dear", why="Too short."),
    ]
    client, store = FakeClient(dict(flags=flags)), LocalMeko()
    dp = store.ensure_datapack("sentinel-signals")
    conv = store.conversation_create(dp, "inbox", "inbox agent session")
    v = VENDORS["V001"]
    result = Reader(client, store=store, datapack=dp).read_email(v.name, v.domain, _email(), conv)
    assert [f.kind for f in result.items] == ["payment_change", "pressure"]   # spacing differences are tolerated
    assert [why for _, why in result.dropped] == ["the quote is not in the email",
                                                  "the quote is too short to prove anything"]
    req = client.requests[0]
    assert req["model"] == MODEL == "claude-opus-5-5" and req["fallbacks"] == "default"
    assert req["output_config"]["format"]["type"] == "json_schema" and req["output_config"]["effort"] == "medium"
    assert "<email>" in req["messages"][0]["content"] and "IBAN:" in req["messages"][0]["content"]
    assert store.token_usage == [dict(conversation_id=conv, name="inbox: read email", input_tokens=812,
                                      output_tokens=164, model="claude-opus-5-5")]   # in Meko, with the trace


def test_a_refusal_or_an_api_error_leaves_no_flags_and_does_not_raise():
    v = VENDORS["V001"]
    refused = Reader(FakeClient(dict(flags=[]), stop_reason="refusal")).read_email(v.name, v.domain, _email())
    assert refused.items == [] and refused.note == "Claude declined to answer"
    down = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    failed = Reader(FakeClient(error=down)).read_email(v.name, v.domain, _email())
    assert failed.items == [] and failed.note == "Claude could not be reached" and failed.usage == {}


def test_explain_drops_sentences_with_memories_or_numbers_the_trace_does_not_hold():
    story = Story(LocalMeko)
    for ch in CHAPTERS[:2]:                                  # Monday and Tuesday: the vendor-master agent blocks
        story.advance()
    lane = story.lanes["learning2"]
    block = next(d for d in lane.log if d["vendor_id"] == "V001" and d["outcome"] == "block")
    real = block["signals"][0].memory_id
    sentences = [
        dict(text=f"The vendor-master agent blocked the change: risk {block['risk']} is over the limit.",
             memory_ids=[]),
        dict(text="The phone the clerk called had been put on file by the attacker the day before.",
             memory_ids=[real[:8]]),
        dict(text="Risk 25 is far above the limit.", memory_ids=[]),
        dict(text="The inbox agent saw a look-alike domain.", memory_ids=["deadbeef"]),
    ]
    result = Reader(FakeClient(dict(sentences=sentences))).explain(block, VENDORS["V001"].name)
    assert [s["text"][:24] for s in result.items] == ["The vendor-master agent ", "The phone the clerk call"]
    assert [why.split(":")[0] for _, why in result.dropped] == ["uses numbers the trace does not hold",
                                                                "cites memories the trace does not hold"]
    facts = Reader.facts(block, VENDORS["V001"].name)
    assert real in facts and f"Risk score: {block['risk']}." in facts


def test_without_a_key_there_is_no_reader(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert Reader.from_env() is None
