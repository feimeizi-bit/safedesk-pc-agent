"""A small, bounded PC Agent runtime built around typed tool calls."""

from __future__ import annotations

from enum import Enum
import secrets
import time

from pydantic import BaseModel

from agent.llm_client import chat_completion
from agent.plan_store import (
    PendingPlanError,
    claim_pending_plan,
    create_pending_plan,
    finish_claimed_plan,
)
from agent.tool_protocol import ToolProtocolError, parse_tool_calls
from agent.tool_registry import TOOL_REGISTRY, ToolDefinition
from agent.trace_store import record_trace
from sandbox import log_audit


# Kept as a compatibility view for older learning scripts.
TOOLS = {name: definition.handler for name, definition in TOOL_REGISTRY.items()}

SYSTEM_PROMPT = """你是一个本地 PC 助手。你只能选择以下工具：
- organize_by_extension(directory): 按扩展名整理目录。该操作具有副作用，系统会独立进行预览和用户确认。
- query_knowledge_base(question): 从本地知识库检索答案。
- get_cpu_usage(): 获取 CPU 使用率。
- get_memory_usage(): 获取内存使用率。
- get_system_summary(): 获取 CPU、内存和系统盘使用情况。
- get_top_processes(limit): 获取内存占用最高的进程，limit 为 1 到 10。
- open_app(app_name): 打开白名单应用，仅支持记事本、计算器、命令提示符、画图、控制面板、任务管理器。
- undo_file_transaction(transaction_id): 撤销一个已完成的文件整理事务，需要独立预览和确认。
- none: 用户闲聊、请求不明确或请求不在工具能力范围内时使用。

你必须只输出一个 JSON 数组，不要输出 Markdown 或解释。数组元素格式：
{"tool": "工具名", "params": {...}}

规则：
1. 最多输出 5 个调用，并按照执行顺序排列。
2. 不得编造工具、参数或用户确认状态。
3. 不得把 shell、PowerShell、删除系统文件等危险请求转换为 open_app。
4. 用户要求执行不支持的应用或高风险系统命令时选择 none。

示例：
用户：整理桌面上的材料文件夹
输出：[{"tool":"organize_by_extension","params":{"directory":"桌面/材料"}}]
用户：先查看 CPU，再打开计算器
输出：[{"tool":"get_cpu_usage","params":{}},{"tool":"open_app","params":{"app_name":"计算器"}}]
用户：我的电脑最近很卡，帮我诊断一下
输出：[{"tool":"get_system_summary","params":{}},{"tool":"get_top_processes","params":{"limit":5}},{"tool":"query_knowledge_base","params":{"question":"电脑运行缓慢怎么办？"}}]
用户：撤销事务 abc_123456
输出：[{"tool":"undo_file_transaction","params":{"transaction_id":"abc_123456"}}]
用户：你好
输出：[{"tool":"none","params":{}}]
"""


class AgentStatus(str, Enum):
    COMPLETED = "completed"
    CONFIRMATION_REQUIRED = "confirmation_required"
    REFUSED = "refused"
    FAILED = "failed"


class AgentResult(BaseModel):
    response: str
    status: AgentStatus
    confirmation_token: str | None = None
    plan_hash: str | None = None
    expires_at: float | None = None
    trace_id: str | None = None


def _friendly_no_tool_response() -> str:
    return "我是本地 PC 助手，可以查询系统状态、打开白名单应用、整理文件或查询本地知识库。"


ValidatedCall = tuple[ToolDefinition, dict, dict]


def _validated_plan(response: str) -> list[ValidatedCall]:
    raw_calls = parse_tool_calls(response)
    if any(call.tool == "none" for call in raw_calls):
        if len(raw_calls) != 1:
            raise ToolProtocolError("none 不能与其他工具同时调用")
        return []
    return _validate_stored_calls(
        [{"tool": call.tool, "params": call.params} for call in raw_calls]
    )


