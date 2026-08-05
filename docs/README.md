此目录存放 RAG 知识库文档。

项目同时支持 TXT 和 PDF 两种格式：
- TXT：示例文档（便于 Git 版本管理和在线预览）
- PDF：生产环境建议放入旅游攻略 PDF 原版，`build_index.py` 自动调用 pypdf 解析

当前 Git 仓库仅含 TXT 示例，PDF 文件已通过 .gitignore 排除。
预置的向量索引（`utils/rag/rag_db/index.json`）基于 20 份文档构建（含 PDF）。
如需更新索引，将文档放入后运行：
    python -m utils.rag.build_index
