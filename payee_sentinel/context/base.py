"""The context layer every agent talks to: the same calls on real Meko and on the offline simulator.

The method names follow Meko's MCP tools (`memory_add`, `memory_search`, `context_search`, `conversation_*`,
`knowledgebase_*`, `artifact_*`, `track_token_usage`) plus the Datapack API calls for learnings and promotion.
Both implementations also keep `calls`: one `Call` per request, which the audit view and the token measurements read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass
class Hit:
    id: str
    text: str
    score: float
    metadata: dict = field(default_factory=dict)
    source: str = "memory"          # memory | knowledge_base | conversation | collective


@dataclass
class Call:
    at: str
    agent_id: str
    tool: str
    datapack: str
    conversation_id: str | None
    summary: str
    latency_ms: float
    tokens_sent: int = 0            # rough size of what came back into the agent's context (chars / 4)
    found: int = 0                  # how many results a search returned


class ContextStore(Protocol):
    calls: list[Call]

    def ensure_datapack(self, name: str, description: str = "") -> str: ...
    def conversation_create(self, datapack_id: str, agent_id: str, title: str,
                            metadata: dict | None = None) -> str: ...
    def add_message(self, datapack_id: str, conversation_id: str, agent_id: str, input: str, output: str,
                    reasoning: str | None = None, plan: list[str] | None = None,
                    metadata: dict | None = None) -> str: ...
    def memory_add(self, datapack_id: str, conversation_id: str, agent_id: str, text: str,
                   metadata: dict | None = None) -> list[str]: ...
    def memory_search(self, datapack_id: str, conversation_id: str, agent_id: str, query: str,
                      limit: int = 10) -> list[Hit]: ...
    def context_search(self, datapack_id: str, conversation_id: str, agent_id: str, query: str,
                       limit: int = 10) -> dict[str, list[Hit]]: ...
    def collective(self, datapack_id: str, conversation_id: str, agent_id: str) -> list[Hit]: ...
    def list_learnings(self, datapack_id: str) -> list[Hit]: ...
    def promote(self, datapack_id: str, memory_ids: list[str]) -> list[str]: ...
    def knowledge_upload(self, datapack_id: str, filename: str, content: bytes,
                         conversation_id: str | None = None) -> str: ...
    def knowledge_ready(self, datapack_id: str, key: str, conversation_id: str | None = None,
                        timeout: float = 60.0) -> str: ...
    def knowledge_search(self, datapack_id: str, conversation_id: str, agent_id: str, query: str,
                         limit: int = 5) -> list[Hit]: ...
    def artifact_put(self, datapack_id: str, conversation_id: str, agent_id: str, filename: str, content: bytes,
                     content_type: str) -> str: ...
    def artifact_get(self, datapack_id: str, conversation_id: str, agent_id: str, content_hash: str) -> bytes: ...
    def track_tokens(self, datapack_id: str, conversation_id: str, name: str, input_tokens: int,
                     output_tokens: int, model: str) -> None: ...
    # Meko's self-learning loop: every `context_search` is logged with a quality band (high, mid or low); the status
    # lists the weak ones, and a verify replays one after context was added. `last_search` keeps the latest
    # `context_search`'s own verdict (`quality`, `hint`).
    last_search: dict
    def selflearning_status(self, datapack_id: str, conversation_id: str) -> dict: ...
    def search_log(self, datapack_id: str, conversation_id: str, query: str) -> dict | None: ...
    def selflearning_verify(self, datapack_id: str, conversation_id: str, log_id: str) -> dict: ...


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def approx_tokens(obj: Any) -> int:
    """Rough token count (4 characters per token), used only to compare context sizes."""
    return max(1, len(str(obj)) // 4)
