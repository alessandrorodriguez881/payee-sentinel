"""Real Meko: the remote MCP server (`/mcp`, Streamable HTTP) plus the Datapack REST API.

Meko has no Python SDK yet, so this speaks JSON-RPC over HTTP directly. The MCP part follows the reference client in
yugabyte/canned-agents (`adlc-review-agent/src/adlc_review_agent/meko_client.py`, Apache-2.0): initialize once, keep
the `Mcp-Session-Id`, read SSE or JSON replies, unwrap double-encoded JSON. Rules from Meko's docs that matter here:
- every request needs `Authorization: Bearer mko_tkn_...` and a `User-Agent`;
- almost every tool needs a `conversation_id`, so agents call `conversation_create` first;
- listing learnings and promoting memories go through the REST API (`/memories?scope=learnings`,
  `/promote-memories`).

Checked against the real service on 2026-11-08 (`scripts/meko_smoke.py`, `docs/MEKO_SMOKE.md`):
- `memory_add` wants `metadata` as a JSON *string*; searches give it back as an object, and explicit text is stored
  as written (only memories extracted from saved conversations are rewritten by Meko's LLM);
- `context_search` answers `{"results": {"memory": [...], "knowledge_base": [...], "conversation": {"hits": [...]}},
  "token_savings": {...}, "match_quality": ...}`;
- the REST knowledge upload answered 404, so files go through `knowledgebase_upload_document` (a presigned S3 POST);
- a search takes 12-16 s, so agents should search once per vendor and decision, not per signal.
"""
from __future__ import annotations

import base64
import hashlib
import itertools
import json
import os
import time
from typing import Any

import httpx

from .base import Call, Hit, approx_tokens, now_iso

MCP_URL = os.environ.get("MEKO_MCP_URL", "https://mcp.mekodata.ai/mcp")
API_URL = os.environ.get("MEKO_API_URL", "https://api.mekodata.ai/api/v1")
USER_AGENT = "payee-sentinel/0.1"


class MekoError(RuntimeError):
    pass


def _unwrap(text: str) -> Any:
    try:
        parsed: Any = json.loads(text)
    except ValueError:
        return text
    while isinstance(parsed, str):  # some tools double-encode their result
        try:
            parsed = json.loads(parsed)
        except ValueError:
            break
    return parsed


def _items(obj: Any) -> list[dict]:
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        for key in ("results", "items", "memories", "hits", "documents", "data"):
            if key in obj:
                return _items(obj[key])
    return []


def _text(item: dict) -> str:
    for key in ("memory", "text", "content", "chunk_text", "chunk", "output", "document", "data"):
        if isinstance(item.get(key), str):
            return item[key]
    return json.dumps(item)[:500]


def _hits(obj: Any, source: str) -> list[Hit]:
    out = []
    for i in _items(obj):
        meta = i.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                meta = {"raw": meta}
        out.append(Hit(str(i.get("id") or i.get("memory_id") or i.get("document_id") or ""), _text(i),
                       float(i.get("score") or i.get("rerank_score") or 0.0), dict(meta, agent_id=i.get("agent_id")),
                       source))
    return out


