"""Smoke test of the demo screen: click through the whole story offline, approve the pattern, read the summary."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _button(at, starts):
    return next(b for b in at.button if b.label.startswith(starts))


def test_click_through_the_story_with_a_promotion(monkeypatch):
    monkeypatch.delenv("MEKO_API_KEY", raising=False)
    monkeypatch.setattr("payee_sentinel.context.load_env", lambda *a, **k: None)
    monkeypatch.setattr("payee_sentinel.context.replay.latest_recording", lambda *a, **k: None)   # the simulator
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    ends = {"Agents in silos (today)": "€85,250", "Sharing memory in Meko": "€0",
            "Week 3 (simulated)": "21 h early"}   # the first screen tells where the story ends: checked below
    assert {m.label: m.value for m in at.metric if m.label in ends} == ends
    for step in range(4):                                # Monday, Tuesday, payment run, audit
        _button(at, "Next:").click()
        at.run()
        assert not at.exception
        if step == 1:                                    # Tuesday: the vendor-master agent blocks the change
            assert any("Bank change blocked" in m.value for m in at.markdown)
    assert any("bank change blocked." in m.value for m in at.markdown)   # the auditor's explanation
    assert any("**mid → high**" in m.value for m in at.markdown)       # Meko's self-learning loop fixed a search
    assert at.selectbox[0].label == "Open a decision's trace"
    _button(at, "Promote to collective memory").click()  # a person approves the pattern
    at.run()
    assert any("Promoted to Meko's collective memory" in s.value for s in at.success)
    while at.subheader[0].value != "Two weeks later":
        _button(at, "Next:").click()
        at.run()
        assert not at.exception
    metrics = {m.label + str(i): m.value for i, m in enumerate(at.metric) if m.label == "Sent to the attackers"}
    assert sorted(metrics.values()) == ["€0", "€85,250"]
    assert any("Bergmann Verpackung GmbH: stopped Tue 24 Nov 09:05" in m.value for m in at.markdown)
    _button(at, "Next:").click()                         # week 3, Monday: another company in the consortium
    at.run()
    assert any("bank details frozen by an approved pattern (Mon 13:20)" in s.value for s in at.success)
    for _ in range(2):                                   # Tuesday, then what the consortium shares
        _button(at, "Next:").click()
        at.run()
        assert not at.exception
    assert any("frozen Mon 30 Nov 13:20, 21 hours before the attacker asked" in m.value for m in at.markdown)
    assert _button(at, "End of the story").disabled
    _button(at, "Take it back").click()                  # a member takes a case back
    at.run()
    assert not at.exception
    assert any("took back the Nordwind Logistik GmbH case: 2 values left the shared list" in i.value for i in at.info)


def test_the_public_demo_opens_on_the_real_meko_recording(monkeypatch):
    """What a visitor without a key gets: the replay of the run recorded on real Meko, from the first screen's Start
    button to the end of week 3."""
    monkeypatch.delenv("MEKO_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("payee_sentinel.context.load_env", lambda *a, **k: None)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert at.radio[0].value == "Replay of a recorded run"
    assert any("Replay of a real Meko run recorded 2026-11-09" in c.value for c in at.caption)
    assert any("You are watching a replay of a run recorded on real Meko" in c.value for c in at.caption)
    assert any("Ledger: a local SQLite copy" in c.value for c in at.caption)   # no Postgres details, no problem
    _button(at, "Start on Monday morning").click()
    at.run()
    assert not at.exception
    assert at.subheader[0].value == "Monday · the supplier portal"
    for _ in range(3):                                   # Tuesday, payment run, audit
        _button(at, "Next:").click()
        at.run()
        assert not at.exception
    assert any("**low → mid**" in m.value for m in at.markdown)        # what real Meko's self-learning loop did
    assert _button(at, "Next:").disabled                 # in the recorded run a person promoted the pattern here
    _button(at, "Promote to collective memory").click()
    at.run()
    while (b := next((b for b in at.button if b.label.startswith("Next:")), None)) and not b.disabled:
        b.click()
        at.run()
        assert not at.exception
        if at.subheader[0].value == "Two weeks later":
            assert sorted(m.value for m in at.metric if m.label == "Sent to the attackers") == ["€0", "€85,250"]
    assert at.subheader[0].value == "What the consortium shares"
    assert any("frozen Mon 30 Nov 13:20, 21 hours before the attacker asked" in m.value for m in at.markdown)


def test_the_demo_replays_a_recorded_run(monkeypatch, tmp_path):
    from payee_sentinel.context.local import LocalMeko
    from payee_sentinel.context.replay import Recorder
    from payee_sentinel.story import CHAPTERS, Story

    rec = Recorder(LocalMeko(), "simulator")
    first = Story(lambda: rec)
    for ch in CHAPTERS:
        first.advance()
        if ch.key == "audit":
            first.promote("V001")
    path = rec.save(tmp_path / "run.json", first.run_id)
    monkeypatch.delenv("MEKO_API_KEY", raising=False)
    monkeypatch.setattr("payee_sentinel.context.load_env", lambda *a, **k: None)
    monkeypatch.setattr("payee_sentinel.context.replay.latest_recording", lambda *a, **k: path)
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio[0].set_value("Replay of a recorded run").run()
    assert any("Replay of a simulator run recorded" in c.value for c in at.caption)
    for _ in range(4):                                   # Monday, Tuesday, payment run, audit
        _button(at, "Next:").click()
        at.run()
        assert not at.exception
    assert _button(at, "Next:").disabled                 # the recorded run had a promotion here
    _button(at, "Promote to collective memory").click()
    at.run()
    while at.subheader[0].value != "Two weeks later":
        _button(at, "Next:").click()
        at.run()
        assert not at.exception
    metrics = [m.value for m in at.metric if m.label == "Sent to the attackers"]
    assert sorted(metrics) == ["€0", "€85,250"]
    while (b := next((b for b in at.button if b.label.startswith("Next:")), None)) and not b.disabled:
        b.click()                                        # week 3 runs on the simulator after the replay
        at.run()
        assert not at.exception
