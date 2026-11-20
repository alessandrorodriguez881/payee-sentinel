"""A model in the agents, tested without a key: a fake client stands in for `anthropic.Anthropic`, and a mock
transport for Groq."""
import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from payee_sentinel.context.local import LocalMeko
from payee_sentinel.reader import GROQ_MODEL, GROQ_URL, MODEL, GroqReader, Reader
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


class JobClient(FakeClient):
    """Flags for an email, sentences for an explanation: the answer follows the schema of the request."""

    def __init__(self, flags, sentences):
        super().__init__()
        self.flags, self.sentences = flags, sentences

    def _create(self, **kwargs):
        props = kwargs["output_config"]["format"]["schema"]["properties"]
        self.payload = dict(flags=self.flags) if "flags" in props else dict(sentences=self.sentences)
        return super()._create(**kwargs)


def _with_claude(story):
    for ch in CHAPTERS:
        story.advance()
        if ch.key == "audit":
            story.promote("V001")
    return story


def test_in_the_demo_claude_reads_and_explains_and_the_decisions_do_not_change():
    flags = [dict(kind="payment_change", quote="please pay all open invoices to:", why="New bank details.")]
    sentences = [dict(text="The vendor-master agent blocked the bank change.", memory_ids=[])]
    client = JobClient(flags, sentences)
    with_claude = _with_claude(Story(LocalMeko, reader=Reader(client)))
    without = _with_claude(Story(LocalMeko))
    assert with_claude.summary()["learning2"]["lost"] == without.summary()["learning2"]["lost"] == 0
    meko_a, meko_b = with_claude.lanes["learning2"], without.lanes["learning2"]
    assert [(d["what"], d["outcome"], d["risk"]) for d in meko_a.log] == \
        [(d["what"], d["outcome"], d["risk"]) for d in meko_b.log]            # Claude never decides
    tuesday = dict(with_claude.done[1][1])
    inbox = [ln for st in tuesday["learning2"].steps if st.agent == "inbox" for ln in st.lines]
    assert any(ln.startswith("Claude Opus 5.5 reads it: payment change") for ln in inbox)
    assert not any("Claude" in ln for st in tuesday["siloed"].steps for ln in st.lines)   # the Meko lane only
    audit = next(out for ch, out in with_claude.done if ch.key == "audit")
    assert audit["learning2"].claude == ["The vendor-master agent blocked the bank change."]
    assert {u["name"] for u in meko_a.store.token_usage} == {"inbox: read email", "auditor: explain"}


def test_a_replay_with_claude_still_matches_the_recording():
    from payee_sentinel.context.replay import Replayer, latest_recording
    path = latest_recording()
    if path is None:
        pytest.skip("no real-Meko recording in recordings/")
    replay = Replayer(path)
    flags = [dict(kind="pressure", quote="Please update this before Friday's payment run.", why="A deadline.")]
    story = _with_claude(Story(lambda: replay, run_id=replay.run_id, reader=Reader(JobClient(flags, []))))
    assert replay.pos == len(replay.entries) and story.reader.store is None   # no token calls in a replay


