from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass
class QueryResult:
    """Fully materialized SQLite result that is safe to use after releasing a lock."""

    rows: list[sqlite3.Row]
    rowcount: int
    lastrowid: int | None = None

    def fetchall(self) -> list[sqlite3.Row]:
        return self.rows

    def fetchone(self) -> sqlite3.Row | None:
        return self.rows[0] if self.rows else None
