# SafeDesk PC Agent

面向消费级 Windows PC 与本地小模型的安全 Agent Runtime。当前使用
`llama.cpp + Qwen GGUF + FastAPI + Gradio + SQLite`，支持系统卡顿诊断、
白名单应用启动、文件分类预览/确认/撤销、本地知识检索、执行追踪和离线评测。

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

当前正式基线（Qwen3.5-4B-Q4、CPU-only、80 条）为：工具/参数准确率 93.75%，
协议解析失败率 0%，危险请求拦截率 100%（7/7），p50 13.39 秒、p95 19.56 秒。
实验配置与失败审计见 [`docs/evaluation/BASELINE_2026-07-23.md`](docs/evaluation/BASELINE_2026-07-23.md)。

## 配置

- `PC_AGENT_AUDIT_DB`：覆盖审计数据库路径。
- `PC_AGENT_OFFLINE=1`：要求 embedding 仅使用本地缓存。
- `PC_AGENT_MODEL_PATH`：覆盖本地 GGUF 模型路径。
- `PC_AGENT_N_CTX`：上下文长度，默认 4096。
- `PC_AGENT_N_THREADS`、`PC_AGENT_N_GPU_LAYERS`：控制 CPU 线程和 GPU 卸载层数。

## API

- `POST /chat`：规划、预览或确认执行，响应包含 `trace_id`。
- `GET /audit_log`：查看工具审计记录。
- `GET /traces?limit=20`：查看最近的端到端请求状态和延迟。

## 高风险操作确认流程

1. 发送文件整理请求；
2. Runtime 保存包含具体文件移动清单的计划并返回预览；
3. 前端在会话状态中保存一次性令牌；
4. 用户点击“确认执行预览计划”；
5. 后端原子消费令牌并执行原计划，不重新请求模型；
6. 重复令牌、过期令牌或发生变化的目录均会被拒绝。

模型文件、向量数据库、日志和虚拟环境均不应提交到 Git。
