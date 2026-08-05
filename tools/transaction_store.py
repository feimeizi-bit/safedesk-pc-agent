"""Durable manifests for file operations with real side effects."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from pathlib import Path

import sandbox


def _connect() -> sqlite3.Connection:
    sandbox.init_audit_db()
    connection = sqlite3.connect(str(sandbox.AUDIT_DB_PATH), timeout=10)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS file_transactions (
            transaction_id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            status TEXT NOT NULL,
            operations_json TEXT NOT NULL,
            completed_count INTEGER NOT NULL DEFAULT 0,
            result TEXT
        )
        """
    )
    connection.commit()
    return connection


def create_file_transaction(operations: list[tuple[Path, Path]]) -> str:
    transaction_id = secrets.token_urlsafe(12)
    payload = [
        {"source": str(source), "destination": str(destination)}
        for source, destination in operations
    ]
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO file_transactions (
                transaction_id, created_at, status, operations_json
            ) VALUES (?, ?, 'running', ?)
            """,
            (transaction_id, time.time(), json.dumps(payload, ensure_ascii=False)),
        )
    return transaction_id


def update_file_transaction(
    transaction_id: str,
    *,
    status: str,
    completed_count: int,
    result: str,
) -> None:
    with _connect() as connection:
        connection.execute(
            """
            UPDATE file_transactions
            SET status = ?, completed_count = ?, result = ?
            WHERE transaction_id = ?
            """,
            (status, completed_count, result, transaction_id),
        )


def get_file_transaction(transaction_id: str) -> dict | None:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT transaction_id, created_at, status, operations_json,
                   completed_count, result
            FROM file_transactions WHERE transaction_id = ?
            """,
            (transaction_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "transaction_id": row[0],
        "created_at": row[1],
        "status": row[2],
        "operations": json.loads(row[3]),
        "completed_count": row[4],
        "result": row[5],
    }
