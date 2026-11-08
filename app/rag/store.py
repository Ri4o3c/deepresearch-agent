"""知识库存储：Chroma 持久化向量 + 内存 BM25 索引（jieba 分词），文档清单存 JSON"""
import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

import chromadb
import jieba
from rank_bm25 import BM25Okapi

from app.rag.embed import embed
from app.rag.ingest import chunk_document, SUPPORTED

jieba.setLogLevel(60)
_WORD = re.compile(r"[\w\u4e00-\u9fff]+")
STOP = set("的 了 和 是 在 与 及 或 对 中 为 有 也 就 都 而 被 把 这 那 a an the of to in and or is are for on with by as at be".split())


def tokenize(text: str) -> list[str]:
    return [t for t in (w.strip().lower() for w in jieba.cut_for_search(text))
            if t and _WORD.fullmatch(t) and t not in STOP]


class KnowledgeBase:
    def __init__(self, root: str = "data"):
        self.root = Path(root)
        self.files = self.root / "kb_files"
        self.files.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.root / "kb_docs.json"
        self.client = chromadb.PersistentClient(path=str(self.root / "chroma"))
        self.col = self.client.get_or_create_collection("knowledge_base", embedding_function=None, metadata={"hnsw:space": "cosine"})
        self.lock = threading.RLock()
        self._bm25 = None
        self._bm25_rows: list[dict] = []

    # ---------- 文档清单 ----------
    def docs(self) -> list[dict]:
        if not self.meta_path.exists():
            return []
        return json.loads(self.meta_path.read_text(encoding="utf-8"))

    def _save(self, docs: list[dict]):
        self.meta_path.write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")

    def get_doc(self, doc_id: str) -> dict | None:
        return next((d for d in self.docs() if d["id"] == doc_id), None)

    def is_empty(self) -> bool:
        return self.col.count() == 0

    # ---------- 写入 / 删除 ----------
    def add_file(self, src: Path, filename: str | None = None) -> dict:
        filename = filename or src.name
        ext = Path(filename).suffix.lower()
        if ext not in SUPPORTED:
            raise ValueError(f"不支持的文件类型：{ext}（仅支持 PDF / MD / TXT）")
        doc_id = uuid.uuid4().hex[:10]
        dst = self.files / f"{doc_id}{ext}"
        shutil.copyfile(src, dst)
        t = time.time()
        try:
            chunks = chunk_document(dst)
            if not chunks:
                raise ValueError("没有解析出文字（可能是扫描版 PDF）")
            vecs = embed([c["text"] for c in chunks])
        except Exception:
            dst.unlink(missing_ok=True)
            raise
        pages = max(c["page"] for c in chunks)
        with self.lock:
            self.col.add(
                ids=[f"{doc_id}-{i}" for i in range(len(chunks))],
                embeddings=vecs,
                documents=[c["text"] for c in chunks],
                metadatas=[{"doc_id": doc_id, "file": filename, "page": c["page"], "chunk": i}
                           for i, c in enumerate(chunks)],
            )
            doc = {"id": doc_id, "filename": filename, "ext": ext, "chunks": len(chunks), "pages": pages,
                   "size": dst.stat().st_size, "created_at": int(time.time()),
                   "ingest_seconds": round(time.time() - t, 2)}
            self._save(self.docs() + [doc])
            self._bm25 = None
        return doc

    def delete(self, doc_id: str) -> bool:
        with self.lock:
            doc = self.get_doc(doc_id)
            if not doc:
                return False
            self.col.delete(where={"doc_id": doc_id})
            (self.files / f"{doc_id}{doc['ext']}").unlink(missing_ok=True)
            self._save([d for d in self.docs() if d["id"] != doc_id])
            self._bm25 = None
            return True

    def file_path(self, doc_id: str) -> Path | None:
        doc = self.get_doc(doc_id)
        return (self.files / f"{doc_id}{doc['ext']}") if doc else None

    # ---------- 检索 ----------
    def vector_search(self, query: str, k: int = 10) -> list[dict]:
        if self.is_empty():
            return []
        res = self.col.query(query_embeddings=embed([query]), n_results=min(k, self.col.count()))
        return [{"cid": cid, "text": doc, **meta, "score": 1 - dist}
                for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0],
                                                res["metadatas"][0], res["distances"][0])]

    def _ensure_bm25(self):
        with self.lock:
            if self._bm25 is not None:
                return
            got = self.col.get(include=["documents", "metadatas"])
            self._bm25_rows = [{"cid": cid, "text": d, **m}
                               for cid, d, m in zip(got["ids"], got["documents"], got["metadatas"])]
            corpus = [tokenize(r["text"]) or ["_"] for r in self._bm25_rows]
            self._bm25 = BM25Okapi(corpus) if corpus else None

    def bm25_search(self, query: str, k: int = 10) -> list[dict]:
        self._ensure_bm25()
        if not self._bm25:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        top = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [dict(self._bm25_rows[i], score=float(scores[i])) for i in top if scores[i] > 0]


kb = KnowledgeBase()
