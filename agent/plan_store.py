"""Persistent, one-time confirmation plans for high-risk Agent actions."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass

import sandbox


DEFAULT_PLAN_TTL_SECONDS = 10 * 60


class PendingPlanError(ValueError):
    """Raised when a confirmation token cannot safely claim a pending plan."""


@dataclass(frozen=True)
class PendingPlan:
    token: str
    plan_hash: str
    expires_at: float


@dataclass(frozen=True)
class ClaimedPlan:
    token_hash: str
    plan_hash: str
    user_input: str
    calls: list[dict]


def canonical_plan_json(calls: list[dict]) -> str:
    return json.dumps(calls, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def calculate_plan_hash(calls: list[dict]) -> str:
    return hashlib.sha256(canonical_plan_json(calls).encode("utf-8")).hexdigest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _connect() -> sqlite3.Connection:
    sandbox.init_audit_db()
    connection = sqlite3.connect(str(sandbox.AUDIT_DB_PATH), timeout=10)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_plans (
            token_hash TEXT PRIMARY KEY,
            plan_hash TEXT NOT NULL,
            user_input TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            preview TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            status TEXT NOT NULL,
            result TEXT
        )
        """
    )
    connection.commit()
    return connection


def create_pending_plan(
    user_input: str,
    calls: list[dict],
    preview: str,
    *,
    ttl_seconds: int = DEFAULT_PLAN_TTL_SECONDS,
) -> PendingPlan:
    if not calls:
        raise PendingPlanError("不能保存空计划")
    ttl_seconds = max(30, min(int(ttl_seconds), 60 * 60))
    token = secrets.token_urlsafe(24)
    token_hash = _token_hash(token)
    plan_hash = calculate_plan_hash(calls)
    created_at = time.time()
    expires_at = created_at + ttl_seconds

    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO pending_plans (
                token_hash, plan_hash, user_input, plan_json, preview,
                created_at, expires_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                token_hash,
                plan_hash,
                user_input,
                canonical_plan_json(calls),
                preview,
                created_at,
                expires_at,
            ),
        )
    return PendingPlan(token=token, plan_hash=plan_hash, expires_at=expires_at)


def claim_pending_plan(token: str) -> ClaimedPlan:
    """Atomically consume a token before any side effect is executed."""
    if not token or len(token) > 256:
        raise PendingPlanError("确认令牌无效")
    token_hash = _token_hash(token)
    now = time.time()
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT plan_hash, user_input, plan_json, expires_at, status
            FROM pending_plans WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if row is None:
            raise PendingPlanError("确认令牌无效或已失效")

        plan_hash, user_input, plan_json, expires_at, status = row
        if status != "pending":
            raise PendingPlanError("确认令牌已使用，不能重复执行")
        if expires_at < now:
            connection.execute(
                "UPDATE pending_plans SET status = 'expired' WHERE token_hash = ?",
                (token_hash,),
            )
            connection.commit()
            raise PendingPlanError("确认令牌已过期，请重新生成操作预览")

        calls = json.loads(plan_json)
        if calculate_plan_hash(calls) != plan_hash:
            connection.execute(
                "UPDATE pending_plans SET status = 'invalid' WHERE token_hash = ?",
                (token_hash,),
            )
            connection.commit()
            raise PendingPlanError("已保存计划完整性校验失败")

        updated = connection.execute(
            """
            UPDATE pending_plans SET status = 'executing'
            WHERE token_hash = ? AND status = 'pending'
            """,
            (token_hash,),
        ).rowcount
        if updated != 1:
            raise PendingPlanError("确认令牌已被其他请求使用")
        connection.commit()
        return ClaimedPlan(
            token_hash=token_hash,
            plan_hash=plan_hash,
            user_input=user_input,
            calls=calls,
        )
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def finish_claimed_plan(token_hash: str, *, success: bool, result: str) -> None:
    status = "completed" if success else "failed"
    with _connect() as connection:
        connection.execute(
            """
            UPDATE pending_plans SET status = ?, result = ?
            WHERE token_hash = ? AND status = 'executing'
            """,
            (status, result, token_hash),
        )
