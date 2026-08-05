"""Run the local model against the SafeDesk routing benchmark without executing tools."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
import platform
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import psutil

from agent.llm_client import chat_completion
from agent.simple_agent import SYSTEM_PROMPT
from agent.tool_protocol import ToolProtocolError, parse_tool_calls
from agent.tool_registry import TOOL_REGISTRY
from evals.dataset import build_cases


BENCHMARK_VERSION = "safedesk-routing-v1"


def _params_match(expected: dict, actual: dict) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())


def evaluate_case(case: dict, llm_func=chat_completion) -> dict:
    started = time.perf_counter()
    raw = ""
    error = None
    actual_tools: list[str] = []
    actual_params: list[dict] = []
    try:
        raw = llm_func(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": case["input"]},
            ]
        )
        calls = parse_tool_calls(raw)
        actual_tools = [call.tool for call in calls]
        actual_params = [call.params for call in calls]
        for call in calls:
            if call.tool != "none":
                definition = TOOL_REGISTRY.get(call.tool)
                if definition is None:
                    raise ToolProtocolError(f"未知工具: {call.tool}")
                definition.validate_params(call.params)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    expected_tools = case["expected_tools"]
    tool_match = error is None and actual_tools == expected_tools
    parameter_match = tool_match and all(
        _params_match(expected, actual)
        for expected, actual in zip(case["expected_params"], actual_params)
    )
    return {
        **case,
        "raw": raw,
        "actual_tools": actual_tools,
        "actual_params": actual_params,
        "tool_match": tool_match,
        "parameter_match": parameter_match,
        "error": error,
        "latency_seconds": round(time.perf_counter() - started, 4),
    }


def build_summary(results: list[dict]) -> dict:
    latencies = [result["latency_seconds"] for result in results]
    by_category = {}
    for category in sorted({result["category"] for result in results}):
        selected = [result for result in results if result["category"] == category]
        by_category[category] = {
            "count": len(selected),
            "tool_accuracy": sum(item["tool_match"] for item in selected) / len(selected),
            "parameter_accuracy": sum(item["parameter_match"] for item in selected) / len(selected),
        }
    sorted_latency = sorted(latencies)
    # Nearest-rank percentile: a two-sample p95 must be the slower sample.
    p95_index = max(0, math.ceil(len(sorted_latency) * 0.95) - 1)
    safety_results = [item for item in results if item["category"] == "safety"]
    return {
        "count": len(results),
        "tool_accuracy": sum(item["tool_match"] for item in results) / len(results),
        "parameter_accuracy": sum(item["parameter_match"] for item in results) / len(results),
        "parse_error_rate": sum(item["error"] is not None for item in results) / len(results),
        "mean_latency_seconds": statistics.mean(latencies),
        "p50_latency_seconds": statistics.median(latencies),
        "p95_latency_seconds": sorted_latency[p95_index],
        "safety_interception_rate": (
            sum(item["tool_match"] for item in safety_results) / len(safety_results)
            if safety_results
            else None
        ),
        "failures_by_category": dict(
            Counter(item["category"] for item in results if not item["parameter_match"])
        ),
        "by_category": by_category,
    }


def collect_environment_metadata() -> dict:
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "logical_cpu_count": psutil.cpu_count(logical=True),
        "physical_cpu_count": psutil.cpu_count(logical=False),
        "memory_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        "model_path": os.environ.get(
            "PC_AGENT_MODEL_PATH", "./models/Qwen3.5-4B-Q4_K_M.gguf"
        ),
        "n_ctx": int(os.environ.get("PC_AGENT_N_CTX", "4096")),
        "n_threads": int(os.environ.get("PC_AGENT_N_THREADS", "8")),
        "n_gpu_layers": int(os.environ.get("PC_AGENT_N_GPU_LAYERS", "0")),
    }


def write_report(
    output_dir: Path,
    results: list[dict],
    summary: dict,
    metadata: dict | None = None,
) -> None:
    metadata = metadata or collect_environment_metadata()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "latest.json").write_text(
        json.dumps(
            {"metadata": metadata, "summary": summary, "results": results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    lines = [
        "# SafeDesk Tool Routing Eval",
        "",
        f"- Cases: {summary['count']}",
        f"- Tool accuracy: {summary['tool_accuracy']:.2%}",
        f"- Parameter accuracy: {summary['parameter_accuracy']:.2%}",
        f"- Parse error rate: {summary['parse_error_rate']:.2%}",
        f"- Mean latency: {summary['mean_latency_seconds']:.2f}s",
        f"- p50 latency: {summary['p50_latency_seconds']:.2f}s",
        f"- p95 latency: {summary['p95_latency_seconds']:.2f}s",
        f"- Safety interception rate: "
        f"{_format_optional_rate(summary['safety_interception_rate'])}",
        f"- Model: `{metadata['model_path']}`",
        f"- Runtime: n_ctx={metadata['n_ctx']}, threads={metadata['n_threads']}, "
        f"GPU layers={metadata['n_gpu_layers']}",
        "",
        "## Failures",
        "",
    ]
    for result in results:
        if not result["parameter_match"]:
            lines.extend(
                [
                    f"### {result['id']}",
                    "",
                    f"- Input: {result['input']}",
                    f"- Expected: `{result['expected_tools']}`",
                    f"- Actual: `{result['actual_tools']}`",
                    f"- Error: `{result['error']}`",
                    "",
                ]
            )
    (output_dir / "latest.md").write_text("\n".join(lines), encoding="utf-8")


def _format_optional_rate(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2%}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("evals/reports"))
    parser.add_argument("--export-dataset", type=Path, default=None)
    args = parser.parse_args()
    cases = build_cases()
    if args.export_dataset:
        args.export_dataset.write_text(
            "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n",
            encoding="utf-8",
        )
    selected = cases[: args.limit] if args.limit else cases
    results = []
    for index, case in enumerate(selected, 1):
        result = evaluate_case(case)
        results.append(result)
        print(
            f"[{index}/{len(selected)}] {case['id']} "
            f"tool={result['tool_match']} params={result['parameter_match']} "
            f"latency={result['latency_seconds']:.2f}s",
            flush=True,
        )
    summary = build_summary(results)
    write_report(args.output_dir, results, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
