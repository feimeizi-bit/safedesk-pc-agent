# tools/rag_query.py
import os
import re
from difflib import SequenceMatcher
from langchain_chroma import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings

CHROMA_DIR = "./chroma_db"  # 向量数据库目录，应与 build_knowledge_base.py 中的一致
EMBEDDING_MODEL = "shibing624/text2vec-base-chinese"

_embeddings = None
_vectorstore = None

def get_vectorstore():
    global _embeddings, _vectorstore
    if _vectorstore is None:
        offline = os.environ.get("PC_AGENT_OFFLINE", "0") == "1"
        model_kwargs = {"local_files_only": True} if offline else {}
        _embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs=model_kwargs,
        )
        _vectorstore = Chroma(persist_directory=CHROMA_DIR, embedding_function=_embeddings)
    return _vectorstore

def query_knowledge_base(question: str) -> str:
    vectorstore = get_vectorstore()
    docs = vectorstore.similarity_search(question, k=3)
    if not docs:
        return "知识库中没有找到相关信息。"

    candidates: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for doc in docs:
        pairs = re.findall(
            r"问：\s*(.*?)\s*\n答：\s*(.*?)(?=\n\s*问：|\Z)",
            doc.page_content,
            flags=re.DOTALL,
        )
        for stored_question, answer in pairs:
            pair = (stored_question.strip(), answer.strip())
            if pair not in seen:
                seen.add(pair)
                candidates.append(pair)

    if candidates:
        normalized = re.sub(r"\s+", "", question).rstrip("？?")
        best_question, best_answer = max(
            candidates,
            key=lambda pair: SequenceMatcher(
                None,
                normalized,
                re.sub(r"\s+", "", pair[0]).rstrip("？?"),
            ).ratio(),
        )
        score = SequenceMatcher(
            None,
            normalized,
            re.sub(r"\s+", "", best_question).rstrip("？?"),
        ).ratio()
        if score >= 0.45:
            return f"{best_answer}\n来源：{docs[0].metadata.get('source', '本地知识库')}"

    content = docs[0].page_content.strip()
    if len(content) > 300:
        content = content[:300] + "..."
    return f"{content}\n来源：{docs[0].metadata.get('source', '本地知识库')}"
