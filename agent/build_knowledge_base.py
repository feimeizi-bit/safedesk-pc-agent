import os
from langchain_community.document_loaders import TextLoader, PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_chroma import Chroma

# 配置
KNOWLEDGE_DIR = "knowledge_base"
CHROMA_DIR = "./chroma_db"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
EMBEDDING_MODEL = "shibing624/text2vec-base-chinese"  # 中文嵌入模型

def load_documents():
    """加载 knowledge_base 目录下的所有 txt 和 pdf 文件"""
    docs = []
    for file in os.listdir(KNOWLEDGE_DIR):
        file_path = os.path.join(KNOWLEDGE_DIR, file)
        if file.endswith(".txt"):
            loader = TextLoader(file_path, encoding="utf-8")
            docs.extend(loader.load())
        elif file.endswith(".pdf"):
            loader = PyPDFLoader(file_path)
            docs.extend(loader.load())
        else:
            print(f"跳过不支持的文件类型: {file}")
    return docs

def main():
    print("正在加载文档...")
    documents = load_documents()
    if not documents:
        print("错误：knowledge_base 文件夹中没有找到 txt 或 pdf 文件。")
        return
    
    print(f"已加载 {len(documents)} 个文档。")
    
    print("正在分割文本...")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=200,
        chunk_overlap=20,
        separators=["\n\n", "\n", "问：", "。", "！", "？", "；", "，", " ", ""]
    )
    chunks = splitter.split_documents(documents)
    print(f"已分割为 {len(chunks)} 个文本块。")
    
    print("正在加载嵌入模型（首次运行会下载模型，稍等）...")
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    
    print("正在构建向量数据库...")
    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=CHROMA_DIR
    )
    # vectorstore.persist()
    print(f"知识库构建完成！已保存至 {CHROMA_DIR}")

if __name__ == "__main__":
    main()