class MekoClient:
    def __init__(self, api_key: str | None = None, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.api_key = api_key or os.environ.get("MEKO_API_KEY", "")
        if not self.api_key.startswith("mko_tkn_"):
            raise MekoError("MEKO_API_KEY missing or not a Meko key (it starts with mko_tkn_)")
        self.calls: list[Call] = []
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self._session: str | None = None
        self._ids = itertools.count(1)
        self._ready = False
        self._datapacks: dict[str, str] = {}

    # ---------- transport ----------
    def _headers(self, json_body: bool = True) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.api_key}", "User-Agent": USER_AGENT}
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def _rpc(self, method: str, params: dict | None = None) -> Any:
        rid = next(self._ids)
        headers = dict(self._headers(), Accept="application/json, text/event-stream")
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        res = self._http.post(MCP_URL, headers=headers,
                              json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        self._session = res.headers.get("mcp-session-id") or self._session
        res.raise_for_status()
        if "text/event-stream" in res.headers.get("content-type", ""):
            payload = None
            for line in res.text.splitlines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    if str(data.get("id")) == str(rid):
                        payload = data
            if payload is None:
                raise MekoError("Meko MCP: no reply in the event stream")
        else:
            payload = res.json()
        if payload.get("error"):
            raise MekoError(f"Meko MCP error {payload['error'].get('code')}: {payload['error'].get('message')}")
        return payload.get("result")

    def _tool(self, agent_id: str, name: str, args: dict, summary: str = "") -> Any:
        if not self._ready:
            self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "clientInfo": {"name": "payee-sentinel", "version": "0.1"}})
            headers = dict(self._headers(), Accept="application/json, text/event-stream")
            if self._session:
                headers["Mcp-Session-Id"] = self._session
            self._http.post(MCP_URL, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            self._ready = True
        t0 = time.perf_counter()
        result = self._rpc("tools/call", {"name": name, "arguments": {k: v for k, v in args.items()
                                                                       if v is not None}})
        texts = [c.get("text", "") for c in (result or {}).get("content", []) if c.get("type") == "text"]
        if (result or {}).get("isError"):
            raise MekoError(f"{name}: " + ("\n".join(texts) or "failed"))
        out = _unwrap(texts[0]) if texts else result
        returned = 0
        if name.endswith("search"):
            body = out.get("results", out) if isinstance(out, dict) else out
            buckets = body.values() if isinstance(body, dict) else [body]
            returned = approx_tokens(" ".join(h.text for b in buckets for h in _hits(b, "")))
        self.calls.append(Call(now_iso(), agent_id, name, args.get("datapack_id", ""), args.get("conversation_id"),
                               summary or name, round((time.perf_counter() - t0) * 1000, 1), returned))
        return out

    def _rest(self, method: str, path: str, **kw) -> Any:
        t0 = time.perf_counter()
        res = self._http.request(method, API_URL + path, headers=self._headers("json" in kw), **kw)
        if res.status_code >= 400:
            raise MekoError(f"{method} {path}: {res.status_code} {res.text[:300]}")
        self.calls.append(Call(now_iso(), "rest", f"{method} {path.split('?')[0]}", "", None, "",
                               round((time.perf_counter() - t0) * 1000, 1)))
        return res.json().get("data")

    # ---------- datapacks and conversations ----------
    def ensure_datapack(self, name: str, description: str = "") -> str:
        if name in self._datapacks:
            return self._datapacks[name]
        found = self._rest("GET", f"/datapacks?limit=100&name={name}")
        items = found.get("items", []) if isinstance(found, dict) else found
        for item in items or []:
            if item.get("datapack_name") == name:
                self._datapacks[name] = item["datapack_id"]
                return item["datapack_id"]
        made = self._rest("POST", "/datapacks", json={"name": name, "description": description})
        self._datapacks[name] = made["datapack_id"]
        return made["datapack_id"]

    def conversation_create(self, datapack_id, agent_id, title, metadata=None) -> str:
        out = self._tool(agent_id, "conversation_create", dict(datapack_id=datapack_id, agent_id=agent_id,
                                                                title=title, metadata=metadata), title)
        cid = out.get("conversation_id") or out.get("id") if isinstance(out, dict) else None
        if not cid:
            raise MekoError(f"conversation_create: no id in {str(out)[:200]}")
        return cid

    def add_message(self, datapack_id, conversation_id, agent_id, input, output, reasoning=None, plan=None,
                    metadata=None) -> str:
        out = self._tool(agent_id, "conversation_add_message", dict(
            datapack_id=datapack_id, conversation_id=conversation_id, agent_id=agent_id, input=input, output=output,
            reasoning=reasoning, plan=plan, metadata=metadata), output[:120])
        return str(out.get("message_id") or out.get("id") or "") if isinstance(out, dict) else ""

    # ---------- memory ----------
    def memory_add(self, datapack_id, conversation_id, agent_id, text, metadata=None) -> list[str]:
        out = self._tool(agent_id, "memory_add", dict(
            datapack_id=datapack_id, conversation_id=conversation_id, agent_id=agent_id, text=text,
            metadata=json.dumps(metadata) if metadata is not None else None), text[:120])
        return [str(i.get("id")) for i in _items(out) if i.get("id")]

    def memory_search(self, datapack_id, conversation_id, agent_id, query, limit=10) -> list[Hit]:
        out = self._tool(agent_id, "memory_search", dict(datapack_id=datapack_id, conversation_id=conversation_id,
                                                          agent_id=agent_id, query=query, limit=limit), query)
        if isinstance(out, dict) and (out.get("error") or isinstance(out.get("detail"), str)):
            raise MekoError(f"memory_search failed: {out}")
        return _hits(out, "memory")

    def context_search(self, datapack_id, conversation_id, agent_id, query, limit=10) -> dict[str, list[Hit]]:
        out = self._tool(agent_id, "context_search", dict(datapack_id=datapack_id, conversation_id=conversation_id,
                                                           agent_id=agent_id, query=query, limit=limit), query)
        self.last_token_savings = out.get("token_savings") if isinstance(out, dict) else None
        buckets = out.get("results", out) if isinstance(out, dict) else {}
        conv = buckets.get("conversation") or {}
        return {"memory": _hits(buckets.get("memory") or [], "memory"),
                "knowledge_base": _hits(buckets.get("knowledge_base") or [], "knowledge_base"),
                "conversation": _hits(conv.get("hits", []) if isinstance(conv, dict) else conv, "conversation")}

    def collective(self, datapack_id, conversation_id, agent_id) -> list[Hit]:
        """Promoted memories. They no longer appear in `memory_search` and lose their metadata, so callers read what
        they need from the text."""
        out = self._tool(agent_id, "memory_get_all", dict(datapack_id=datapack_id, conversation_id=conversation_id,
                                                           agent_id=agent_id, promoted=True), "collective memory")
        items = out.get("promoted_memories", []) if isinstance(out, dict) else []
        return [Hit(str(i.get("id")), str(i.get("data", "")), 1.0, {"promoted_at": i.get("promoted_to_kb_at")},
                    "collective") for i in items]

    # ---------- learnings and promotion ----------
    def list_learnings(self, datapack_id) -> list[Hit]:
        return _hits(self._rest("GET", f"/memories?datapack_id={datapack_id}&scope=learnings"), "memory")

    def promote(self, datapack_id, memory_ids) -> list[str]:
        out = self._rest("POST", "/promote-memories", json={"datapack_id": datapack_id, "memory_ids": memory_ids})
        return list(out.get("inserted_ids", [])) + list(out.get("updated_ids", []))

    # ---------- knowledge base and artifacts ----------
    def knowledge_upload(self, datapack_id, filename, content: bytes, conversation_id: str | None = None) -> str:
        """Ask Meko for a presigned S3 POST, send the file there, and return the document key (indexing goes on in
        the background; `knowledgebase_document_status` reports it)."""
        cid = conversation_id or self.conversation_create(datapack_id, "setup", "knowledge upload")
        out = self._tool("setup", "knowledgebase_upload_document", dict(
            datapack_id=datapack_id, conversation_id=cid, filename=filename, size=len(content),
            content_hash=hashlib.sha256(content).hexdigest(),
            content_head_base64=base64.b64encode(content[:3072]).decode()), filename)
        if not out.get("already_uploaded"):
            res = httpx.post(out["url"], data=out.get("fields") or {}, files={"file": (filename, content)},
                             timeout=60)
            if res.status_code >= 300:
                raise MekoError(f"knowledge upload to storage failed: {res.status_code} {res.text[:200]}")
        return out.get("key", "")

    def knowledge_search(self, datapack_id, conversation_id, agent_id, query, limit=5) -> list[Hit]:
        out = self._tool(agent_id, "knowledgebase_search", dict(datapack_id=datapack_id,
                                                                 conversation_id=conversation_id, agent_id=agent_id,
                                                                 query=query, limit=limit), query)
        return _hits(out, "knowledge_base")

    def artifact_put(self, datapack_id, conversation_id, agent_id, filename, content: bytes, content_type) -> str:
        out = self._tool(agent_id, "artifact_put", dict(
            datapack_id=datapack_id, conversation_id=conversation_id, agent_id=agent_id, filename=filename,
            content_type=content_type, content_base64=base64.b64encode(content).decode()), filename)
        return out.get("content_hash", "") if isinstance(out, dict) else ""

    def artifact_get(self, datapack_id, conversation_id, agent_id, content_hash) -> bytes:
        out = self._tool(agent_id, "artifact_get", dict(datapack_id=datapack_id, conversation_id=conversation_id,
                                                         agent_id=agent_id, content_hash=content_hash), content_hash)
        data = out.get("content_base64") if isinstance(out, dict) else None
        return base64.b64decode(data) if data else b""

    def track_tokens(self, datapack_id, conversation_id, name, input_tokens, output_tokens, model) -> None:
        self._tool(name, "track_token_usage", dict(
            datapack_id=datapack_id, conversation_id=conversation_id, name=name, input_tokens=input_tokens,
            output_tokens=output_tokens, total_tokens=input_tokens + output_tokens, model=model))
