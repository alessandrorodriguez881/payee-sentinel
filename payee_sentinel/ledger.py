"""The company's ledger: the vendor master with its history, bank-change requests and payments, as SQL tables.

"Postgres inside the context": on real Meko this is a Postgres database inside the `sentinel-audit` datapack (Meko
AMP), next to the agents' memory. Every row that comes from a decision carries the id of that decision's trace in
Meko, so a record and the reasoning behind it point at each other. Where that database cannot be reached (the public
demo, a replay, the tests) the same schema runs on SQLite in memory.

The ledger records what happened and answers questions about it; the decisions still come from the rules and the
shared memory. The analyst reads from it the phone number that was on file 60 days before a review. Every run writes
under its own `run_id`, so runs never mix and nothing has to be deleted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
import sqlite3
import time

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS vendors (run_id TEXT, vendor_id TEXT, name TEXT, country TEXT, iban TEXT, phone TEXT, "
    "email TEXT, frozen_at TEXT, frozen_trace TEXT, PRIMARY KEY (run_id, vendor_id))",
    "CREATE TABLE IF NOT EXISTS vendor_history (run_id TEXT, vendor_id TEXT, field TEXT, value TEXT, valid_from TEXT, "
    "source TEXT)",
    "CREATE TABLE IF NOT EXISTS bank_changes (run_id TEXT, request_id TEXT, vendor_id TEXT, new_iban TEXT, "
    "requested_at TEXT, via TEXT, outcome TEXT, decided_at TEXT, trace_id TEXT)",
    "CREATE TABLE IF NOT EXISTS payments (run_id TEXT, invoice_no TEXT, vendor_id TEXT, run_at TEXT, amount NUMERIC, "
    "iban TEXT, decision TEXT, trace_id TEXT)",
)
FIELDS = ("phone", "email", "iban")


@dataclass
class Query:
    sql: str
    ms: float
    rows: int


class Ledger:
    """The SQL both backends run (`%s` placeholders); each backend only executes."""
    kind = "ledger"

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.queries: list[Query] = []
        for sql in SCHEMA:
            self._execute(sql, ())

    def _execute(self, sql: str, params: tuple, many: bool = False) -> list[tuple]:
        raise NotImplementedError

    def q(self, sql: str, params: tuple = (), many: bool = False) -> list[tuple]:
        t0 = time.perf_counter()
        rows = self._execute(sql, params, many)
        self.queries.append(Query(" ".join(sql.split()), round((time.perf_counter() - t0) * 1000, 1), len(rows)))
        return rows

    # ---------- what the agents and the analyst write ----------
    def setup(self, vendors: dict, since: datetime) -> None:
        """The vendor master at the start, each contact value on file since `since`."""
        self.q("INSERT INTO vendors (run_id, vendor_id, name, country, iban, phone, email) VALUES (%s, %s, %s, %s, %s, "
               "%s, %s)", [(self.run_id, v.id, v.name, v.country, v.iban, v.phone, v.email) for v in vendors.values()],
               many=True)
        self.q("INSERT INTO vendor_history VALUES (%s, %s, %s, %s, %s, %s)",
               [(self.run_id, v.id, f, getattr(v, f), since.isoformat(), "onboarding") for v in vendors.values()
                for f in FIELDS], many=True)

    def contact_changed(self, vendor_id: str, field: str, value: str, at: datetime, source: str) -> None:
        assert field in FIELDS
        self.q(f"UPDATE vendors SET {field} = %s WHERE run_id = %s AND vendor_id = %s", (value, self.run_id, vendor_id))
        self.q("INSERT INTO vendor_history VALUES (%s, %s, %s, %s, %s, %s)",
               (self.run_id, vendor_id, field, value, at.isoformat(), source))

    def bank_change_requested(self, request_id: str, vendor_id: str, new_iban: str, at: datetime, via: str) -> None:
        self.q("INSERT INTO bank_changes VALUES (%s, %s, %s, %s, %s, %s, 'pending', NULL, NULL)",
               (self.run_id, request_id, vendor_id, new_iban, at.isoformat(), via))

    def bank_change_decided(self, vendor_id: str, outcome: str, at: datetime, trace_id: str) -> None:
        """The pending request's outcome (applied, blocked or rejected); an applied one changes the vendor master."""
        rows = self.q("SELECT request_id, new_iban FROM bank_changes WHERE run_id = %s AND vendor_id = %s AND "
                      "outcome = 'pending' ORDER BY requested_at DESC", (self.run_id, vendor_id))
        if not rows:
            return
        self.q("UPDATE bank_changes SET outcome = %s, decided_at = %s, trace_id = %s WHERE run_id = %s AND "
               "request_id = %s", (outcome, at.isoformat(), trace_id, self.run_id, rows[0][0]))
        if outcome == "applied":
            self.contact_changed(vendor_id, "iban", rows[0][1], at, f"bank-change request {rows[0][0]}")

    def frozen(self, vendor_id: str, at: datetime, trace_id: str | None) -> None:
        self.q("UPDATE vendors SET frozen_at = %s, frozen_trace = %s WHERE run_id = %s AND vendor_id = %s AND "
               "frozen_at IS NULL", (at.isoformat(), trace_id, self.run_id, vendor_id))

    def payment(self, invoice_no: str, vendor_id: str, run_at: datetime, amount: float, iban: str, decision: str,
                trace_id: str) -> None:
        self.q("INSERT INTO payments VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
               (self.run_id, invoice_no, vendor_id, run_at.isoformat(), amount, iban, decision, trace_id))

    # ---------- what the analyst and the auditor ask ----------
    def value_as_of(self, vendor_id: str, field: str, at: datetime) -> str | None:
        """What the vendor master held for a field at a given time (the history makes it an "as of" query)."""
        rows = self.q("SELECT value FROM vendor_history WHERE run_id = %s AND vendor_id = %s AND field = %s AND "
                      "valid_from <= %s ORDER BY valid_from DESC LIMIT 1", (self.run_id, vendor_id, field, at.isoformat()))
        return rows[0][0] if rows else None

    def table(self, name: str) -> list[dict]:
        cols = {"vendors": "vendor_id, name, iban, phone, email, frozen_at, frozen_trace",
                "vendor_history": "vendor_id, field, value, valid_from, source",
                "bank_changes": "request_id, vendor_id, new_iban, requested_at, via, outcome, decided_at, trace_id",
                "payments": "invoice_no, vendor_id, run_at, amount, iban, decision, trace_id"}[name]
        order = {"vendors": "vendor_id", "vendor_history": "valid_from, vendor_id", "bank_changes": "requested_at",
                 "payments": "run_at, invoice_no"}[name]
        rows = self.q(f"SELECT {cols} FROM {name} WHERE run_id = %s ORDER BY {order}", (self.run_id,))
        keys = [c.strip() for c in cols.split(",")]
        return [{k: float(v) if k == "amount" else v for k, v in zip(keys, row)} for row in rows]


