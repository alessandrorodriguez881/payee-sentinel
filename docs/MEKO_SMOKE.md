# Real Meko: what was checked

First run against the live service on 2026-11-08, 19:20-19:30 CET, Free plan, in a separate datapack
(`sentinel-smoke`), with `scripts/meko_smoke.py` and two smaller probes. About 25 calls in all. Personal details that
some replies include (account name and e-mail) are left out here.

| Feature | Result | What it changed in the code |
|---|---|---|
| Create a datapack (REST `POST /datapacks`) | Works, ~0.7 s | — |
| `conversation_create`, `conversation_add_message` with `reasoning` and `plan` | Work, 0.3-1 s | — |
| `memory_add` | Needs `metadata` as a JSON **string**; an object is rejected (`Input should be a valid string`). Explicit text is stored as written; replies `{"results": [{"id", "memory", "event": "ADD"}]}` | Client sends `json.dumps(metadata)` |
| `memory_search` | Returns `metadata` back as an object, plus `agent_id`, `score`, `raw_similarity_score`. **Took 12-16 s** per call in these first tests (but see below) | Agents search once per vendor and decision |
| Memories extracted from a saved conversation | Meko's LLM rewrites them, and got one wrong: an email *from* a vendor asking us to change *its* IBAN became "a change to the user's IBAN" | Signals are written with explicit `memory_add`, never left to extraction |
| `context_search` | `{"results": {"memory", "knowledge_base", "conversation": {"hits", "thread"}}, "token_savings": {...}, "match_quality", "agent_hint"}`, ~12 s | Parser fixed; Meko's own `token_savings` kept for the token measurements |
| Promote a memory (REST `POST /promote-memories`) | Works. The promoted memory **leaves `memory_search`**, is listed by `memory_get_all(promoted=True)` with `promoted_to_kb_at` and who promoted it, and **loses its metadata** | Patterns carry their signal types in the text; the simulator now behaves the same way |
| Knowledge upload, REST `POST /datapacks/{id}/knowledge-bases/upload` | 404 `Not Found`, although the documented path and the OpenAPI file match | Uploads go through MCP `knowledgebase_upload_document` (presigned S3 POST): indexed in ~10 s, then found by `knowledgebase_search` (`chunk_text`, `rerank_score`) |
| `artifact_put` | Works, returns the SHA-256 content hash | — |
| `track_token_usage` | Works (empty reply) | — |
| Traces | `memory_get_all` replies carry a Langfuse project id; the portal's Observe view was not opened yet | To check in the portal |

## The whole demo story on real Meko (2026-11-09, ~00:00 CET)
`Story(MekoClient)` run end to end (both weeks, one promotion by a person), the same code as the offline demo:

| | Result |
|---|---|
| Outcome | Identical to the offline simulator: siloed lane €85,250 to the attackers, Meko lane €0; Nordwind's change blocked Tue 16:40, Bergmann frozen Tue 09:05 by the promoted pattern |
| Total time | 73 s (setup 5.8 s; slowest step 16 s, the first payment run) |
| `memory_search` | 21 calls, median 1.7 s, max 5.3 s — much faster than the 12-16 s of the first tests three hours earlier |
| `memory_add` | 27 calls, median 0.4 s |
| `memory_get_all` (collective memory) | 28 calls, median 0.2 s |
| `conversation_add_message` (decisions with reasoning) | 3 calls, median 0.5 s |
| Promotion (REST) | 1 call, 2.6 s |

Search latency varies a lot from one hour to the next; both measurements are reported.

## Consequences for the plan
- A full six-week world on real Meko means hundreds of searches: minutes at best, hours at the first tests' latency,
  and a dent in the Free plan's 10,000 retrievals a month. The large evaluation stays on the simulator; real Meko
  runs the demo story (73 s) and, later, a measured sample, with one search per vendor and payment run.
- Repeated demo runs write into the same datapacks, so an independent code review asked whether earlier rehearsals
  crowd a run's own memories out of the search results. On the simulator they did from the sixth rehearsal on (the
  metadata filter only works on what a search returns). Fix: demo runs put their run id in each memory's text and in
  the query, and search with a limit of 50. Ten rehearsals in a row then give the same decisions on the simulator;
  on real Meko this is still to be checked.
- The simulator was aligned with these findings the same evening. Re-running the confirmation seeds gave identical
  decisions (`docs/RESULTS.md`).
- Open questions: why the REST upload returns 404; whether search latency is lower outside the Free plan or at other
  hours; how decision traces look in the portal.
