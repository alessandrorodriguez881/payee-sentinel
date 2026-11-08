# Real Meko: what was checked

First run against the live service on 2026-11-08, 19:20-19:30 CET, Free plan, in a separate datapack
(`sentinel-smoke`), with `scripts/meko_smoke.py` and two smaller probes. About 25 calls in all. Personal details that
some replies include (account name and e-mail) are left out here.

| Feature | Result | What it changed in the code |
|---|---|---|
| Create a datapack (REST `POST /datapacks`) | Works, ~0.7 s | — |
| `conversation_create`, `conversation_add_message` with `reasoning` and `plan` | Work, 0.3-1 s | — |
| `memory_add` | Needs `metadata` as a JSON **string**; an object is rejected (`Input should be a valid string`). Explicit text is stored as written; replies `{"results": [{"id", "memory", "event": "ADD"}]}` | Client sends `json.dumps(metadata)` |
| `memory_search` | Returns `metadata` back as an object, plus `agent_id`, `score`, `raw_similarity_score`. **Takes 12-16 s** per call | Agents must search once per vendor and decision |
| Memories extracted from a saved conversation | Meko's LLM rewrites them, and got one wrong: an email *from* a vendor asking us to change *its* IBAN became "a change to the user's IBAN" | Signals are written with explicit `memory_add`, never left to extraction |
| `context_search` | `{"results": {"memory", "knowledge_base", "conversation": {"hits", "thread"}}, "token_savings": {...}, "match_quality", "agent_hint"}`, ~12 s | Parser fixed; Meko's own `token_savings` kept for the token measurements |
| Promote a memory (REST `POST /promote-memories`) | Works. The promoted memory **leaves `memory_search`**, is listed by `memory_get_all(promoted=True)` with `promoted_to_kb_at` and who promoted it, and **loses its metadata** | Patterns carry their signal types in the text; the simulator now behaves the same way |
| Knowledge upload, REST `POST /datapacks/{id}/knowledge-bases/upload` | 404 `Not Found`, although the documented path and the OpenAPI file match | Uploads go through MCP `knowledgebase_upload_document` (presigned S3 POST): indexed in ~10 s, then found by `knowledgebase_search` (`chunk_text`, `rerank_score`) |
| `artifact_put` | Works, returns the SHA-256 content hash | — |
| `track_token_usage` | Works (empty reply) | — |
| Traces | `memory_get_all` replies carry a Langfuse project id; the portal's Observe view was not opened yet | To check in the portal |

## Consequences for the plan
- At 12-16 s per search, a full six-week world on real Meko (hundreds of searches) would take hours and eat into the
  Free plan's 10,000 retrievals a month. The large evaluation stays on the simulator; real Meko runs the demo story
  and a measured sample, with one search per vendor and payment run.
- The simulator was aligned with these findings the same evening. Re-running the confirmation seeds gave identical
  decisions (`docs/RESULTS.md`).
- Open questions: why the REST upload returns 404; whether search latency is lower outside the Free plan or at other
  hours; how decision traces look in the portal.
