"""The token table (`payee_sentinel/tokens.py`), tested without a key: a fake client counts and answers."""
import json
from types import SimpleNamespace

from payee_sentinel.tokens import demo_items, main


class CountingClient:
    """A token per four characters of the request, and a one-sentence explanation for every question."""

    def __init__(self):
        self.counts, self.created = [], []
        self.messages = SimpleNamespace(count_tokens=self._count)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _count(self, **kwargs):
        self.counts.append(kwargs)
        text = kwargs["system"] + kwargs["messages"][0]["content"] + json.dumps(kwargs.get("output_config", {}))
        return SimpleNamespace(input_tokens=len(text) // 4)

    def _create(self, **kwargs):
        self.created.append(kwargs)
        answer = dict(sentences=[dict(text="The agent stopped it under its rule.", memory_ids=[])])
        return SimpleNamespace(model=kwargs["model"], stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(answer))],
                               usage=SimpleNamespace(input_tokens=len(kwargs["messages"][0]["content"]) // 4,
                                                     output_tokens=200))


def test_without_a_key_nothing_runs(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("payee_sentinel.tokens.load_env", lambda *a, **k: None)
    assert main([]) == 2
    assert "needs ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_with_meko_claude_reads_the_trace_and_without_it_the_raw_records():
    items = demo_items()
    assert [it["label"].split(":")[0] for it in items] == [
        "Hallberg, Tue 17 Nov 16", "Hallberg, Tue 24 Nov 09", "Hallberg, Tue 24 Nov 15",
        "Weller, Mon 30 Nov 13", "Weller, Tue 01 Dec 15"]
    block = items[0]                                     # week 1: the vendor-master agent blocks Nordwind's change
    assert "Signals behind it" in block["facts"]["meko"] and block["ids"]["meko"]
    assert all(m in block["facts"]["meko"] for m in block["ids"]["meko"])
    assert "Signals behind it" not in block["facts"]["vendor"] and "V001-P4" in block["facts"]["vendor"]
    assert block["ids"]["vendor"] < block["ids"]["all"]  # the vendor's records are some of the company's
    assert "V003" not in block["facts"]["vendor"] and "V003" in block["facts"]["all"]


def test_the_table_counts_each_stop_three_ways(tmp_path):
    client = CountingClient()
    assert main(["--out", str(tmp_path / "TOKENS.md"), "--json", str(tmp_path / "t.json")], client=client) == 0
    assert not client.created                            # counting only: nothing is generated, nothing is billed
    assert all(kw["output_config"]["format"]["type"] == "json_schema" for kw in client.counts)
    m = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))
    assert len(m["items"]) == 5 and m["schema_counted"] and not m["runs"]
    assert all(it["tokens"]["meko"] < it["tokens"]["vendor"] <= it["tokens"]["all"] for it in m["items"])
    assert [it["records"]["vendor"] == it["records"]["all"] for it in m["items"]] == [False] * 3 + [True] * 2
    # (in week 3 the demo's Weller has one supplier, so its records are all of the company's)
    assert m["scale"]["vendors"] == 60 and m["scale"]["records"] > m["items"][0]["records"]["all"]
    md = (tmp_path / "TOKENS.md").read_text(encoding="utf-8")
    assert md.count("\n| Hallberg") == 3 and md.count("\n| Weller") == 2 and "token counts only" in md
    assert "Real explanations" not in md


def test_run_explains_each_stop_with_and_without_meko_under_the_cap(tmp_path):
    client = CountingClient()
    args = ["--run", "--out", str(tmp_path / "TOKENS.md"), "--json", str(tmp_path / "t.json")]
    assert main(args, client=client) == 0
    assert len(client.created) == 10                     # 5 decisions x (with Meko, the vendor's records)
    assert {kw["system"][:40] for kw in client.created} == {"You explain one decision of an accounts-"}
    m = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))
    assert [r["kept"] for r in m["runs"]] == [1] * 10 and m["spent"] > 0
    assert "## Real explanations" in (tmp_path / "TOKENS.md").read_text(encoding="utf-8")
    capped = CountingClient()
    assert main(args + ["--max-dollars", "0"], client=capped) == 0
    assert not capped.created
    runs = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))["runs"]
    assert len(runs) == 10 and all(r["note"].startswith("skipped") for r in runs)