def test_the_installed_sdk_sends_and_reads_what_reader_expects():
    """The path a real key will take, checked offline: the anthropic SDK builds Reader's request (beta header,
    server-side fallbacks, the answer's schema, effort) and parses a reply, through a mock transport. Token counting
    goes the same way."""
    import httpx2                                       # the HTTP library this SDK version is built on
    seen = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(dict(path=request.url.path, beta=request.headers.get("anthropic-beta", ""),
                         body=json.loads(request.content)))
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json={"input_tokens": 321})
        answer = dict(sentences=[dict(text="The vendor-master agent blocked the bank change.", memory_ids=[])])
        return httpx2.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": MODEL,
            "content": [{"type": "thinking", "thinking": "", "signature": "x"},
                        {"type": "text", "text": json.dumps(answer)}],
            "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 900, "output_tokens": 120}})

    client = anthropic.Anthropic(api_key="test-key", max_retries=0,
                                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    story = Story(LocalMeko)
    for ch in CHAPTERS[:2]:
        story.advance()
    block = next(d for d in story.lanes["learning2"].log if d["outcome"] == "block")
    result = Reader(client).explain(block, VENDORS["V001"].name)
    assert [s["text"] for s in result.items] == ["The vendor-master agent blocked the bank change."]
    assert result.usage == dict(model=MODEL, input_tokens=900, output_tokens=120) and not result.note
    req = seen[0]
    assert req["path"] == "/v1/messages" and "server-side-fallback-2026-07-01" in req["beta"]
    assert req["body"]["fallbacks"] == "default" and req["body"]["model"] == "claude-opus-5-5"
    assert req["body"]["output_config"]["format"]["type"] == "json_schema"
    assert req["body"]["output_config"]["effort"] == "medium" and req["body"]["max_tokens"] == 4000
    counted = client.messages.count_tokens(model=MODEL, system="s", messages=[{"role": "user", "content": "u"}],
                                           output_config={"format": {"type": "json_schema", "schema": {}}})
    assert counted.input_tokens == 321 and seen[1]["path"] == "/v1/messages/count_tokens"


def test_without_a_key_there_is_no_reader(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert Reader.from_env() is None


def _groq(handler, waits=None):
    """A Groq reader whose requests go to `handler`, and whose waits (on a 429) are only written down."""
    return GroqReader(httpx.Client(transport=httpx.MockTransport(handler)), key="gsk_test",
                      wait=waits.append if waits is not None else (lambda seconds: None))


def _reply(payload, finish="stop", **message):
    return httpx.Response(200, json={"model": GROQ_MODEL, "usage": {"prompt_tokens": 700, "completion_tokens": 90},
                                     "choices": [{"finish_reason": finish, "message": {
                                         "role": "assistant", "content": json.dumps(payload), **message}}]})


def test_groq_asks_for_a_strict_schema_and_its_flags_pass_the_same_quote_check():
    seen = []

    def handler(request):
        seen.append(request)
        return _reply({"flags": [
            dict(kind="payment_change", quote="please pay all open invoices to:", why="New bank details."),
            dict(kind="impersonation", quote="We are your new finance partner", why="Invented.")]})

    res = _groq(handler).read_email("Nordwind Logistik GmbH", "nordwind-logistik.example", _email())
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == GROQ_URL and seen[0].headers["authorization"] == "Bearer gsk_test"
    assert body["model"] == GROQ_MODEL and body["temperature"] == 0
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert [f.kind for f in res.items] == ["payment_change"] and len(res.dropped) == 1    # the invented quote
    assert res.usage == dict(model=GROQ_MODEL, input_tokens=700, output_tokens=90) and res.note == ""


def test_groq_waits_on_its_free_rate_limit_and_tries_again():
    calls, waits = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "2"}) if len(calls) < 3 else _reply({"flags": []})

    res = _groq(handler, waits).read_email("Nordwind Logistik GmbH", "nordwind-logistik.example", _email())
    assert len(calls) == 3 and waits == [2.0, 2.0] and res.items == [] and res.note == ""


def _broken(request):
    raise httpx.ConnectError("offline", request=request)


@pytest.mark.parametrize("handler, note", [
    (lambda request: httpx.Response(429), "Groq's free rate limit was reached"),
    (lambda request: httpx.Response(500), "Groq returned an error (500)"),
    (_broken, "Groq could not be reached"),
    (lambda request: _reply({"flags": []}, finish="length"), "the model's answer was cut off"),
    (lambda request: _reply({}, refusal="I can't help with that."), "the model declined to answer"),
    (lambda request: httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "oops"}}]}),
     "the model's answer was not valid JSON"),
])
def test_groq_problems_leave_no_flags_and_say_why(handler, note):
    res = _groq(handler).read_email("Nordwind Logistik GmbH", "nordwind-logistik.example", _email())
    assert res.items == [] and res.note == note


def test_groq_explains_and_the_trace_check_still_drops_what_the_trace_does_not_hold():
    facts = ("Decision: block the bank change (outcome block) by the vendor_master agent.\nRisk score: 19.\n"
             "- 5e7d2b59aa11 | Tue 17 Nov 16:05 | inbox | email.freemail_sender | +2 | Email from mail.test.")
    sentences = [dict(text="The agent blocked the change at risk 19.", memory_ids=["5e7d2b59"]),
                 dict(text="An earlier case agreed.", memory_ids=["deadbeef"]),
                 dict(text="It saved 42 euros.", memory_ids=[])]
    res = _groq(lambda request: _reply({"sentences": sentences})).explain_facts(facts, {"5e7d2b59aa11"}, "V001")
    assert [s["text"] for s in res.items] == ["The agent blocked the change at risk 19."] and len(res.dropped) == 2


