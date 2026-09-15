"""Lightweight execution traces used by debugging and offline evaluation."""

from __future__ import annotations

import sqlite3
import threading
import time

import sandbox


_SCHEMA_LOCK = threading.Lock()


def _trace_from_row(row: tuple) -> dict:
    """Convert the SQLite row shape shared by trace queries into API data."""
    return {
        "trace_id": row[0],
        "created_at": row[1],
        "user_input": row[2],
        "status": row[3],
        "response": row[4],
        "plan_hash": row[5],
        "duration_ms": row[6],
        "is_confirmation": bool(row[7]),
        "queue_ms": row[8],
        "model_ms": row[9],
        "tool_ms": row[10],
        "total_ms": row[11],
    }


def _connect() -> sqlite3.Connection:
    sandbox.init_audit_db()
    connection = sqlite3.connect(str(sandbox.AUDIT_DB_PATH), timeout=10)
    with _SCHEMA_LOCK:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_traces (
                trace_id TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                user_input TEXT NOT NULL,
                status TEXT NOT NULL,
                response TEXT NOT NULL,
                plan_hash TEXT,
                duration_ms REAL NOT NULL,
                is_confirmation INTEGER NOT NULL,
                queue_ms REAL NOT NULL DEFAULT 0,
                model_ms REAL NOT NULL DEFAULT 0,
                tool_ms REAL NOT NULL DEFAULT 0,
                total_ms REAL NOT NULL DEFAULT 0
            )
            """
        )
        existing_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(agent_traces)")
        }
        migrations = {
            "queue_ms": "ALTER TABLE agent_traces ADD COLUMN queue_ms REAL NOT NULL DEFAULT 0",
            "model_ms": "ALTER TABLE agent_traces ADD COLUMN model_ms REAL NOT NULL DEFAULT 0",
            "tool_ms": "ALTER TABLE agent_traces ADD COLUMN tool_ms REAL NOT NULL DEFAULT 0",
            "total_ms": "ALTER TABLE agent_traces ADD COLUMN total_ms REAL NOT NULL DEFAULT 0",
        }
        for column, statement in migrations.items():
            if column not in existing_columns:
                connection.execute(statement)
                if column == "total_ms":
                    connection.execute(
                        "UPDATE agent_traces SET total_ms = duration_ms"
                    )
        connection.commit()
    return connection


def record_trace(
    *,
    trace_id: str,
    user_input: str,
    status: str,
    response: str,
    plan_hash: str | None,
    duration_ms: float,
    is_confirmation: bool,
    queue_ms: float = 0.0,
    model_ms: float = 0.0,
    tool_ms: float = 0.0,
    total_ms: float | None = None,
) -> None:
    total_ms = duration_ms if total_ms is None else total_ms
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO agent_traces (
                trace_id, created_at, user_input, status, response,
                plan_hash, duration_ms, is_confirmation, queue_ms,
                model_ms, tool_ms, total_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                time.time(),
                user_input,
                status,
                response,
                plan_hash,
                duration_ms,
                1 if is_confirmation else 0,
                queue_ms,
                model_ms,
                tool_ms,
                total_ms,
            ),
        )


def get_recent_traces(limit: int = 20) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT trace_id, created_at, user_input, status, response,
                   plan_hash, duration_ms, is_confirmation, queue_ms,
                   model_ms, tool_ms, total_ms
            FROM agent_traces ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_trace_from_row(row) for row in rows]


def get_trace(trace_id: str) -> dict | None:
    """Return one exact trace, or None when the identifier does not exist."""
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT trace_id, created_at, user_input, status, response,
                   plan_hash, duration_ms, is_confirmation, queue_ms,
                   model_ms, tool_ms, total_ms
            FROM agent_traces WHERE trace_id = ?
            """,
            (trace_id,),
        ).fetchone()
    return _trace_from_row(row) if row is not None else None
