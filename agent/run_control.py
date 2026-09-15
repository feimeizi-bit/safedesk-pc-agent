"""Thread-safe cancellation controls and timing counters for Agent runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import threading
import time


class StopReason(str, Enum):
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class AgentRunStopped(RuntimeError):
    """Raised at a safe boundary after cancellation or timeout is requested."""

    def __init__(self, reason: StopReason):
        self.reason = reason
        message = "任务已取消" if reason is StopReason.CANCELLED else "任务执行超时"
        super().__init__(message)


@dataclass
class AgentRunControl:
    """Cooperative stop signal shared by the event loop and worker thread."""

    deadline_monotonic: float | None = None
    _event: threading.Event = field(default_factory=threading.Event, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _reason: StopReason | None = field(default=None, init=False)

    def request_stop(self, reason: StopReason) -> bool:
        """Keep the first stop reason so a later timeout cannot rewrite cancellation."""
        with self._lock:
            if self._reason is not None:
                return False
            self._reason = reason
            self._event.set()
            return True

    @property
    def stop_reason(self) -> StopReason | None:
        with self._lock:
            return self._reason

    def is_stop_requested(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Allow blocking code and deterministic tests to wait for a stop request."""
        return self._event.wait(timeout)

    def raise_if_stopped(self) -> None:
        if (
            self.deadline_monotonic is not None
            and time.perf_counter() >= self.deadline_monotonic
        ):
            self.request_stop(StopReason.TIMED_OUT)
        reason = self.stop_reason
        if reason is not None:
            raise AgentRunStopped(reason)


@dataclass
class AgentRunMetrics:
    """Accumulate time spent inside model and tool calls from a worker thread."""

    _model_ms: float = 0.0
    _tool_ms: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def add_model_time(self, duration_ms: float) -> None:
        with self._lock:
            self._model_ms += duration_ms

    def add_tool_time(self, duration_ms: float) -> None:
        with self._lock:
            self._tool_ms += duration_ms

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return {
                "model_ms": self._model_ms,
                "tool_ms": self._tool_ms,
            }