def test_in_the_demo_groq_reads_with_its_own_name_and_never_decides():
    flags = [dict(kind="payment_change", quote="please pay all open invoices to:", why="New bank details.")]

    def handler(request):
        schema = json.loads(request.content)["response_format"]["json_schema"]["schema"]
        return _reply({"flags": flags} if "flags" in schema["properties"] else
                      {"sentences": [dict(text="The vendor-master agent blocked the bank change.", memory_ids=[])]})

    with_groq = _with_claude(Story(LocalMeko, reader=_groq(handler)))
    without = _with_claude(Story(LocalMeko))
    assert [(d["what"], d["outcome"], d["risk"]) for d in with_groq.lanes["learning2"].log] == \
        [(d["what"], d["outcome"], d["risk"]) for d in without.lanes["learning2"].log]
    tuesday = dict(with_groq.done[1][1])
    inbox = [ln for st in tuesday["learning2"].steps if st.agent == "inbox" for ln in st.lines]
    assert any(ln.startswith("gpt-oss on Groq reads it: payment change") for ln in inbox)


def test_the_free_groq_key_is_used_first_and_the_choice_can_be_forced(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("PAYEE_SENTINEL_LLM", raising=False)
    assert isinstance(Reader.from_env(), GroqReader)
    monkeypatch.setenv("PAYEE_SENTINEL_LLM", "claude")
    assert type(Reader.from_env()) is Reader
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert Reader.from_env() is None                    # Claude asked for, and no Claude key
    monkeypatch.setenv("PAYEE_SENTINEL_LLM", "groq")
    assert isinstance(Reader.from_env(), GroqReader)


def _groq_jobs(request):
    """Groq's answer to either job: one flag the email holds, or one plain sentence."""
    schema = json.loads(request.content)["response_format"]["json_schema"]["schema"]
    if "flags" in schema["properties"]:
        return _reply({"flags": [dict(kind="payment_change", quote="please pay all open invoices to:",
                                      why="New bank details.")]})
    return _reply({"sentences": [dict(text="The vendor-master agent blocked the bank change.", memory_ids=[])]})


def _shown(story):
    lines = []
    for ch, out in story.done:
        if "learning2" in out:
            lines += [ln for st in out["learning2"].steps for ln in st.lines] + out["learning2"].claude
    return [ln.replace(" (recorded answers)", "") for ln in lines]


def test_a_run_recorded_with_the_model_replays_its_answers_with_no_key(tmp_path):
    from payee_sentinel.context.replay import Recorder, Replayer
    from payee_sentinel.reader import AnswerRecorder, ReplayReader
    reader = _groq(_groq_jobs)
    answers = AnswerRecorder(reader)
    recorder = Recorder(LocalMeko(), "simulator")
    recorded = _with_claude(Story(lambda: recorder, reader=reader))
    path = recorder.save(tmp_path / "run.json", recorded.run_id, llm=answers.dump(),
                         consortium_on_meko=recorded.week3_on_meko)
    replay = Replayer(path)
    again = _with_claude(Story(lambda: replay, run_id=replay.run_id, reader=ReplayReader.from_recording(path)))
    assert replay.pos == len(replay.entries)                  # every Meko call, the token reports included
    assert again.reader.pos == len(answers.answers) >= 2      # each email read and the explanation, in order
    assert again.reader.label == "gpt-oss on Groq (recorded answers)"
    assert _shown(again) == _shown(recorded) and any("gpt-oss on Groq reads it" in ln for ln in _shown(again))


def test_recorded_answers_answer_only_the_recorded_request(tmp_path):
    from payee_sentinel.reader import ReplayReader
    reader = ReplayReader(dict(label="gpt-oss on Groq", model=GROQ_MODEL,
                               answers=[dict(name="inbox: read email", digest="0" * 16, data={"flags": []}, usage={},
                                             note="")]))
    res = reader.read_email("Nordwind Logistik GmbH", "nordwind-logistik.example", _email())
    assert res.items == [] and res.note == "this request is not the recorded one"
    assert ReplayReader.from_recording(None) is None

