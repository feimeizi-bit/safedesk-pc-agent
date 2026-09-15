"""Run a deterministic, offline RAG retrieval and threshold evaluation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from difflib import SequenceMatcher
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.rag_query as rag_query


@dataclass
class Document:
    page_content: str
    metadata: dict[str, str]


class OfflineKnowledgeStore:
    """Small deterministic store for CI and threshold calibration.

    Production uses Chroma embeddings. This evaluator intentionally avoids
    downloading a model so changes to retrieval policy can be checked on any
    machine with the repository's knowledge files.
    """

    def __init__(self, documents: list[Document]):
        self.documents = documents

    def similarity_search_with_relevance_scores(self, question: str, k: int):
        ranked = []
        for document in self.documents:
            stored_questions = re.findall(
                r"问：\s*(.*?)\s*\n答：", document.page_content, flags=re.DOTALL
            )
            score = max(
                (_similarity(question, stored_question) for stored_question in stored_questions),
                default=0.0,
            )
            ranked.append((document, score))
        return sorted(ranked, key=lambda item: item[1], reverse=True)[:k]


def _similarity(left: str, right: str) -> float:
    compact = lambda value: re.sub(r"\s+", "", value).rstrip("？?").lower()
    return SequenceMatcher(None, compact(left), compact(right)).ratio()


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_store(knowledge_dir: Path) -> OfflineKnowledgeStore:
    documents = [
        Document(path.read_text(encoding="utf-8"), {"source": path.name})
        for path in sorted(knowledge_dir.glob("*.txt"))
    ]
    return OfflineKnowledgeStore(documents)


def evaluate(cases: list[dict], store: OfflineKnowledgeStore, threshold: float, top_k: int):
    rag_query._vectorstore = store
    retrieval_hits = 0
    answered_positive = 0
    keyword_hits = 0
    false_answers = 0
    for case in cases:
        candidates = rag_query.retrieve_knowledge(case["question"], top_k=top_k)
        if case["expected_source"] and any(
            candidate.source == case["expected_source"] for candidate in candidates
        ):
            retrieval_hits += 1

        result = rag_query.query_knowledge_base_detailed(
            case["question"], top_k=top_k, min_confidence=threshold
        )
        answered = result["status"] == "answered"
        if case["should_answer"]:
            if answered:
                answered_positive += 1
                if all(keyword in result["answer"] for keyword in case["answer_keywords"]):
                    keyword_hits += 1
        elif answered:
            false_answers += 1

    positives = sum(case["should_answer"] for case in cases)
    negatives = len(cases) - positives
    true_negatives = negatives - false_answers
    precision = keyword_hits / (keyword_hits + false_answers) if keyword_hits + false_answers else 0.0
    recall = keyword_hits / positives if positives else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "retrieval_recall": retrieval_hits / positives if positives else 0.0,
        "answer_hit_rate": keyword_hits / positives if positives else 0.0,
        "abstention_rate_on_negatives": true_negatives / negatives if negatives else 0.0,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_answers": false_answers,
        "answered_positive": answered_positive,
        "keyword_hits": keyword_hits,
    }


def render_report(cases, threshold_rows, top_k):
    best = max(threshold_rows, key=lambda row: (row["f1"], row["abstention_rate_on_negatives"]))
    lines = [
        "# SafeDesk RAG Offline Evaluation",
        "",
        f"- Cases: {len(cases)} ({sum(case['should_answer'] for case in cases)} answerable, {sum(not case['should_answer'] for case in cases)} negative)",
        f"- Retrieval top_k: {top_k}",
        f"- Recommended threshold: `{best['threshold']:.2f}` (max F1, then abstention)",
        "",
        "| threshold | retrieval recall | answer hit rate | precision | recall | F1 | negative abstention |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in threshold_rows:
        lines.append(
            f"| {row['threshold']:.2f} | {row['retrieval_recall']:.1%} | {row['answer_hit_rate']:.1%} | {row['precision']:.1%} | {row['recall']:.1%} | {row['f1']:.1%} | {row['abstention_rate_on_negatives']:.1%} |"
        )
    lines.extend(["", "This report uses an offline deterministic store; it does not download embedding models or call external services."])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "evals" / "rag_dataset.jsonl")
    parser.add_argument("--knowledge-dir", type=Path, default=ROOT / "knowledge_base")
    parser.add_argument("--top-k", type=int, default=2)
    parser.add_argument("--output", type=Path, default=ROOT / "evals" / "reports" / "rag_latest.md")
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    store = build_store(args.knowledge_dir)
    thresholds = [0.25, 0.35, 0.45, 0.55, 0.65]
    rows = [
        {"threshold": threshold, **evaluate(cases, store, threshold, args.top_k)}
        for threshold in thresholds
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_report(cases, rows, args.top_k), encoding="utf-8")
    best = max(rows, key=lambda row: (row["f1"], row["abstention_rate_on_negatives"]))
    print(json.dumps({"output": str(args.output), "recommended_threshold": best["threshold"], "rows": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