class SqliteLedger(Ledger):
    kind = "SQLite in memory, a local copy of the schema"

    def __init__(self, run_id: str):
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        super().__init__(run_id)

    def _execute(self, sql, params, many=False):
        sql = sql.replace("%s", "?")
        cur = self._db.executemany(sql, params) if many else self._db.execute(sql, params)
        rows = cur.fetchall() if cur.description else []
        self._db.commit()
        return rows


class PgLedger(Ledger):
    kind = "Postgres in Meko (AMP), datapack sentinel-audit"

    def __init__(self, run_id: str):
        import psycopg
        e = os.environ
        self._connect = lambda: psycopg.connect(
            host=e["MEKO_PG_HOST"], port=int(e["MEKO_PG_PORT"]), user=e["MEKO_PG_USER"], password=e["MEKO_PG_PASSWORD"],
            dbname=e.get("MEKO_PG_DATABASE", "postgres"), sslmode="require", connect_timeout=10, autocommit=True)
        self._db = self._connect()
        super().__init__(run_id)

    def _execute(self, sql, params, many=False):
        import psycopg
        for attempt in (1, 2):
            try:
                with self._db.cursor() as cur:
                    if many:
                        cur.executemany(sql, params)
                    else:
                        cur.execute(sql, params)
                    return cur.fetchall() if cur.description else []
            except psycopg.OperationalError:
                if attempt == 2:
                    raise
                self._db = self._connect()          # a dropped connection: reconnect once
        return []


def open_ledger(run_id: str) -> Ledger:
    """Postgres in Meko when its connection details are in the environment (`.env`) and it answers; else SQLite."""
    if os.environ.get("MEKO_PG_HOST") and os.environ.get("MEKO_PG_PASSWORD"):
        try:
            return PgLedger(run_id)
        except Exception:                           # unreachable (another IP, offline): fall back, say so on screen
            pass
    return SqliteLedger(run_id)
