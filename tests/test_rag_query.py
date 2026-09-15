from dataclasses import dataclass

import tools.rag_query as rag_query


@dataclass
class FakeDocument:
    page_content: str
    metadata: dict


class ScoredStore:
    def __init__(self, rows):
        self.rows = rows

    def similarity_search_with_relevance_scores(self, _question, k):
        return self.rows[:k]


class PlainStore:
    def __init__(self, documents):
        self.documents = documents

    def similarity_search(self, _question, k):
        return self.documents[:k]


def test_detailed_query_returns_answer_confidence_and_sources(monkeypatch):
    document = FakeDocument(
        "问：如何整理桌面文件？\n答：可以按扩展名分类整理。",
        {"source": "办公效率技巧.txt"},
    )
    monkeypatch.setattr(
        rag_query,
        "_vectorstore",
        ScoredStore([(document, 0.31)]),
    )

    result = rag_query.query_knowledge_base_detailed("如何整理桌面文件？")

    assert result["status"] == "answered"
    assert result["answer"] == "可以按扩展名分类整理。"
    assert result["confidence"] > 0.45
    assert result["sources"] == ["办公效率技巧.txt"]
    assert result["matched_question"] == "如何整理桌面文件？"
    assert "来源：办公效率技巧.txt" in rag_query.query_knowledge_base(
        "如何整理桌面文件？"
    )


def test_low_confidence_query_abstains_instead_of_returning_wrong_answer(monkeypatch):
    document = FakeDocument(
        "这是一段没有问答格式的知识库内容。",
        {"source": "电脑维护与故障排除.txt"},
    )
    monkeypatch.setattr(
        rag_query,
        "_vectorstore",
        ScoredStore([(document, 0.2)]),
    )

    result = rag_query.query_knowledge_base_detailed("如何配置邮件服务器？")

    assert result["status"] == "low_confidence"
    assert result["confidence"] == 0.2
    assert "没有足够匹配" in result["answer"]


def test_retrieval_supports_vectorstores_without_relevance_scores(monkeypatch):
    document = FakeDocument(
        "问：如何查看电脑温度？\n答：可以使用 HWMonitor。",
        {"file_path": "电脑维护与故障排除.txt"},
    )
    monkeypatch.setattr(rag_query, "_vectorstore", PlainStore([document]))

    result = rag_query.query_knowledge_base_detailed("如何查看电脑温度？")

    assert result["status"] == "answered"
    assert result["sources"] == ["电脑维护与故障排除.txt"]
