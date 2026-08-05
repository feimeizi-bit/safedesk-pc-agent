from fastapi import FastAPI,HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from agent.simple_agent import run_agent
from agent.llm_client import chat_completion
import traceback
from sandbox import get_audit_log
from agent.trace_store import get_recent_traces

app=FastAPI(title="PC Agent")

class Query(BaseModel):
    text: str = ""
    confirmation_token: str | None = None

@app.post("/chat")
async def chat(query:Query):
    try:
        result = await run_in_threadpool(
            run_agent,
            query.text,
            chat_completion,
            confirmation_token=query.confirmation_token,
        )
        return result.model_dump(mode="json")
    except Exception as e:
        print("\n========== 后端错误详情 ==========")
        traceback.print_exc()   # 这行会打印完整堆栈
        print("=================================\n")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/audit_log")
async def audit_log(limit: int = 10):
    logs = get_audit_log(limit)
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
    return {"traces": get_recent_traces(limit)}
