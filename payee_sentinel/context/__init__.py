"""Pick the context store: real Meko when `MEKO_API_KEY` is set (in the environment or in `.env`), else the simulator."""
from __future__ import annotations

import os
from pathlib import Path

from .local import LocalMeko


def load_env(path: Path = Path(__file__).resolve().parents[2] / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def open_store(prefer_real: bool = True):
    load_env()
    if prefer_real and os.environ.get("MEKO_API_KEY", "").startswith("mko_tkn_"):
        from .meko import MekoClient
        return MekoClient()
    return LocalMeko()
