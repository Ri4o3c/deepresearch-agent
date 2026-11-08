"""本地知识库（RAG）：解析 → 切块 → 向量 + BM25 混合检索 → RRF 融合 → 重排"""
from app.rag.store import kb, KnowledgeBase  # noqa: F401
from app.rag.retrieve import kb_search, hybrid_search  # noqa: F401
