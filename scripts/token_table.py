"""Tokens with and without Meko: what Claude reads to explain the same decision (`payee_sentinel/tokens.py`).

    python scripts/token_table.py              # token counts only (Anthropic's token-counting endpoint)
    python scripts/token_table.py --run        # plus one real explanation per decision, with Meko and without

Needs ANTHROPIC_API_KEY in .env or in the environment, and stops without it. Writes docs/TOKENS.md.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from payee_sentinel.tokens import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
