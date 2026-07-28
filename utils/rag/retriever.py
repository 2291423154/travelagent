"""
RAG 检索器 - 给 ReActAgent 提供知识库检索能力
使用 BGE 中文 Embedding + Chroma 向量数据库
"""
import os
import chromadb
from chromadb.config import Settings
from openai import OpenAI

# 自动加载项目根目录的 .env 文件
_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
if os.path.exists(_ENV_PATH):
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_PATH)
    except ImportError:
        pass  # python-dotenv 未安装则跳过，依赖系统环境变量


class RAGRetriever:
    """知识库检索器：存储旅游攻略等非结构化知识，按需检索注入 Agent Context"""

    def __init__(self, persist_dir: str = None):
        if persist_dir is None:
            persist_dir = os.path.join(os.path.dirname(__file__), "rag_db")

        self.persist_dir = persist_dir
        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name="travel_knowledge",
            metadata={"hnsw:space": "cosine"},
        )

        # Embedding 模型配置（独立于 Chat 模型，从 .env 读取）
        api_key = os.getenv("EMBED_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("EMBED_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        if not api_key:
            raise RuntimeError("缺少 API Key！请在 .env 中设置 EMBED_API_KEY")
        self.embed_client = OpenAI(api_key=api_key, base_url=base_url)
        self.embed_model = os.getenv("EMBED_MODEL", "text-embedding-v1")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量文本 -> 向量"""
        resp = self.embed_client.embeddings.create(
            model=self.embed_model,
            input=texts,
        )
        return [d.embedding for d in resp.data]

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """语义检索 Top-K Chunks，返回原始文本 + 元数据"""
        if self.collection.count() == 0:
            return []

        query_vec = self.embed([query])[0]
        results = self.collection.query(
            query_embeddings=[query_vec],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        chunks = []
        for i in range(len(results["documents"][0])):
            chunks.append({
                "content": results["documents"][0][i],
                "source": results["metadatas"][0][i].get("source", "未知"),
                "distance": results["distances"][0][i],
            })
        return chunks

    def format_for_llm(self, query: str, top_k: int = 3) -> str:
        """检索结果格式化为 LLM 可读的 Prompt 片段"""
        chunks = self.search(query, top_k)
        if not chunks:
            return "（知识库中未找到相关内容）"

        parts = ["【知识库检索结果】"]
        for i, c in enumerate(chunks, 1):
            parts.append(f"[{i}] (来源: {c['source']}, 相关度: {1-c['distance']:.2f})")
            parts.append(c["content"])
            parts.append("")
        return "\n".join(parts)

    @property
    def doc_count(self) -> int:
        return self.collection.count()


# 全局单例
_retriever: RAGRetriever = None


def get_retriever() -> RAGRetriever:
    global _retriever
    if _retriever is None:
        _retriever = RAGRetriever()
    return _retriever
