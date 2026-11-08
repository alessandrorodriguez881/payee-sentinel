"""Offline stand-in for Meko, used when there is no API key and for the large evaluation runs.

It keeps Meko's rules that the agents depend on:
- memory belongs to the account and is searchable by all of its agents in the same datapack (`agent_id` is only for
  attribution); nothing crosses datapacks;
- promoting a memory is one-way: it leaves the private store and becomes collective memory every member can find;
- `context_search` answers in separate buckets (memory, knowledge base, conversations);
- artifacts are content-addressed (same bytes, same hash); the Free plan allows 10 datapacks.

What it does not copy: Meko ranks with embeddings plus keywords and entities, and extracts facts with an LLM. This
simulator stores text as given and ranks by shared words and exact entities (vendor ids, names, IBANs), so it is
stricter than the real service. The README says which numbers come from here and which from real Meko.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
import uuid

from .base import Call, Hit, approx_tokens, now_iso

FREE_DATAPACKS = 10
_WORD = re.compile(r"[a-z0-9][a-z0-9_.\-]*")
_ENTITY = re.compile(r"\b(V\d{3}|E\d{5}|ATK-\d{2}|[A-Z]{2}\d{2}[A-Z0-9]{10,30})\b")


def _words(text: str) -> set[str]:
    return {w.strip(".-") for w in _WORD.findall(text.lower()) if len(w) > 2}


def _score(query: str, text: str) -> float:
    q, d = _words(query), _words(text)
    if not q or not d:
        return 0.0
    overlap = len(q & d) / math.sqrt(len(q) * len(d))
    entities = set(_ENTITY.findall(query)) & set(_ENTITY.findall(text))
    return round(overlap + 0.5 * len(entities), 4)


class LocalMeko:
    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.datapacks: dict[str, str] = {}          # name -> id
        self.conversations: dict[str, dict] = {}
        self.memories: dict[str, dict] = {}
        self.knowledge: dict[str, list[dict]] = {}   # datapack id -> chunks
        self.artifacts: dict[str, bytes] = {}
        self.token_usage: list[dict] = []

    # ---------- bookkeeping ----------
    def _log(self, agent_id, tool, datapack, conversation_id, summary, t0, returned=None) -> None:
        self.calls.append(Call(now_iso(), agent_id, tool, datapack, conversation_id, summary,
                               round((time.perf_counter() - t0) * 1000, 2),
                               approx_tokens(returned) if returned is not None else 0))

    def _check(self, datapack_id: str) -> None:
        if datapack_id not in self.datapacks.values():
            raise KeyError(f"datapack {datapack_id} not found")

    # ---------- datapacks and conversations ----------
    def ensure_datapack(self, name: str, description: str = "") -> str:
        t0 = time.perf_counter()
        if name not in self.datapacks:
            if len(self.datapacks) >= FREE_DATAPACKS:
                raise RuntimeError("FREE_TIER_DATAPACK_LIMIT: a Free account has already created 10 datapacks")
            self.datapacks[name] = str(uuid.uuid4())
            self.knowledge[self.datapacks[name]] = []
        self._log("setup", "datapack_create", name, None, description, t0)
        return self.datapacks[name]

    def conversation_create(self, datapack_id, agent_id, title, metadata=None) -> str:
        t0 = time.perf_counter()
        self._check(datapack_id)
        cid = str(uuid.uuid4())
        self.conversations[cid] = dict(datapack=datapack_id, agent_id=agent_id, title=title,
                                       metadata=metadata or {}, messages=[])
        self._log(agent_id, "conversation_create", datapack_id, cid, title, t0)
        return cid

    def add_message(self, datapack_id, conversation_id, agent_id, input, output, reasoning=None, plan=None,
                    metadata=None) -> str:
        t0 = time.perf_counter()
        conv = self.conversations[conversation_id]
        mid = str(uuid.uuid4())
        conv["messages"].append(dict(id=mid, agent_id=agent_id, input=input, output=output, reasoning=reasoning,
                                     plan=plan or [], metadata=metadata or {}, at=now_iso()))
        self._log(agent_id, "conversation_add_message", datapack_id, conversation_id, output[:120], t0)
        return mid

    # ---------- memory ----------
    def memory_add(self, datapack_id, conversation_id, agent_id, text, metadata=None) -> list[str]:
        t0 = time.perf_counter()
        self._check(datapack_id)
        mid = str(uuid.uuid4())
        self.memories[mid] = dict(id=mid, datapack=datapack_id, conversation_id=conversation_id,
                                  agent_id=agent_id, text=text, metadata=metadata or {}, state="private",
                                  at=now_iso())
        self._log(agent_id, "memory_add", datapack_id, conversation_id, text[:120], t0)
        return [mid]

    def _memory_hits(self, datapack_id: str, query: str) -> list[Hit]:
        hits = []
        for m in self.memories.values():
            if m["datapack"] != datapack_id:
                continue
            s = _score(query, m["text"])
            if s > 0:
                hits.append(Hit(m["id"], m["text"], s, dict(m["metadata"], agent_id=m["agent_id"]),
                                "collective" if m["state"] == "promoted" else "memory"))
        return sorted(hits, key=lambda h: -h.score)

    def memory_search(self, datapack_id, conversation_id, agent_id, query, limit=10) -> list[Hit]:
        t0 = time.perf_counter()
        self._check(datapack_id)
        hits = self._memory_hits(datapack_id, query)[:limit]
        self._log(agent_id, "memory_search", datapack_id, conversation_id, query, t0, [h.text for h in hits])
        return hits

    def context_search(self, datapack_id, conversation_id, agent_id, query, limit=10) -> dict[str, list[Hit]]:
        t0 = time.perf_counter()
        self._check(datapack_id)
        mem = self._memory_hits(datapack_id, query)[:limit]
        kb = self._kb_hits(datapack_id, query)[:limit]
        conv = []
        for cid, c in self.conversations.items():
            if c["datapack"] != datapack_id:
                continue
            for msg in c["messages"]:
                text = f"{msg['input']}\n{msg['output']}\n{msg['reasoning'] or ''}"
                s = _score(query, text)
                if s > 0:
                    conv.append(Hit(msg["id"], msg["output"], s, dict(msg["metadata"], agent_id=msg["agent_id"],
                                                                      conversation_id=cid), "conversation"))
        conv = sorted(conv, key=lambda h: -h.score)[:limit]
        out = {"memory": mem, "knowledge_base": kb, "conversation": conv}
        self._log(agent_id, "context_search", datapack_id, conversation_id, query, t0,
                  [h.text for b in out.values() for h in b])
        return out

    # ---------- learnings and promotion (Datapack API) ----------
    def list_learnings(self, datapack_id) -> list[Hit]:
        """Memories waiting for a promote-or-keep decision. Meko picks them itself; here, the ones an agent marked
        as candidates (`metadata.learning_candidate`), newest first, at most 5 like Meko."""
        t0 = time.perf_counter()
        out = [Hit(m["id"], m["text"], 1.0, m["metadata"], "memory") for m in reversed(list(self.memories.values()))
               if m["datapack"] == datapack_id and m["state"] == "private"
               and m["metadata"].get("learning_candidate")][:5]
        self._log("reviewer", "list_learnings", datapack_id, None, f"{len(out)} candidates", t0)
        return out

    def promote(self, datapack_id, memory_ids) -> list[str]:
        t0 = time.perf_counter()
        moved = []
        for mid in memory_ids:
            m = self.memories.get(mid)
            if m and m["datapack"] == datapack_id and m["state"] == "private":
                m["state"] = "promoted"
                moved.append(mid)
        self._log("reviewer", "promote_memories", datapack_id, None, f"promoted {len(moved)}", t0)
        return moved

    # ---------- knowledge base ----------
    def knowledge_upload(self, datapack_id, filename, content: bytes) -> str:
        t0 = time.perf_counter()
        self._check(datapack_id)
        text = content.decode("utf-8")
        doc_id = hashlib.sha256(content).hexdigest()[:16]
        chunks = [c.strip() for c in re.split(r"\n(?=#)|\n\s*\n", text) if c.strip()]
        self.knowledge[datapack_id] = [c for c in self.knowledge[datapack_id] if c["doc"] != doc_id]
        self.knowledge[datapack_id] += [dict(doc=doc_id, file=filename, n=i, text=c) for i, c in enumerate(chunks)]
        self._log("setup", "knowledgebase_upload", datapack_id, None, f"{filename}: {len(chunks)} chunks", t0)
        return doc_id

    def _kb_hits(self, datapack_id: str, query: str) -> list[Hit]:
        hits = [Hit(f"{c['doc']}#{c['n']}", c["text"], _score(query, c["text"]), dict(file=c["file"]),
                    "knowledge_base") for c in self.knowledge.get(datapack_id, [])]
        hits += [h for h in self._memory_hits(datapack_id, query) if h.source == "collective"]
        return sorted([h for h in hits if h.score > 0], key=lambda h: -h.score)

    def knowledge_search(self, datapack_id, conversation_id, agent_id, query, limit=5) -> list[Hit]:
        t0 = time.perf_counter()
        hits = self._kb_hits(datapack_id, query)[:limit]
        self._log(agent_id, "knowledgebase_search", datapack_id, conversation_id, query, t0, [h.text for h in hits])
        return hits

    # ---------- artifacts and tokens ----------
    def artifact_put(self, datapack_id, conversation_id, agent_id, filename, content: bytes, content_type) -> str:
        t0 = time.perf_counter()
        h = hashlib.sha256(content).hexdigest()
        self.artifacts[h] = content
        self._log(agent_id, "artifact_put", datapack_id, conversation_id, f"{filename} ({len(content)} bytes)", t0)
        return h

    def artifact_get(self, datapack_id, conversation_id, agent_id, content_hash) -> bytes:
        t0 = time.perf_counter()
        data = self.artifacts[content_hash]
        self._log(agent_id, "artifact_get", datapack_id, conversation_id, content_hash[:12], t0)
        return data

    def track_tokens(self, datapack_id, conversation_id, name, input_tokens, output_tokens, model) -> None:
        t0 = time.perf_counter()
        self.token_usage.append(dict(conversation_id=conversation_id, name=name, input_tokens=input_tokens,
                                     output_tokens=output_tokens, model=model))
        self._log(name, "track_token_usage", datapack_id, conversation_id, f"{input_tokens}+{output_tokens}", t0)
