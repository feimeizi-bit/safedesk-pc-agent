"""Lightweight execution traces used by debugging and offline evaluation."""

from __future__ import annotations

import sqlite3
import time

import sandbox


def _connect() -> sqlite3.Connection:
    sandbox.init_audit_db()
    connection = sqlite3.connect(str(sandbox.AUDIT_DB_PATH), timeout=10)
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
            is_confirmation INTEGER NOT NULL
        )
        """
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
) -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO agent_traces (
                trace_id, created_at, user_input, status, response,
                plan_hash, duration_ms, is_confirmation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )


def get_recent_traces(limit: int = 20) -> list[dict]:
    limit = max(1, min(int(limit), 200))
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT trace_id, created_at, user_input, status, response,
                   plan_hash, duration_ms, is_confirmation
            FROM agent_traces ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "trace_id": row[0],
            "created_at": row[1],
            "user_input": row[2],
            "status": row[3],
            "response": row[4],
            "plan_hash": row[5],
            "duration_ms": row[6],
            "is_confirmation": bool(row[7]),
        }
        for row in rows
    ]
