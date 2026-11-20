"""Record one run of the demo against a context store, and replay it later without a key.

The public demo cannot spend the Meko account's quota on every visitor, and judges have no key. So one clean run on
real Meko is recorded call by call (`Recorder`), and the demo replays it (`Replayer`): the same calls, in the same
order, get the answers real Meko gave, with their original latencies, and the screen says it is a replay. The story is
deterministic, so a replay that receives a different call from the one recorded stops with `ReplayMismatch` instead of
making an answer up.

Only the parsed results the agents use are kept (memories, hits, ids, grades), never Meko's raw replies, which carry
the account's name and e-mail.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from .base import Call, Hit, now_iso

METHODS = ("ensure_datapack", "conversation_create", "add_message", "memory_add", "memory_search", "context_search",
           "collective", "list_learnings", "promote", "knowledge_upload", "knowledge_ready", "knowledge_search",
           "artifact_put", "track_tokens", "selflearning_status", "search_log", "selflearning_verify")
KEEP = {"selflearning_status": ("quality",),                    # what the story reads from each raw reply
        "search_log": ("id", "band", "query", "searched_at", "at", "best_score"),
        "selflearning_verify": ("before", "after", "outcome")}
RECORDINGS = Path(__file__).resolve().parents[2] / "recordings"


class ReplayMismatch(RuntimeError):
    pass


def _dump(x: Any) -> Any:
    if isinstance(x, Hit):
        return {"__hit__": asdict(x)}
    if isinstance(x, (list, tuple)):
        return [_dump(i) for i in x]
    if isinstance(x, dict):
        return {k: _dump(v) for k, v in x.items()}
    return x


def _load(x: Any) -> Any:
    if isinstance(x, dict) and "__hit__" in x:
        return Hit(**x["__hit__"])
    if isinstance(x, list):
        return [_load(i) for i in x]
    if isinstance(x, dict):
        return {k: _load(v) for k, v in x.items()}
    return x


def _key(method: str, args: tuple, kwargs: dict) -> str:
    """A fingerprint of the call, enough to notice a replay going off script: the query of a search, the start of a
    memory's text, a datapack's name."""
    if method == "search_log":                        # (datapack_id, conversation_id, query, since=...)
        return str(kwargs.get("query", args[2] if len(args) > 2 else ""))
    named = dict(zip(("datapack_id", "conversation_id", "agent_id", "query"), args)) | kwargs
    if method in ("memory_search", "context_search", "knowledge_search"):
        return str(named.get("query", ""))
    if method == "memory_add":
        text = kwargs.get("text", args[3] if len(args) > 3 else "")
        return str(text)[:80]
    if method == "ensure_datapack":
        return str(kwargs.get("name", args[0] if args else ""))
    return ""


def _trim(method: str, result: Any) -> Any:
    if method == "knowledge_upload" and isinstance(result, str):
        return result.rsplit("/", 1)[-1]          # the storage key starts with the account's id: keep the file name
    keep = KEEP.get(method)
    if keep and isinstance(result, dict):
        return {k: v for k, v in result.items() if k in keep}
    return result


class Recorder:
    """Wraps a store (real Meko, or the simulator in tests) and keeps every call the story makes."""

    def __init__(self, inner, engine: str):
        self.inner, self.engine, self.entries = inner, engine, []

    def __getattr__(self, name: str):
        attr = getattr(self.inner, name)
        if name not in METHODS:
            return attr

        def call(*args, **kwargs):
            before = len(self.inner.calls)
            result = attr(*args, **kwargs)
            self.entries.append(dict(
                method=name, key=_key(name, args, kwargs), result=_dump(_trim(name, result)),
                calls=[asdict(c) for c in self.inner.calls[before:]],
                last_search=dict(getattr(self.inner, "last_search", None) or {}) if name == "context_search" else None))
            return result
        return call

    def save(self, path: Path, run_id: str, **extra) -> Path:
        """`extra` goes in the file too: the model's recorded answers, under `llm`."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(engine=self.engine, run_id=run_id, recorded_at=now_iso(),
                                        entries=self.entries, **extra), indent=1, default=str), encoding="utf-8")
        return path


class Replayer:
    """Answers the story's calls with a recording, in order, and refuses any call that was not recorded next."""

    def __init__(self, path: Path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.engine, self.run_id, self.recorded_at = data["engine"], data["run_id"], data["recorded_at"]
        self.consortium_on_meko = bool(data.get("consortium_on_meko"))   # week 3 is in the recording too
        self.entries, self.pos = data["entries"], 0
        self.calls: list[Call] = []
        self.last_search: dict = {}
        self.datapacks: dict[str, str] = {}

    def __getattr__(self, name: str):
        if name not in METHODS:
            raise AttributeError(name)

        def call(*args, **kwargs):
            key = _key(name, args, kwargs)
            if self.pos >= len(self.entries):
                raise ReplayMismatch(f"the recording ended before {name}({key!r})")
            e = self.entries[self.pos]
            if (e["method"], e["key"]) != (name, key):
                raise ReplayMismatch(f"call {self.pos + 1}: recorded {e['method']}({e['key']!r}), got {name}({key!r})")
            self.pos += 1
            self.calls += [Call(**c) for c in e["calls"]]
            if e["last_search"] is not None:
                self.last_search = e["last_search"]
            result = _load(e["result"])
            if name == "ensure_datapack":
                self.datapacks[key] = result
            return result
        return call


def latest_recording(engine: str = "real Meko") -> Path | None:
    """The newest recording of the given engine in `recordings/`, if any."""
    found = []
    for p in RECORDINGS.glob("*.json"):
        try:
            head = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if head.get("engine") == engine:
            found.append((head.get("recorded_at", ""), p))
    return max(found)[1] if found else None
