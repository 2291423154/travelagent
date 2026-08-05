"""Rebuild Chroma index — use new collection name to avoid crash"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["TOKENIZERS_PARALLELISM"] = "false"

from pathlib import Path
from utils.rag.retriever import RAGRetriever
from utils.rag.build_index import read_file_content, split_text

retriever = RAGRetriever()
doc_files = []

for scan_dir in [Path("utils/rag/documents"), Path("docs")]:
    if scan_dir.exists():
        for f in scan_dir.glob("*.txt"):
            doc_files.append(f)

all_chunks = []
for fp in doc_files:
    text = read_file_content(fp)
    if not text: continue
    chunks = split_text(text, fp.name)
    base = fp.stem[:20]
    for c in chunks:
        c["chunk_id"] = f"{base}_{c['chunk_id']}"
    all_chunks.extend(chunks)
    print(f"  {fp.name}: {len(chunks)} chunks")

print(f"Total: {len(all_chunks)} chunks, embedding...")

# Batch embed
texts = [c["content"] for c in all_chunks]
B = 10
all_vecs = []
for i in range(0, len(texts), B):
    batch = texts[i:i+B]
    vecs = retriever.embed(batch)
    all_vecs.extend(vecs)
    print(f"  embed batch {i//B + 1}: {len(batch)} OK")

# Write to NEW collection name (avoid crashy delete_collection)
import chromadb
client = chromadb.PersistentClient(path="utils/rag/rag_db")
col_name = "travel_knowledge_v2"
try: client.delete_collection(col_name)
except: pass
col = client.get_or_create_collection(name=col_name, metadata={"hnsw:space": "cosine"})

ids = [c["chunk_id"] for c in all_chunks]
metas = [{"source": c["source"]} for c in all_chunks]
for i in range(0, len(all_chunks), B):
    batch_ids = ids[i:i+B]
    batch_vecs = all_vecs[i:i+B]
    batch_texts = texts[i:i+B]
    batch_metas = metas[i:i+B]
    col.add(ids=batch_ids, embeddings=batch_vecs, documents=batch_texts, metadatas=batch_metas)
    print(f"  write batch {i//B + 1}: OK")

print(f"Done! {col.count()} chunks in '{col_name}'")

# Update retriever to use new collection
from utils.rag.retriever import _retriever_instance
if _retriever_instance:
    _retriever_instance.collection = col
