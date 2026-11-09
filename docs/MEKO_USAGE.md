# How Payee Sentinel uses Meko

Meko is the only place the agents share anything. Each agent keeps its own conversation; what one agent learns reaches
another only through memory in a Meko datapack. This page maps every Meko feature to the code that uses it, and says
plainly which features are not used by the agents yet.

## Used by the agents today

| Meko feature | What Payee Sentinel does with it | Code |
|---|---|---|
| **Datapacks** (REST `POST /datapacks`) | Four separate datapacks, as theme 03 suggests: `sentinel-signals` (raw risk signals and the analyst's resolutions), `sentinel-patterns` (attack patterns a person approved), `sentinel-legit` (legitimate look-alikes a person confirmed), `sentinel-audit` (decision records) | `agents.py`, `Memory.__init__` |
| **Conversations** (`conversation_create`) | One conversation per agent; the payments agent starts a new one at every payment run, so it begins with no history and must find what matters in memory | `Memory.conversation`, `Memory.new_session`, `story.py` `run_payments` |
| **Decisions with reasoning** (`conversation_add_message` with `reasoning` and `plan`) | Every protective decision (bank change blocked, vendor frozen, payment held) is saved as a turn with its reasoning and plan, which Meko's documentation says its portal shows as a decision trace (we have not opened that view yet) | `story.py`, `Lane._decision` |
| **Memory** (`memory_add` with JSON metadata) | Each risk signal an agent sees (type, vendor, time, weight, evidence) — the core of the shared memory; the analyst's verdicts ("attack confirmed", "legitimate"), so closed cases stop raising alarms; decision records, so the auditor can find them later; pattern candidates | `Memory.write`, `Memory.resolve`, `Memory.record_decision`, `evaluate.py`, `story.py` |
| **Memory search** (`memory_search`, hybrid) | Before every bank-change and payment decision an agent searches the vendor's signals from all agents; the auditor searches decision records and signals to explain a case | `Memory.signals`, `Memory.history`, `Memory.decisions` |
| **Learnings and promotion** (REST `POST /promote-memories`) | After a confirmed attack the auditor writes a pattern candidate; a person presses "Promote" and it becomes collective memory. In learning v2, legitimate look-alikes a person confirms are promoted the same way | `story.py` `Story.promote`, `evaluate.py`, `context/meko.py` |
| **Collective memory** (`memory_get_all` with `promoted=True`) | Before acting, agents check approved patterns; a match freezes a vendor's bank details before anyone asks to change them | `Memory.patterns`, `Memory.legit_patterns`, `Agent._check_patterns` |
| **Knowledge base** (`knowledgebase_upload_document`, `knowledgebase_search`) | The company's payment policy and fraud playbook (`payee_sentinel/knowledge/`, invented) are uploaded to `sentinel-patterns` when the demo starts; the auditor quotes the passage behind the rule it explains | `story.py`, `Story.__init__`, `Story.explain` |
| **Artifacts** (`artifact_put`) | The auditor keeps each case report as an artifact in `sentinel-audit` (content-addressed: the same report gives the same hash) | `story.py`, `Story._save_report` |

## Checked against the live service, not used by the agents yet
| Meko feature | Status | Planned use |
|---|---|---|
| `track_token_usage` | Works (`docs/MEKO_SMOKE.md`) | Once Claude reads the emails and writes the explanations |
| `context_search` | Works, returns Meko's own `token_savings` | The auditor's single search across memory, knowledge and conversations |

## What we learned about Meko along the way
The live service and the offline simulator behave the same for everything above (`docs/MEKO_SMOKE.md`):
- metadata goes in as a JSON string;
- promoted memories leave `memory_search` and lose their metadata, so a pattern carries its signal types in its text;
- explicit `memory_add` text is stored as written, while memories extracted from conversations are rewritten by an
  LLM — and one was rewritten wrongly — so signals are always written explicitly;
- the whole demo story ran on real Meko in about a minute and reached the same decisions as offline — on a clean,
  quiet service. In nine rehearsals in a row right after it, new memories did not show up in searches in time, and
  the Meko lane lost like the siloed one. That is open (`docs/MEKO_SMOKE.md`).