def _validate_stored_calls(calls: list[dict]) -> list[ValidatedCall]:
    """Revalidate persisted plans instead of trusting database contents."""
    plan: list[ValidatedCall] = []
    for call in calls:
        if not isinstance(call, dict) or not set(call).issubset(
            {"tool", "params", "prepared"}
        ) or not {"tool", "params"}.issubset(call):
            raise ToolProtocolError("已保存计划结构无效")
        definition = TOOL_REGISTRY.get(call["tool"])
        if definition is None:
            raise ToolProtocolError(f"拒绝未知工具: {call['tool']}")
        params = definition.validate_params(call["params"])
        prepared = call.get("prepared", {})
        if not isinstance(prepared, dict):
            raise ToolProtocolError("已保存计划的执行上下文无效")
        plan.append((definition, params, prepared))
    if not plan or len(plan) > 5:
        raise ToolProtocolError("已保存计划的步骤数量无效")
    return plan


def _serialized_plan(plan: list[ValidatedCall]) -> list[dict]:
    calls: list[dict] = []
    for definition, params, prepared in plan:
        call = {"tool": definition.name, "params": params}
        if prepared:
            call["prepared"] = prepared
        calls.append(call)
    return calls


def _result_failed(result: str) -> bool:
    failure_markers = (
        "安全限制",
        "操作已取消",
        "执行失败",
        "启动失败",
        "模型调用失败",
        "无法生成预览",
    )
    return result.startswith(failure_markers) or "执行失败" in result


def _execute_plan(
    plan: list[ValidatedCall],
    *,
    confirmed: bool,
) -> tuple[str, bool, list[dict]]:
    results: list[str] = []
    steps: list[dict] = []
    success = True
    for definition, params, prepared in plan:
        invocation_params = dict(params)
        invocation_params.update(prepared)
        if definition.requires_confirmation:
            invocation_params["confirmed"] = confirmed
        try:
            result = str(definition.handler(**invocation_params))
        except Exception as exc:
            result = f"工具 {definition.name} 执行失败：{type(exc).__name__}: {exc}"
        if _result_failed(result):
            success = False
        log_audit(
            definition.name,
            params,
            result,
            user_confirmed=definition.requires_confirmation and confirmed,
        )
        results.append(result)
        steps.append(
            {
                "tool": definition.name,
                "params": params,
                "result": result,
                "success": not _result_failed(result),
            }
        )
        if not success:
            break
    return "\n".join(results), success, steps


def _summarize_tool_results(user_input: str, steps: list[dict], llm_func) -> str:
    """Turn multiple observations into a grounded user-facing report."""
    observations = "\n\n".join(
        f"工具：{step['tool']}\n结果：{step['result']}" for step in steps
    )
    messages = [
        {
            "role": "system",
            "content": (
                "你是 PC 诊断报告生成器。只能根据给出的工具结果回答，"
                "不得编造温度、进程、磁盘或其他数据。用简洁中文总结："
                "当前状态、可能问题、建议；如果证据不足要明确说明。"
                "这一步只生成文字，禁止输出工具调用 JSON。"
            ),
        },
        {
            "role": "user",
            "content": f"用户原始问题：{user_input}\n\n工具结果：\n{observations}",
        },
    ]
    try:
        summary = str(llm_func(messages)).strip()
    except Exception as exc:
        return f"{observations}\n\n生成综合报告失败：{type(exc).__name__}: {exc}"
    return summary or observations


def _execute_confirmed_plan(confirmation_token: str, llm_func) -> AgentResult:
    try:
        claimed = claim_pending_plan(confirmation_token)
    except PendingPlanError as exc:
        return AgentResult(response=f"未执行任何操作：{exc}", status=AgentStatus.REFUSED)

    try:
        plan = _validate_stored_calls(claimed.calls)
        response, success, steps = _execute_plan(plan, confirmed=True)
        if success and len(steps) > 1:
            response = _summarize_tool_results(claimed.user_input, steps, llm_func)
    except Exception as exc:
        response = f"已保存计划执行失败：{type(exc).__name__}: {exc}"
        success = False

    finish_claimed_plan(claimed.token_hash, success=success, result=response)
    return AgentResult(
        response=response,
        status=AgentStatus.COMPLETED if success else AgentStatus.FAILED,
        plan_hash=claimed.plan_hash,
    )


