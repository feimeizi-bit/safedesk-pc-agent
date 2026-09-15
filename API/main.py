import os
import traceback

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from agent.llm_client import chat_completion
from agent.simple_agent import run_agent
from agent.task_manager import AgentBusyError, AgentTaskManager
from agent.trace_store import get_recent_traces, get_trace
from sandbox import get_audit_log


app = FastAPI(title="PC Agent")


def _configured_max_runtime() -> float:
    try:
        value = float(os.environ.get("PC_AGENT_MAX_RUNTIME_SECONDS", "120"))
    except ValueError:
        return 120.0
    return max(1.0, min(value, 3600.0))


task_manager = AgentTaskManager(max_runtime_seconds=_configured_max_runtime())

class Query(BaseModel):
    text: str = ""
    confirmation_token: str | None = None


def _submit_task(query: Query) -> dict:
    try:
        return task_manager.submit(
            query.text,
            chat_completion,
            confirmation_token=query.confirmation_token,
            runner=run_agent,
        )
    except AgentBusyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "agent_busy",
                "message": str(exc),
                "active_task_id": exc.active_task_id,
                "retryable": True,
            },
        ) from exc


@app.post("/chat")
async def chat(query: Query):
    """Compatibility endpoint that waits while sharing the same admission gate."""
    submitted = _submit_task(query)
    try:
        finished = await task_manager.wait(submitted["task_id"])
        if finished is None or finished["result"] is None:
            raise RuntimeError("Agent task finished without a result")
        return finished["result"]
    except Exception as e:
        print("\n========== 后端错误详情 ==========")
        traceback.print_exc()
        print("=================================\n")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tasks", status_code=202)
async def create_agent_task(query: Query):
    """Submit work and return immediately; excess work is rejected, not queued."""
    return _submit_task(query)


@app.get("/tasks/{task_id}")
async def get_agent_task(task_id: str):
    task = task_manager.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/tasks/{task_id}/cancel", status_code=202)
async def cancel_agent_task(task_id: str):
    task = task_manager.cancel(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/audit_log")
async def audit_log(limit: int = 10):
    logs = await run_in_threadpool(get_audit_log, limit)
    # 将每条日志转换为字典，方便前端显示
    formatted = []
    for row in logs:
        # row 结构: (timestamp, tool_name, params, result, user_confirmed)
        formatted.append({
            "timestamp": row[0],
            "tool": row[1],
            "params": row[2],
            "result": row[3][:50],  # 截断过长结果
            "confirmed": bool(row[4])
        })
    return {"logs": formatted}


@app.get("/traces")
async def traces(limit: int = 20):
    return {"traces": await run_in_threadpool(get_recent_traces, limit)}


@app.get("/traces/{trace_id}")
async def trace(trace_id: str):
    stored_trace = await run_in_threadpool(get_trace, trace_id)
    if stored_trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return stored_trace
