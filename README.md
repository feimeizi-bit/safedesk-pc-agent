# SafeDesk PC Agent

SafeDesk 聚焦的不是“模型能否调用工具”，而是“工具调用产生外部副作用后是否仍然
可控”：它是一个面向本地小模型 Agent 的安全执行 Runtime，把不确定的模型输出
约束成可验证、可确认、可追踪、可恢复的工具调用。当前使用
`llama.cpp + Qwen GGUF + FastAPI + Gradio + SQLite`，
支持系统卡顿诊断、白名单应用启动、文件分类预览/确认/撤销、本地知识检索、
执行追踪和离线评测。

## 项目定位与差异化

与只展示“自然语言 → 工具调用”的 Agent Demo 相比，SafeDesk 把重点放在模型
产生外部副作用之后，系统是否仍然可控、可解释、可恢复：

- **确定性边界**：Pydantic 协议、声明式工具注册、风险分级和 Fail Closed，
  将模型能力限制在显式允许的工具与参数集合内。
- **副作用生命周期**：高风险操作采用预览、计划哈希、一次性令牌、确认执行；
  通过目录快照、冲突拒绝、反向清单补偿和撤销处理真实文件变更，而不是只返回文本。
- **资源与任务治理**：单进程只接收一个活动 Agent 任务，忙碌时立即返回 503；
  状态查询、协作式取消、软超时和线程池隔离让有限的本地模型资源保持有界。
- **可验证工程闭环**：工具审计、端到端 Trace、固定路由集和自动化测试共同验证
  协议正确性、安全拦截和延迟，而不是只展示一次成功演示。

RAG 子模块位于安全执行层之前，负责把本地知识转成带来源和置信度的候选答案。
它支持 Chroma 相关性分数，也能在无相关性分数的向量库实现上回退；当匹配低于
阈值时主动拒答，而不是把最相似的错误片段继续交给模型。详细结果可通过
`tools.rag_query.query_knowledge_base_detailed()` 获取，Agent 工具接口仍返回
兼容旧调用链的纯文本答案。

## 当前安全边界

- 模型输出必须通过 Pydantic 工具协议校验。
- 未注册工具和额外参数会被拒绝。
- 应用启动仅允许固定白名单，且不经过 shell。
- 文件整理默认只生成预览，并保存带 SHA-256 哈希的操作计划。
- 确认使用十分钟有效的一次性令牌，确认阶段不会让模型重新规划。
- 预览后的目录内容发生变化时拒绝执行，避免检查/执行时间差问题。
- 文件移动中途失败时按反向清单自动补偿。
- 已完成的文件整理事务可以通过同样的预览/确认流程主动撤销。
- 文件冲突时拒绝覆盖。
- 审计日志记录真实确认状态，默认保存到 `.pc_agent/audit.db`。
- 单进程内只接受一个活动 Agent 任务；额外请求立即返回 busy，不建立隐藏队列。
- 任务支持状态查询、协作式取消和软超时；工作线程真正退出前不会释放执行槽位。

当前版本已经实现一次性计划令牌、持久化事务清单和主动撤销；进程崩溃后的
启动恢复仍在开发中。
详细限制和路线图见
[`docs/TECHNICAL_LOG.md`](docs/TECHNICAL_LOG.md)。

## 运行

```powershell
.\.venv\Scripts\python.exe -m uvicorn API.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe frontend\ui.py
```

浏览器访问 `http://127.0.0.1:7861`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试只在临时目录中执行文件操作，不会整理真实桌面。

## 本地模型评测

评测只检查模型生成的工具名与参数，不执行真实工具。数据集固定为 80 条，
覆盖单工具、多工具诊断、RAG、文件操作、撤销和危险请求拒绝。报告记录模型参数、
本机资源、工具/参数准确率、安全拦截率及 p50/p95 延迟，便于做模型和配置对照。

```powershell
# 先跑 5 条确认环境可用
$env:PC_AGENT_OFFLINE='1'
.\.venv\Scripts\python.exe -m evals.run_tool_routing_eval --limit 5

# 完整评测
.\.venv\Scripts\python.exe -m evals.run_tool_routing_eval
```

报告生成在 `evals/reports/latest.json` 和 `latest.md`。该目录默认不提交 Git，
避免把一次局部实验误当成稳定基线。

RAG 检索策略也可以在不下载 embedding 模型的情况下离线评测：

```powershell
.\.venv\Scripts\python.exe -m evals.run_rag_eval
```

评测使用 `evals/rag_dataset.jsonl` 的 7 条可回答问题和 4 条负例，扫描置信度
阈值并生成 `evals/reports/rag_latest.md`。

当前正式基线（Qwen3.5-4B-Q4、CPU-only、80 条）为：工具/参数准确率 93.75%，
协议解析失败率 0%，危险请求拦截率 100%（7/7），p50 13.39 秒、p95 19.56 秒。
实验配置与失败审计见 [`docs/evaluation/BASELINE_2026-07-23.md`](docs/evaluation/BASELINE_2026-07-23.md)。

## 配置

- `PC_AGENT_AUDIT_DB`：覆盖审计数据库路径。
- `PC_AGENT_OFFLINE=1`：要求 embedding 仅使用本地缓存。
- `PC_AGENT_MODEL_PATH`：覆盖本地 GGUF 模型路径。
- `PC_AGENT_N_CTX`：上下文长度，默认 4096。
- `PC_AGENT_N_THREADS`、`PC_AGENT_N_GPU_LAYERS`：控制 CPU 线程和 GPU 卸载层数。
- `PC_AGENT_MAX_RUNTIME_SECONDS`：单任务软超时，默认 120 秒，范围 1～3600 秒。

## API

- `POST /chat`：兼容原有同步等待方式，但与任务接口共享单任务容量限制。
- `POST /tasks`：提交任务并立即返回 `202` 和 `task_id`；忙碌时返回 `503 agent_busy`。
- `GET /tasks/{task_id}`：查询状态、结果和 `queue_ms/model_ms/tool_ms/total_ms`。
- `POST /tasks/{task_id}/cancel`：请求协作式取消。
- `GET /audit_log`：查看工具审计记录。
- `GET /traces?limit=20`：查看最近的端到端请求状态和延迟。
- `GET /traces/{trace_id}`：根据标识精确查询一次端到端请求，不存在时返回 404。

任务状态保存在当前进程内，进程重启后不会恢复；已完成请求的 Trace 仍会持久化到
SQLite。取消和超时只能在模型调用、工具步骤等安全边界生效。同步线程或 llama.cpp
尚未真正返回时，任务保持 `cancel_requested` 或 `timeout_requested`，系统仍拒绝新任务。

## 高风险操作确认流程

1. 发送文件整理请求；
2. Runtime 保存包含具体文件移动清单的计划并返回预览；
3. 前端在会话状态中保存一次性令牌；
4. 用户点击“确认执行预览计划”；
5. 后端原子消费令牌并执行原计划，不重新请求模型；
6. 重复令牌、过期令牌或发生变化的目录均会被拒绝。

模型文件、向量数据库、日志和虚拟环境均不应提交到 Git。
