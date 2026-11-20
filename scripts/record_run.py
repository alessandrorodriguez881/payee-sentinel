"""Record one run of the demo story, call by call, so the demo can replay it without a key.

    python scripts/record_run.py --real      # real Meko: run once, on empty demo datapacks (see docs/MEKO_SMOKE.md)
    python scripts/record_run.py             # the offline simulator, to check the mechanism

With a model key in `.env` (`GROQ_API_KEY`, free, or `ANTHROPIC_API_KEY`), the model reads the emails and explains
the decisions during the run, and its answers are saved in the recording too, so the demo replays them with no key.

The real recording goes to `recordings/real-meko-<date>.json`, which the demo offers as "replay of a real Meko run".
A person promotes the Nordwind pattern at the audit chapter, as in the demo. Prints no personal data.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from payee_sentinel.context import load_env  # noqa: E402
from payee_sentinel.context.local import LocalMeko  # noqa: E402
from payee_sentinel.context.replay import RECORDINGS, Recorder  # noqa: E402
from payee_sentinel.reader import AnswerRecorder, Reader  # noqa: E402
from payee_sentinel.story import CHAPTERS, Story  # noqa: E402


def record(real: bool, out: Path | None = None, reader: Reader | None = None) -> tuple[Story, Path]:
    load_env()
    reader = reader or Reader.from_env()
    answers = AnswerRecorder(reader) if reader is not None else None
    if real:
        from payee_sentinel.context.meko import MekoClient
        recorder = Recorder(MekoClient(), "real Meko")
    else:
        recorder = Recorder(LocalMeko(), "simulator")
    story = Story(lambda: recorder, reader=reader)
    for ch in CHAPTERS:
        story.advance()
        if ch.key == "audit":
            story.promote("V001")
    name = f"{'real-meko' if real else 'simulator'}-{date.today():%Y-%m-%d}-{story.run_id}.json"
    extra = dict(consortium_on_meko=story.week3_on_meko) | (dict(llm=answers.dump()) if answers is not None else {})
    return story, recorder.save(out or RECORDINGS / name, story.run_id, **extra)


if __name__ == "__main__":
    story, path = record("--real" in sys.argv)
    s = story.summary()
    print(f"saved {path.relative_to(ROOT)}: run {story.run_id}, lost {s['siloed']['lost']:,.0f} (silos) vs "
          f"{s['learning2']['lost']:,.0f} (Meko), {len(story.lanes['learning2'].store.entries)} calls recorded")
