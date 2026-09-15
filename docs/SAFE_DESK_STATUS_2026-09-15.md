# SafeDesk Current Status

As of 2026-09-15, SafeDesk is a local, safety-first Agent runtime for controlled PC actions. The current repository state is the v0.4 single-process bounded-capacity milestone.

## Implemented

- Local Tool-Calling Agent using llama.cpp and a Qwen3.5-4B-Q4 model, exposed through FastAPI and Gradio.
- Typed Pydantic tool protocol, declarative registry, risk levels, allowlists, and fail-closed handling for unknown tools, extra parameters, and dangerous requests.
- Preview → plan hash → one-time confirmation token → execution flow for high-risk actions; confirmation does not call the model again.
- SQLite-backed atomic consumption of ten-minute confirmation tokens, directory snapshot checks before file operations, reverse-manifest compensation, preview-based undo, and conflict refusal.
- `AgentTaskManager` with one active Agent task per process. Busy submissions return `503 agent_busy` instead of creating a hidden queue.
- `POST /tasks`, `GET /tasks/{task_id}`, and cooperative cancellation/soft-timeout controls. A cancelled or timed-out worker keeps the execution slot until it actually returns.
- Synchronous SQLite reads for audit logs and traces moved to the FastAPI worker thread. Traces record `queue_ms`, `model_ms`, `tool_ms`, and `total_ms`.
- A bounded RAG retrieval layer now returns normalized candidates with matching scores,
  matched questions, and source citations. Low-confidence retrievals abstain instead of
  passing an ungrounded or weakly matched snippet to the Agent.
- An offline labeled set of 19 cases (15 answerable, 4 negative) is used to sweep the
  confidence threshold. At thresholds 0.35–0.45 it reached 100% negative abstention and
  93.3% answer hit rate; one paraphrase miss remains a known lexical-retrieval boundary.

## Verification

- `python -m pytest -q`: 26 tests passed on 2026-09-15.
- Fixed routing set: 80 Chinese cases; strict tool/parameter accuracy 93.75% (75/80).
- Protocol parse failure rate: 0%.
- Dangerous-request interception: 100% (7/7).
- Routing latency baseline: p50 13.39 s, p95 19.56 s.

## Known boundaries

- Task state is in process memory; multi-worker coordination and restart recovery are not implemented.
- Cancellation is cooperative and cannot forcibly terminate a synchronous llama.cpp call or tool.
- SQLite connections, crash recovery, and durable task state remain follow-up hardening work.
- Real-model CPU saturation and control-plane tail latency still need measurement.
- RAG recall/precision and threshold calibration still need a dedicated labeled dataset.
