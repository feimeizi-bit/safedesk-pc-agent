import asyncio
import threading
import time

import httpx
import pytest

import sandbox
from API import main
from agent.plan_store import claim_pending_plan, create_pending_plan
from agent.simple_agent import AgentResult, AgentStatus
from agent.task_manager import AgentTaskManager


async def wait_for_status(client, task_id, expected, timeout=1.0):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        response = await client.get(f"/tasks/{task_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] == expected:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError(f"task {task_id} did not reach {expected}")


@pytest.mark.asyncio
async def test_busy_rejects_immediately_while_control_plane_responds(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(sandbox, "AUDIT_DB_PATH", tmp_path / "audit.db")
    monkeypatch.setattr(
        main,
        "task_manager",
        AgentTaskManager(max_runtime_seconds=5),
    )
    pending_plan = create_pending_plan(
        "查看CPU",
        [{"tool": "get_cpu_usage", "params": {}}],
        "将查看CPU",
    )
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def fake_agent(text, _llm_func, *, trace_id=None, **_kwargs):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(2)
        return AgentResult(
            response=f"finished: {text}",
            status=AgentStatus.COMPLETED,
            trace_id=trace_id,
        )

    monkeypatch.setattr(main, "run_agent", fake_agent)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        accepted = await client.post("/tasks", json={"text": "slow"})
        assert accepted.status_code == 202
        task_id = accepted.json()["task_id"]
        assert await asyncio.to_thread(started.wait, 1)

        rejected_started = time.perf_counter()
        rejected = await client.post("/tasks", json={"text": "second"})
        rejected_ms = (time.perf_counter() - rejected_started) * 1000
        assert rejected.status_code == 503
        assert rejected.json()["detail"] == {
            "code": "agent_busy",
            "message": "当前有 Agent 任务正在执行",
            "active_task_id": task_id,
            "retryable": True,
        }
        assert rejected_ms < 500
        assert calls == 1

        legacy_chat = await client.post("/chat", json={"text": "bypass attempt"})
        assert legacy_chat.status_code == 503
        assert legacy_chat.json()["detail"]["active_task_id"] == task_id
        assert calls == 1

        busy_confirmation = await client.post(
            "/tasks",
            json={"confirmation_token": pending_plan.token},
        )
        assert busy_confirmation.status_code == 503
        assert calls == 1

        traces_started = time.perf_counter()
        traces = await client.get("/traces?limit=1")
        traces_ms = (time.perf_counter() - traces_started) * 1000
        assert traces.status_code == 200
        assert traces_ms < 500

        release.set()
        finished = await wait_for_status(client, task_id, "completed")
        assert finished["result"]["response"] == "finished: slow"
        assert finished["metrics"]["queue_ms"] is not None
        assert finished["metrics"]["total_ms"] >= finished["metrics"]["queue_ms"]

        # Admission happens before one-time token consumption, so busy cannot
        # destroy a confirmation that the user has not executed yet.
        claimed = await asyncio.to_thread(claim_pending_plan, pending_plan.token)
        assert claimed.plan_hash == pending_plan.plan_hash


@pytest.mark.asyncio
async def test_cancel_does_not_release_capacity_until_worker_really_stops(monkeypatch):
    monkeypatch.setattr(
        main,
        "task_manager",
        AgentTaskManager(max_runtime_seconds=5),
    )
    started = threading.Event()
    stop_observed = threading.Event()
    release = threading.Event()

    def fake_agent(text, _llm_func, *, trace_id=None, run_control=None, **_kwargs):
        if text == "next":
            return AgentResult(
                response="next completed",
                status=AgentStatus.COMPLETED,
                trace_id=trace_id,
            )
        started.set()
        assert run_control.wait(2)
        stop_observed.set()
        assert release.wait(2)
        run_control.raise_if_stopped()

    monkeypatch.setattr(main, "run_agent", fake_agent)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        accepted = await client.post("/tasks", json={"text": "slow"})
        task_id = accepted.json()["task_id"]
        assert await asyncio.to_thread(started.wait, 1)

        cancelled = await client.post(f"/tasks/{task_id}/cancel")
        assert cancelled.status_code == 202
        assert cancelled.json()["status"] == "cancel_requested"
        assert await asyncio.to_thread(stop_observed.wait, 1)

        still_busy = await client.post("/tasks", json={"text": "too early"})
        assert still_busy.status_code == 503

        release.set()
        finished = await wait_for_status(client, task_id, "cancelled")
        assert finished["stop_reason"] == "cancelled"

        next_task = await client.post("/tasks", json={"text": "next"})
        assert next_task.status_code == 202
        await wait_for_status(client, next_task.json()["task_id"], "completed")


@pytest.mark.asyncio
async def test_timeout_keeps_slot_until_uninterruptible_worker_returns(monkeypatch):
    monkeypatch.setattr(
        main,
        "task_manager",
        AgentTaskManager(max_runtime_seconds=0.05),
    )
    timeout_observed = threading.Event()
    release = threading.Event()

    def fake_agent(_text, _llm_func, *, run_control=None, **_kwargs):
        assert run_control.wait(1)
        timeout_observed.set()
        assert release.wait(2)
        run_control.raise_if_stopped()

    monkeypatch.setattr(main, "run_agent", fake_agent)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        accepted = await client.post("/tasks", json={"text": "slow"})
        task_id = accepted.json()["task_id"]
        assert await asyncio.to_thread(timeout_observed.wait, 1)

        timing_out = await client.get(f"/tasks/{task_id}")
        assert timing_out.json()["status"] == "timeout_requested"
        assert timing_out.json()["stop_reason"] == "timed_out"
        assert (await client.post("/tasks", json={"text": "second"})).status_code == 503

        release.set()
        await wait_for_status(client, task_id, "timed_out")
