"""Rebuild Chroma index — txt only (PDF 解析有 C 层崩溃)"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path
from utils.rag.retriever import RAGRetriever
from utils.rag.build_index import read_file_content, split_text

retriever = RAGRetriever()
doc_files = []

for scan_dir in [Path("utils/rag/documents"), Path("docs")]:
    if scan_dir.exists():
        for f in scan_dir.glob("*.txt"):
            doc_files.append(f)

print(f"找到 {len(doc_files)} 个 txt 文件")

all_chunks = []
for fp in doc_files:
    text = read_file_content(fp)
    if not text:
        continue
    chunks = split_text(text, fp.name)
    base = fp.stem[:20]
    for c in chunks:
        c["chunk_id"] = f"{base}_{c['chunk_id']}"
    all_chunks.extend(chunks)
    print(f"  {fp.name}: {len(chunks)} chunks")

print(f"总计 {len(all_chunks)} chunks")

# 清空旧索引
try:
    retriever.client.delete_collection("travel_knowledge")
    print("旧索引已清除")
except Exception:
    pass

retriever.collection = retriever.client.get_or_create_collection(
    name="travel_knowledge",
    metadata={"hnsw:space": "cosine"},
)

# 分批 embed + 写入（每批最多 25）
batch_size = 10
for i in range(0, len(all_chunks), batch_size):
    batch = all_chunks[i : i + batch_size]
    texts = [c["content"] for c in batch]
    ids = [c["chunk_id"] for c in batch]
    metas = [{"source": c["source"]} for c in batch]
    vecs = retriever.embed(texts)
    retriever.collection.add(
        ids=ids, embeddings=vecs, documents=texts, metadatas=metas
    )
    print(f"  batch {i // batch_size + 1}: {len(batch)} chunks")

print(f"完成！索引共 {retriever.collection.count()} chunks")
