"""SafeDesk's local knowledge retrieval boundary.

The Agent still receives a plain string from ``query_knowledge_base`` for
backwards compatibility.  The detailed API is intentionally structured so an
API layer, Trace record, or evaluation script can inspect confidence and
citations without parsing presentation text.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import os
import re
from typing import Any


CHROMA_DIR = os.environ.get("PC_AGENT_CHROMA_DIR", "./chroma_db")
COLLECTION_NAME = os.environ.get("PC_AGENT_CHROMA_COLLECTION", "safedesk_knowledge")
EMBEDDING_MODEL = os.environ.get(
    "PC_AGENT_EMBEDDING_MODEL", "shibing624/text2vec-base-chinese"
)
DEFAULT_TOP_K = 3
DEFAULT_MIN_CONFIDENCE = 0.35

_embeddings = None
_vectorstore = None


@dataclass(frozen=True)
class RetrievedKnowledge:
    """One normalized candidate exposed by the retrieval layer."""

    answer: str
    source: str
    score: float
    matched_question: str | None = None


def get_vectorstore():
    """Load the persistent Chroma store lazily so non-RAG tools stay usable."""
    global _embeddings, _vectorstore
    if _vectorstore is None:
        from langchain_chroma import Chroma
        from langchain_community.embeddings import HuggingFaceEmbeddings

        offline = os.environ.get("PC_AGENT_OFFLINE", "0") == "1"
        model_kwargs = {"local_files_only": True} if offline else {}
        _embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs=model_kwargs,
        )
        _vectorstore = Chroma(
            collection_name=COLLECTION_NAME,
            persist_directory=CHROMA_DIR,
            embedding_function=_embeddings,
        )
    return _vectorstore


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).rstrip("？?").lower()


def _question_similarity(left: str, right: str) -> float:
    normalized_left = _normalize(left)
    normalized_right = _normalize(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def _source(metadata: dict[str, Any]) -> str:
    return str(
        metadata.get("source")
        or metadata.get("file_path")
        or metadata.get("doc_id")
        or "本地知识库"
    )


def _extract_qa_pairs(content: str) -> list[tuple[str, str]]:
    pairs = re.findall(
        r"问：\s*(.*?)\s*\n答：\s*(.*?)(?=\n\s*问：|\Z)",
        content,
        flags=re.DOTALL,
    )
    return [(question.strip(), answer.strip()) for question, answer in pairs]


def _search_with_scores(store, question: str, top_k: int):
    """Use relevance scores where supported, and degrade to plain search."""
    # Chroma's relevance helper can emit warnings when a distance falls
    # outside its assumed [0, 1] range. Its raw distance API is more stable;
    # convert distance to a bounded, monotonic score for our policy layer.
    raw_score_search = getattr(store, "similarity_search_with_score", None)
    if callable(raw_score_search):
        try:
            rows = raw_score_search(question, k=top_k)
            return [
                (document, 1.0 / (1.0 + max(0.0, float(distance))))
                for document, distance in rows
            ]
        except (NotImplementedError, TypeError, ValueError):
            pass
    try:
        scored = store.similarity_search_with_relevance_scores(question, k=top_k)
    except (AttributeError, NotImplementedError, TypeError, ValueError):
        return [(document, None) for document in store.similarity_search(question, k=top_k)]
    return list(scored)


def retrieve_knowledge(
    question: str,
    *,
    top_k: int = DEFAULT_TOP_K,
) -> list[RetrievedKnowledge]:
    """Retrieve and normalize answer candidates without deciding to abstain."""
    if not question.strip() or top_k <= 0:
        return []

    candidates: list[RetrievedKnowledge] = []
    seen: set[tuple[str, str]] = set()
    for document, relevance in _search_with_scores(get_vectorstore(), question, top_k):
        content = str(getattr(document, "page_content", "")).strip()
        metadata = getattr(document, "metadata", {}) or {}
        source = _source(metadata)
        base_score = float(relevance) if relevance is not None else 0.0
        pairs = _extract_qa_pairs(content)
        if pairs:
            for stored_question, answer in pairs:
                key = (stored_question, answer)
                if key in seen:
                    continue
                seen.add(key)
                similarity = _question_similarity(question, stored_question)
                # A document can contain several Q&A pairs. Do not copy its
                # document-level score to every pair; combine it with the
                # pair-level lexical score so the final answer is grounded in
                # the matched question as well as the retrieved document.
                score = (
                    similarity
                    if relevance is None
                    else 0.65 * similarity + 0.35 * max(0.0, min(1.0, base_score))
                )
                candidates.append(
                    RetrievedKnowledge(
                        answer=answer,
                        source=source,
                        score=score,
                        matched_question=stored_question,
                    )
                )
            continue

        if not content:
            continue
        candidates.append(
            RetrievedKnowledge(
                answer=content,
                source=source,
                score=base_score,
            )
        )

    return sorted(candidates, key=lambda item: item.score, reverse=True)


def query_knowledge_base_detailed(
    question: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    min_confidence: float | None = None,
) -> dict[str, Any]:
    """Return an answer with confidence, status, and deduplicated citations."""
    threshold = (
        float(os.environ.get("PC_AGENT_RAG_MIN_CONFIDENCE", DEFAULT_MIN_CONFIDENCE))
        if min_confidence is None
        else float(min_confidence)
    )
    candidates = retrieve_knowledge(question, top_k=top_k)
    if not candidates:
        return {
            "answer": "知识库中没有找到相关信息。",
            "confidence": 0.0,
            "status": "no_match",
            "sources": [],
            "retrieved_count": 0,
        }

    best = candidates[0]
    sources = list(dict.fromkeys(item.source for item in candidates))
    if best.score < threshold:
        return {
            "answer": "知识库中没有足够匹配的信息，建议补充问题或查看原始文档。",
            "confidence": round(best.score, 4),
            "status": "low_confidence",
            "sources": sources,
            "retrieved_count": len(candidates),
            "matched_question": best.matched_question,
        }

    return {
        "answer": best.answer,
        "confidence": round(best.score, 4),
        "status": "answered",
        "sources": sources,
        "retrieved_count": len(candidates),
        "matched_question": best.matched_question,
    }


def query_knowledge_base(question: str) -> str:
    """Agent-compatible text answer with a compact source suffix."""
    result = query_knowledge_base_detailed(question)
    if not result["sources"]:
        return result["answer"]
    source_text = "、".join(result["sources"])
    return f"{result['answer']}\n来源：{source_text}"
