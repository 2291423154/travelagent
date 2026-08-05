"""
RAG 检索器 — Chroma 向量数据库 + OpenAI Embedding
"""
import os
import chromadb
from openai import OpenAI

_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
if os.path.exists(_ENV_PATH):
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_PATH)
    except ImportError:
        pass


class RAGRetriever:
    """Chroma 向量存储：自建 embedding（不走 Chroma 默认 onnx，避免 Windows 崩溃）"""

    def __init__(self, persist_dir: str = None):
        if persist_dir is None:
            persist_dir = os.path.join(os.path.dirname(__file__), "rag_db")

        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=chromadb.Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name="travel_knowledge",
            metadata={"hnsw:space": "cosine"},
        )

        # Embedding 模型（独立于 Chroma 自带 embedder）
        api_key = os.getenv("EMBED_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("EMBED_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        if not api_key:
            raise RuntimeError("缺少 API Key！请在 .env 中设置 OPENAI_API_KEY")
        self.embed_client = OpenAI(api_key=api_key, base_url=base_url)
        self.embed_model = os.getenv("EMBED_MODEL", "text-embedding-v1")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量文本 → 向量"""
        resp = self.embed_client.embeddings.create(
            model=self.embed_model, input=texts
        )
        return [d.embedding for d in resp.data]

    def add(self, chunks: list[dict], vectors: list[list[float]]):
        """批量添加 chunk + 显式 vector（不走 Chroma 自动 embed，避免 onnx 崩溃）"""
        ids = [c["chunk_id"] for c in chunks]
        texts = [c["content"] for c in chunks]
        metas = [{"source": c["source"]} for c in chunks]
        self.collection.add(ids=ids, embeddings=vectors, documents=texts, metadatas=metas)
        print(f"[RAG] 新增 {len(chunks)} chunks, 总计 {self.collection.count()}")

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """语义检索 Top-K Chunks"""
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
                "score": 1 - results["distances"][0][i],
            })
        return chunks

    def format_for_llm(self, query: str, top_k: int = 3) -> str:
        """检索结果格式化为 LLM 可读的 Prompt 片段"""
        chunks = self.search(query, top_k)
        if not chunks:
            return "（知识库中未找到相关内容）"
        parts = ["【知识库检索结果】"]
        for i, c in enumerate(chunks, 1):
            parts.append(f"[{i}] (来源: {c['source']}, 相关度: {c['score']:.2f})")
            parts.append(c["content"])
            parts.append("")
        return "\n".join(parts)

    def count(self) -> int:
        return self.collection.count()

    def clear(self):
        try:
            self.client.delete_collection("travel_knowledge")
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(
            name="travel_knowledge",
            metadata={"hnsw:space": "cosine"},
        )
        print("[RAG] 索引已清空")


_retriever: RAGRetriever = None


def get_retriever() -> RAGRetriever:
    global _retriever
    if _retriever is None:
        _retriever = RAGRetriever()
    return _retriever