def _run_agent_core(
    user_input: str,
    llm_func=chat_completion,
    *,
    confirmation_token: str | None = None,
) -> AgentResult:
    """Create a plan or atomically execute a previously confirmed plan."""
    if confirmation_token:
        # Confirmation never asks the model to plan again; it may summarize observations afterward.
        return _execute_confirmed_plan(confirmation_token, llm_func)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_input},
    ]
    try:
        response = llm_func(messages)
        plan = _validated_plan(response)
    except ToolProtocolError as exc:
        return AgentResult(
            response=f"未执行任何操作：{exc}",
            status=AgentStatus.REFUSED,
        )
    except Exception as exc:
        return AgentResult(
            response=f"模型调用失败：{type(exc).__name__}: {exc}",
            status=AgentStatus.FAILED,
        )

    if not plan:
        return AgentResult(
            response=_friendly_no_tool_response(),
            status=AgentStatus.COMPLETED,
        )

    dangerous_calls = [item for item in plan if item[0].requires_confirmation]
    if dangerous_calls:
        previews: list[str] = []
        prepared_plan: list[ValidatedCall] = []
        for definition, params, prepared in plan:
            if not definition.requires_confirmation:
                prepared_plan.append((definition, params, prepared))
                continue
            if definition.prepare_handler is not None:
                preview, prepared = definition.prepare_handler(**params)
            elif definition.preview_handler is None:
                preview = f"工具 {definition.name} 需要用户确认。"
            else:
                preview = definition.preview_handler(**params)
            if _result_failed(preview):
                return AgentResult(response=preview, status=AgentStatus.REFUSED)
            previews.append(preview)
            prepared_plan.append((definition, params, prepared))

        plan = prepared_plan

        preview_text = "\n".join(previews)
        stored = create_pending_plan(
            user_input,
            _serialized_plan(plan),
            preview_text,
        )
        for definition, params, _prepared in plan:
            if not definition.requires_confirmation:
                continue
            log_audit(
                definition.name,
                {**params, "plan_hash": stored.plan_hash},
                preview_text,
                user_confirmed=False,
            )
        return AgentResult(
            response=(
                f"{preview_text}\n操作尚未执行。计划编号：{stored.plan_hash[:12]}。"
                "请在令牌过期前点击“确认执行预览计划”。"
            ),
            status=AgentStatus.CONFIRMATION_REQUIRED,
            confirmation_token=stored.token,
            plan_hash=stored.plan_hash,
            expires_at=stored.expires_at,
        )

    response, success, steps = _execute_plan(plan, confirmed=False)
    if success and len(steps) > 1:
        response = _summarize_tool_results(user_input, steps, llm_func)
    return AgentResult(
        response=response,
        status=AgentStatus.COMPLETED if success else AgentStatus.FAILED,
    )


def run_agent(
    user_input: str,
    llm_func=chat_completion,
    *,
    confirmation_token: str | None = None,
) -> AgentResult:
    """Run the Agent and persist an end-to-end trace for later evaluation."""
    trace_id = secrets.token_urlsafe(12)
    started = time.perf_counter()
    result = _run_agent_core(
        user_input,
        llm_func,
        confirmation_token=confirmation_token,
    )
    duration_ms = (time.perf_counter() - started) * 1000
    try:
        record_trace(
            trace_id=trace_id,
            user_input=user_input,
            status=result.status.value,
            response=result.response,
            plan_hash=result.plan_hash,
            duration_ms=duration_ms,
            is_confirmation=bool(confirmation_token),
        )
    except Exception:
        # Trace persistence must not turn a completed user operation into failure.
        pass
    return result.model_copy(update={"trace_id": trace_id})


def simple_agent(
    user_input: str,
    llm_func=chat_completion,
    *,
    confirmation_token: str | None = None,
    confirmed: bool = False,
) -> str:
    """Compatibility wrapper returning the original string response type."""
    if confirmed and not confirmation_token:
        return "未执行任何操作：布尔确认已停用，请使用操作预览返回的一次性确认令牌。"
    return run_agent(
        user_input,
        llm_func,
        confirmation_token=confirmation_token,
    ).response
