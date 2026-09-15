"""In-process admission control for the single SafeDesk execution surface."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import secrets
import threading
import time
from typing import Callable

from fastapi.concurrency import run_in_threadpool

from agent.run_control import (
    AgentRunControl,
    AgentRunMetrics,
    AgentRunStopped,
    StopReason,
)
from agent.simple_agent import AgentResult, AgentStatus


class TaskStatus(str, Enum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    TIMEOUT_REQUESTED = "timeout_requested"
    COMPLETED = "completed"
    CONFIRMATION_REQUIRED = "confirmation_required"
    REFUSED = "refused"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


TERMINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.CONFIRMATION_REQUIRED,
    TaskStatus.REFUSED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.TIMED_OUT,
}


class AgentBusyError(RuntimeError):
    def __init__(self, active_task_id: str):
        self.active_task_id = active_task_id
        super().__init__("当前有 Agent 任务正在执行")


@dataclass
class ManagedTask:
    task_id: str
    status: TaskStatus
    created_at: float
    submitted_monotonic: float
    max_runtime_seconds: float
    control: AgentRunControl
    run_metrics: AgentRunMetrics
    done_event: asyncio.Event
    started_at: float | None = None
    started_monotonic: float | None = None
    finished_at: float | None = None
    finished_monotonic: float | None = None
    queue_ms: float | None = None
    result: dict | None = None
    error: str | None = None
    background_task: asyncio.Task | None = None
    timeout_task: asyncio.Task | None = None


class AgentTaskManager:
    """Accept one task, reject extra work, and keep control-plane state responsive."""

    def __init__(self, *, max_runtime_seconds: float = 120.0, max_history: int = 100):
        if max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be positive")
        self.max_runtime_seconds = float(max_runtime_seconds)
        self.max_history = max(1, int(max_history))
        self._lock = threading.Lock()
        self._records: dict[str, ManagedTask] = {}
        self._active_task_id: str | None = None

    def submit(
        self,
        user_input: str,
        llm_func,
        *,
        confirmation_token: str | None,
        runner: Callable,
    ) -> dict:
        """Atomically admit work and start it without creating a hidden queue."""
        loop = asyncio.get_running_loop()
        submitted_monotonic = time.perf_counter()
        task_id = secrets.token_urlsafe(12)
        control = AgentRunControl(
            deadline_monotonic=submitted_monotonic + self.max_runtime_seconds
        )
        record = ManagedTask(
            task_id=task_id,
            status=TaskStatus.ACCEPTED,
            created_at=time.time(),
            submitted_monotonic=submitted_monotonic,
            max_runtime_seconds=self.max_runtime_seconds,
            control=control,
            run_metrics=AgentRunMetrics(),
            done_event=asyncio.Event(),
        )

        with self._lock:
            if self._active_task_id is not None:
                raise AgentBusyError(self._active_task_id)
            self._prune_locked()
            self._records[task_id] = record
            self._active_task_id = task_id

        try:
            record.timeout_task = loop.create_task(self._watch_timeout(record))
            record.background_task = loop.create_task(
                self._run(record, user_input, llm_func, confirmation_token, runner)
            )
        except Exception:
            if record.timeout_task is not None:
                record.timeout_task.cancel()
            if record.background_task is not None:
                record.background_task.cancel()
            with self._lock:
                self._records.pop(task_id, None)
                if self._active_task_id == task_id:
                    self._active_task_id = None
            raise
        return self._snapshot(record)

    def get(self, task_id: str) -> dict | None:
        with self._lock:
            record = self._records.get(task_id)
            return self._snapshot(record) if record is not None else None

    async def wait(self, task_id: str) -> dict | None:
        with self._lock:
            record = self._records.get(task_id)
        if record is None:
            return None
        await record.done_event.wait()
        return self.get(task_id)

    def cancel(self, task_id: str) -> dict | None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return None
            if record.status in TERMINAL_STATUSES:
                return self._snapshot(record)
            if record.control.request_stop(StopReason.CANCELLED):
                record.status = TaskStatus.CANCEL_REQUESTED
            return self._snapshot(record)

    async def _watch_timeout(self, record: ManagedTask) -> None:
        try:
            await asyncio.sleep(record.max_runtime_seconds)
        except asyncio.CancelledError:
            return
        with self._lock:
            if record.status in TERMINAL_STATUSES:
                return
            if record.control.request_stop(StopReason.TIMED_OUT):
                record.status = TaskStatus.TIMEOUT_REQUESTED

    async def _run(
        self,
        record: ManagedTask,
        user_input: str,
        llm_func,
        confirmation_token: str | None,
        runner: Callable,
    ) -> None:
        def invoke_runner() -> AgentResult:
            """Mark the real worker-thread start, then enter the sync runtime."""
            started_monotonic = time.perf_counter()
            with self._lock:
                record.started_at = time.time()
                record.started_monotonic = started_monotonic
                record.queue_ms = (
                    started_monotonic - record.submitted_monotonic
                ) * 1000
                if record.status is TaskStatus.ACCEPTED:
                    record.status = TaskStatus.RUNNING
                queue_ms = record.queue_ms
            return runner(
                user_input,
                llm_func,
                confirmation_token=confirmation_token,
                trace_id=record.task_id,
                run_control=record.control,
                run_metrics=record.run_metrics,
                queue_ms=queue_ms,
            )

        result: AgentResult
        try:
            result = await run_in_threadpool(invoke_runner)
        except AgentRunStopped as exc:
            status = (
                AgentStatus.CANCELLED
                if exc.reason is StopReason.CANCELLED
                else AgentStatus.TIMED_OUT
            )
            result = AgentResult(response=str(exc), status=status, trace_id=record.task_id)
        except Exception as exc:
            result = AgentResult(
                response=f"Agent 任务失败：{type(exc).__name__}: {exc}",
                status=AgentStatus.FAILED,
                trace_id=record.task_id,
            )
            with self._lock:
                record.error = f"{type(exc).__name__}: {exc}"

        finished_monotonic = time.perf_counter()
        with self._lock:
            record.result = result.model_dump(mode="json")
            record.status = TaskStatus(result.status.value)
            record.finished_at = time.time()
            record.finished_monotonic = finished_monotonic
            if self._active_task_id == record.task_id:
                self._active_task_id = None
            if record.timeout_task is not None:
                record.timeout_task.cancel()
            record.done_event.set()

    def _snapshot(self, record: ManagedTask) -> dict:
        timing = record.run_metrics.snapshot()
        end = record.finished_monotonic or time.perf_counter()
        return {
            "task_id": record.task_id,
            "status": record.status.value,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "max_runtime_seconds": record.max_runtime_seconds,
            "stop_reason": (
                record.control.stop_reason.value
                if record.control.stop_reason is not None
                else None
            ),
            "metrics": {
                "queue_ms": record.queue_ms,
                **timing,
                "total_ms": (end - record.submitted_monotonic) * 1000,
            },
            "result": record.result,
            "error": record.error,
        }

    def _prune_locked(self) -> None:
        completed = [
            record
            for record in self._records.values()
            if record.status in TERMINAL_STATUSES
        ]
        excess = len(self._records) - self.max_history + 1
        if excess <= 0:
            return
        completed.sort(key=lambda item: item.finished_at or item.created_at)
        for record in completed[:excess]:
            self._records.pop(record.task_id, None)
