from evals.dataset import build_cases
from evals.run_tool_routing_eval import build_summary


def test_routing_dataset_has_expected_size_and_unique_ids():
    cases = build_cases()
    assert len(cases) == 80
    assert len({case["id"] for case in cases}) == 80
    assert {case["category"] for case in cases} >= {
        "single_tool",
        "multi_tool",
        "rag",
        "high_risk",
        "safety",
    }


def test_eval_p95_uses_nearest_rank():
    results = [
        {
            "category": "single_tool",
            "tool_match": True,
            "parameter_match": True,
            "error": None,
            "latency_seconds": 1.0,
        },
        {
            "category": "single_tool",
            "tool_match": True,
            "parameter_match": True,
            "error": None,
            "latency_seconds": 2.0,
        },
    ]

    assert build_summary(results)["p95_latency_seconds"] == 2.0
