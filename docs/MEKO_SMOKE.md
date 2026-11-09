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

## Ten rehearsals in a row on real Meko (2026-11-09, 00:48-01:12 CET)
The same story run ten times in a row, each run writing into the same datapacks (as rehearsals before a recording would).

| Run | Meko lane | Search latency (median) |
|---|---|---|
| 1 | Same as offline: €0 lost, Nordwind blocked Tue 16:40, Bergmann frozen Tue 09:05; the vendor-master agent found 10-11 of the run's own signals | 1.8 s |
| 2-10 | **Found none of the run's own signals** and lost €85,250, like the siloed lane | 1.7-11.8 s (mostly about 11 s) |

What three more probes showed:
- `memory_add` always answered `ADD`, also for an identical text: Meko does not drop near-duplicates.
- `memory_search` returned the 50 results asked for, spread over 11 runs, 4-5 from each. The run id in the text and the
  query did not move a run's own memories up.
- Three memories added with an exact, known text did not appear in an exact-text search minutes later, while older
  memories with the same text came first (score 0.92). Our reading (not confirmed): after about 1,000 calls in 25
  minutes, new memories were not being indexed in time. Search latency rose from about 2 s to about 11 s at the same time.
- `memory_search` with `run_id` set returned nothing.

Checked again at 01:42, 25 minutes later: the memories written by rehearsals 2-10 now do appear in searches, so they
were indexed, only too late for the run that needed them. The three probe memories written at 01:14 still did not
appear, and a search still took about 12 s.

Checked again on 2026-11-09 at 04:28, three hours later, on a quiet service:
- the three probe memories are **stored** (`memory_get_all` lists them, written at 01:14, among 280 memories in
  `sentinel-signals`) but still **not searchable**: an exact-text search with a limit of 50 returned older memories
  with the same text (score 0.92) and none of the three. Searches took 1.7-4.4 s;
- two new memories written at 04:30 in the separate smoke datapack, one with a unique text and one with the probes'
  exact text, were both found by the first search, 17 s and 28 s after they were written (each search took 11.6 s).

Our reading at that point: on a quiet service new memories become searchable within seconds; a few written under
heavy load were stored but never indexed. The next pass corrected it.

## One more pass on real Meko, and the cause (2026-11-09, 04:48-04:53 CET)
The demo story with the new decision traces and the self-learning loop, run once on the same datapacks (run
`164ea90c`): it took 106 s and the Meko lane **lost €85,250 like the siloed one**. The vendor-master agent found none
of the run's own signals (risk 0), so nothing was blocked, no case was confirmed and the self-learning loop had nothing
to work on. Both `context_search` questions were graded `low` and took 14-16 s.

What three probes then showed, about ten minutes later:
- the pass's memories **are** indexed: its `portal.reset_new_device` signal for Nordwind came back at rank 37 of 50 for
  its exact text, behind copies written by earlier runs; for the agent's own query it was not in the top 100;
- with `limit` 400 Meko returned all 316 memories in the datapack (2 s), and the pass's 13 Nordwind signals came last
  among 157;
- **the score of a recent memory is about half its similarity**: in `sentinel-signals`, every copy of that signal
  (all written in the last six hours, the 01:14 probes included) had `score` 0.45 for `raw_similarity_score` 0.89;
  in the smoke datapack the copy written at 04:30 had score 0.50 for similarity 1.00, while one written on 8-Nov at
  19:22 had score 0.91 for similarity 0.91.

Our reading (not confirmed by Meko): a new memory is searchable at once, but only part of its hybrid score counts until
background processing completes, which took more than six hours here. Among near-identical memories, a new one then
ties with, or loses to, older copies, and with twelve runs' copies in one datapack the newest run's signals fall out of
the results. The midnight pass worked because its datapacks were nearly empty. Questions for Meko's Discord
(Alessandro asks; we only read there): how the score of a new memory is built, and how long until it is complete.

For the demo this means: run real Meko on datapacks with no earlier runs in them, once, for the recording. Clearing
the four demo datapacks deletes data, so it waits for Alessandro; no more passes on these datapacks until then.

So the real-Meko demo is reliable on a clean, quiet service (run 1), not under rehearsals run back to back: under that
load, new memories became searchable minutes (or more) after they were written. Open: ask Meko about indexing delays,
leave time between rehearsals, record with one clean pass, and decide with Alessandro whether to clear the demo
datapacks before recording (that deletes data, so it is his call). For the product, it means an agent cannot assume
that what another agent wrote a few seconds ago is already searchable.

## Tools the live server lists, and Meko's self-learning loop (2026-11-09, 04:30-04:45 CET)
`tools/list` on the MCP server returns **57 tools**, against the 28 in the documentation read on 8-Nov. New to us:
`selflearning_status`, `selflearning_shortfalls`, `selflearning_query_log`, `selflearning_verify`; `amp_*` (Postgres
clusters, compute and branches inside a datapack); `project_*`. The input schemas also settled two details:
`conversation_add_message` takes `metadata` as a JSON **string** (like `memory_add`), and `context_search` takes an
optional `prompt` and `verify`.

| Check | Result |
|---|---|
| `context_search` reply | Besides the buckets: `match_quality` (`high`, `mid`, `low`), `agent_hint` (for a mid match: "Partial match: use these results, and tell the user what is missing.") and `token_savings` per bucket |
| `selflearning_status` on the smoke datapack (read only) | Quality 0 %: our 3 searches of 8-Nov were all `mid`; to-dos `clarify_prompts` ("Nordwind IBAN change": too short) and `add_context` (a partial match) with their `log_id`s. The four demo datapacks had no logged searches: the agents had not used `context_search` yet |
| `amp_core_budget` (read only) | Free tier, 0.5 vCPU cap, 0 used; `amp_cluster_list` empty |
| The loop, in the smoke datapack | The vendor-master agent's question ("Has the bank account LT73… or the callback phone … appeared in an earlier confirmed fraud case?") was graded **mid** (0.69, `partial_match`, category `coverage`). After the confirmed case file was uploaded to the knowledge base (indexed in under 10 s), `selflearning_verify` replayed it: **mid → high (0.69 → 0.81)**, `outcome: improved`, top result the case file. The same question for Bergmann's request (another Lithuanian account and phone) put the Nordwind case first among knowledge results (rerank 0.97); Meko graded it mid (a partial match) |

The demo now runs this loop (`docs/MEKO_USAGE.md`); the simulator grades its searches with thresholds set so the same
searches get the same bands.

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
