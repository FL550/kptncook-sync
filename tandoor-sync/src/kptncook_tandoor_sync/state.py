from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1


class StateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImportRecord:
    source_oid: str
    status: str
    tandoor_id: int | None
    updated_at: str


class StateStore:
    def __init__(self, path: Path, source_account: str, destination: str):
        self.path = path
        self.source_account = source_account
        self.destination = destination

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA synchronous = FULL")
        db.execute("PRAGMA busy_timeout = 30000")
        return db

    def initialize(self) -> None:
        try:
            with closing(self.connect()) as db:
                db.execute("PRAGMA journal_mode = DELETE")
                db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS imports (
                        source_oid TEXT PRIMARY KEY,
                        status TEXT NOT NULL CHECK(status IN ('in_flight', 'uncertain', 'imported')),
                        tandoor_id INTEGER,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        CHECK((status = 'imported' AND tandoor_id IS NOT NULL)
                           OR (status != 'imported' AND tandoor_id IS NULL))
                    );
                    """
                )
                expected = {
                    "schema_version": str(SCHEMA_VERSION),
                    "source_account": self.source_account,
                    "destination": self.destination,
                }
                existing = dict(db.execute("SELECT key, value FROM metadata"))
                for key, value in expected.items():
                    if key in existing and existing[key] != value:
                        raise StateError(
                            f"State is bound to a different {key.replace('_', ' ')}; "
                            "use the matching configuration or a separate state file"
                        )
                if "schema_version" in existing and existing["schema_version"] != str(SCHEMA_VERSION):
                    raise StateError("Unsupported state database schema version")
                db.executemany(
                    "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
                    expected.items(),
                )
                db.execute(
                    "UPDATE imports SET status='uncertain', updated_at=CURRENT_TIMESTAMP "
                    "WHERE status='in_flight'"
                )
        except (sqlite3.Error, OSError) as exc:
            raise StateError("Unable to initialize durable sync history") from exc

    def get_status(self, source_oid: str) -> str | None:
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT status FROM imports WHERE source_oid = ?", (source_oid,)
            ).fetchone()
            return None if row is None else str(row["status"])

    def begin_create(self, source_oid: str) -> None:
        try:
            with closing(self.connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO imports(source_oid, status) VALUES (?, 'in_flight')",
                    (source_oid,),
                )
                db.commit()
        except sqlite3.Error as exc:
            raise StateError("Could not durably record the pre-create marker") from exc

    def mark_uncertain(self, source_oid: str) -> None:
        self._update(source_oid, "uncertain", None)

    def mark_imported(self, source_oid: str, tandoor_id: int) -> None:
        if tandoor_id < 1:
            raise ValueError("Tandoor ID must be positive")
        self._update(source_oid, "imported", tandoor_id)

    def _update(self, source_oid: str, status: str, tandoor_id: int | None) -> None:
        try:
            with closing(self.connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                cursor = db.execute(
                    "UPDATE imports SET status=?, tandoor_id=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE source_oid=? AND status IN ('in_flight', 'uncertain')",
                    (status, tandoor_id, source_oid),
                )
                if cursor.rowcount != 1:
                    raise StateError(f"No pending state record for source recipe {source_oid}")
                db.commit()
        except sqlite3.Error as exc:
            raise StateError("Could not persist recipe import state") from exc

    def list_uncertain(self) -> list[ImportRecord]:
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT source_oid, status, tandoor_id, updated_at FROM imports "
                "WHERE status IN ('uncertain', 'in_flight') ORDER BY source_oid"
            ).fetchall()
        return [ImportRecord(row["source_oid"], row["status"], row["tandoor_id"], row["updated_at"]) for row in rows]

    def reconcile_imported(self, source_oid: str, tandoor_id: int) -> None:
        self.mark_imported(source_oid, tandoor_id)

    def reconcile_clear(self, source_oid: str) -> None:
        try:
            with closing(self.connect()) as db:
                db.execute("BEGIN IMMEDIATE")
                cursor = db.execute(
                    "DELETE FROM imports WHERE source_oid=? AND status IN ('uncertain', 'in_flight')",
                    (source_oid,),
                )
                if cursor.rowcount != 1:
                    raise StateError(f"No uncertain state record for source recipe {source_oid}")
                db.commit()
        except sqlite3.Error as exc:
            raise StateError("Could not clear uncertain recipe state") from exc

