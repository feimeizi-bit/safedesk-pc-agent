import sqlite3

from fastapi.testclient import TestClient

import sandbox
from API import main
from agent.simple_agent import AgentResult, AgentStatus
from agent.trace_store import get_trace, record_trace


def test_chat_passes_confirmation_token(monkeypatch):
    captured = {}

    def fake_agent(
        text,
        llm_func,
        *,
        confirmation_token=None,
        trace_id=None,
        **_kwargs,
    ):
        captured.update(text=text, confirmation_token=confirmation_token)
        return AgentResult(
            response="ok",
            status=AgentStatus.COMPLETED,
            plan_hash="abc",
            trace_id=trace_id,
        )

    monkeypatch.setattr(main, "run_agent", fake_agent)
    client = TestClient(main.app)
    response = client.post(
        "/chat",
        json={"text": "", "confirmation_token": "one-time-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "response": "ok",
        "status": "completed",
        "confirmation_token": None,
        "plan_hash": "abc",
        "expires_at": None,
        "trace_id": payload["trace_id"],
    }
    assert payload["trace_id"]
    assert captured == {"text": "", "confirmation_token": "one-time-token"}


def test_get_trace_returns_exact_record(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "AUDIT_DB_PATH", tmp_path / "audit.db")
    record_trace(
        trace_id="trace-target",
        user_input="查看CPU",
        status="completed",
        response="CPU使用率为10%",
        plan_hash=None,
        duration_ms=12.5,
        is_confirmation=False,
    )
    record_trace(
        trace_id="trace-other",
        user_input="其他请求",
        status="refused",
        response="未执行",
        plan_hash=None,
        duration_ms=3.0,
        is_confirmation=False,
    )

    response = TestClient(main.app).get("/traces/trace-target")

    assert response.status_code == 200
    assert response.json() == {
        "trace_id": "trace-target",
        "created_at": response.json()["created_at"],
        "user_input": "查看CPU",
        "status": "completed",
        "response": "CPU使用率为10%",
        "plan_hash": None,
        "duration_ms": 12.5,
        "is_confirmation": False,
        "queue_ms": 0.0,
        "model_ms": 0.0,
        "tool_ms": 0.0,
        "total_ms": 12.5,
    }


def test_get_trace_returns_404_for_unknown_id(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "AUDIT_DB_PATH", tmp_path / "audit.db")

    response = TestClient(main.app).get("/traces/trace-does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Trace not found"}


def test_existing_trace_database_is_migrated_without_losing_duration(
    tmp_path, monkeypatch
):
    database = tmp_path / "audit.db"
    monkeypatch.setattr(sandbox, "AUDIT_DB_PATH", database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE agent_traces (
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
        connection.execute(
            "INSERT INTO agent_traces VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("old-trace", 1.0, "旧请求", "completed", "ok", None, 12.5, 0),
        )

    migrated = get_trace("old-trace")

    assert migrated is not None
    assert migrated["duration_ms"] == 12.5
    assert migrated["queue_ms"] == 0
    assert migrated["model_ms"] == 0
    assert migrated["tool_ms"] == 0
    assert migrated["total_ms"] == 12.5
