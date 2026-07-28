"""
离线建库脚本 — 旅游攻略文档 → 切分 → Embedding → Chroma
运行一次即可：python -m utils.rag.build_index
"""
import os
import re
from pathlib import Path
from utils.rag.retriever import RAGRetriever

# 切分参数
CHUNK_SIZE = 500        # 中文字符数
CHUNK_OVERLAP = 100     # 重叠字符数


def split_text(text: str, source_name: str) -> list[dict]:
    """
    按段落 + 句子边界智能切分。
    优先在句号、换行等自然边界下刀，避免从中间切断句子。
    """
    chunks = []
    # 先按双换行（段落边界）粗切
    paragraphs = re.split(r'\n\s*\n', text)
    current_chunk = ""
    chunk_idx = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # 如果当前 chunk + 新段落不超过限制，追加
        if len(current_chunk) + len(para) <= CHUNK_SIZE:
            current_chunk += para + "\n"
        else:
            # 当前 chunk 先存
            if len(current_chunk.strip()) >= 50:  # 过滤太短的碎片
                chunks.append({
                    "content": current_chunk.strip(),
                    "source": source_name,
                    "chunk_id": chunk_idx,
                })
                chunk_idx += 1

            # 新段落开始新 chunk，带 overlap
            current_chunk = current_chunk[-CHUNK_OVERLAP:] + para + "\n" if len(current_chunk) >= CHUNK_OVERLAP else para + "\n"

    # 最后一个 chunk
    if len(current_chunk.strip()) >= 50:
        chunks.append({
            "content": current_chunk.strip(),
            "source": source_name,
            "chunk_id": chunk_idx,
        })

    return chunks


def read_file_content(fpath: Path) -> str:
    """读取 txt 或 pdf 文件内容"""
    suffix = fpath.suffix.lower()
    if suffix == ".txt":
        with open(fpath, "r", encoding="utf-8") as f:
            return f.read()
    elif suffix == ".pdf":
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(str(fpath))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            print(f"[RAG] 需要安装 PyPDF2: pip install PyPDF2（跳过 {fpath.name}）")
            return ""
    else:
        print(f"[RAG] 不支持的文件格式: {suffix}")
        return ""


def build_index(documents_dir: str = None):
    """离线建库：读取 documents/ 下所有 txt 和 pdf，切分 → Embedding → Chroma"""
    if documents_dir is None:
        documents_dir = os.path.join(os.path.dirname(__file__), "documents")

    retriever = RAGRetriever()
    doc_path = Path(documents_dir)

    if not doc_path.exists():
        print(f"[RAG] 文档目录不存在: {documents_dir}")
        return

    # 支持 txt 和 pdf（Windows glob 不区分大小写，写小写即可）
    doc_files = list(doc_path.glob("*.txt")) + list(doc_path.glob("*.pdf"))
    if not doc_files:
        print(f"[RAG] 没有找到文档文件: {documents_dir}")
        return

    print(f"[RAG] 找到 {len(doc_files)} 个文档，开始建库...")

    all_chunks = []
    for fpath in doc_files:
        print(f"[RAG]   处理: {fpath.name}")
        text = read_file_content(fpath)
        if not text:
            continue
        chunks = split_text(text, fpath.name)
        # 给每个 chunk 的 ID 加上文件名前缀，避免不同文档的 chunk_id 冲突
        base_name = fpath.stem[:20]  # 取文件名前20字符
        for c in chunks:
            c["chunk_id"] = f"{base_name}_{c['chunk_id']}"
        all_chunks.extend(chunks)
        print(f"[RAG]     → {len(chunks)} 个 chunk")

    print(f"[RAG] 总计 {len(all_chunks)} 个 chunk，开始 Embedding...")

    # 清空旧数据
    try:
        retriever.client.delete_collection("travel_knowledge")
        retriever.collection = retriever.client.get_or_create_collection(
            name="travel_knowledge",
            metadata={"hnsw:space": "cosine"},
        )
    except Exception:
        pass

    # 批量 Embedding + 入库
    batch_size = 20
    for i in range(0, len(all_chunks), batch_size):
        batch = all_chunks[i : i + batch_size]
        texts = [c["content"] for c in batch]
        embeddings = retriever.embed(texts)
        ids = [f"chunk_{c['chunk_id']}" for c in batch]  # chunk_id 已包含文件名前缀，全局唯一
        metadatas = [{"source": c["source"], "chunk_id": c["chunk_id"]} for c in batch]

        retriever.collection.add(
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
            ids=ids,
        )
        print(f"[RAG]   已入库 {min(i + batch_size, len(all_chunks))}/{len(all_chunks)}")

    print(f"[RAG] 建库完成！共 {retriever.doc_count} 条记录")


if __name__ == "__main__":
    build_index()
