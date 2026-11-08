"""混合检索：向量 + BM25 → RRF 融合 → 可配置的文本重排。"""
import re
from functools import lru_cache

from app.rag.embed import rerank
from app.rag.store import kb as _kb, KnowledgeBase

RRF_K = 60


@lru_cache(maxsize=128)
def _english_query(query: str) -> str:
    """只翻译检索问题，不生成答案；失败时保留原查询。"""
    from app.llm import get_llm
    try:
        response = get_llm(temperature=0, max_retries=0, max_tokens=128).invoke([
            ("system", "Translate the user's search query into English. Preserve entities and intent. "
             "Output only the translated query, without answering it or adding facts."),
            ("human", query),
        ])
        text = response.content.strip().strip('"') if isinstance(response.content, str) else ""
        if (not text or re.search(r"[\u4e00-\u9fff]", text)
                or response.response_metadata.get("finish_reason") == "length"):
            return query
        return text
    except Exception:
        return query


def rrf(*ranked: list[dict], k: int = RRF_K) -> list[dict]:
    """Reciprocal Rank Fusion：score = Σ 1/(k + rank)，只看名次，不受两路分数量纲不同的影响"""
    fused: dict[str, dict] = {}
    for lst in ranked:
        for rank, r in enumerate(lst, 1):
            item = fused.setdefault(r["cid"], dict(r, rrf=0.0))
            item["rrf"] += 1 / (k + rank)
    return sorted(fused.values(), key=lambda r: r["rrf"], reverse=True)


def hybrid_search(query: str, k: int = 5, mode: str = "hybrid_rerank", recall: int = 20,
                  store: KnowledgeBase | None = None) -> list[dict]:
    """mode: vector | bm25 | hybrid | hybrid_rerank"""
    store = store or _kb
    if mode == "vector":
        return store.vector_search(query, k)
    if mode == "bm25":
        return store.bm25_search(query, k)
    vector = store.vector_search(query, recall)
    lexical = store.bm25_search(query, recall)
    fused = rrf(vector, lexical)
    if mode == "hybrid":
        return fused[:k]
    rank_query = query
    sample = "\n".join(h["text"] for h in vector[:3])
    if (re.search(r"[\u4e00-\u9fff]", query)
            and len(re.findall(r"[A-Za-z]", sample)) > 2 * len(re.findall(r"[\u4e00-\u9fff]", sample))):
        translated = _english_query(query)
        if translated != query:
            rank_query = translated
            fused = rrf(vector, lexical, store.vector_search(translated, recall),
                        store.bm25_search(translated, recall))
    cands = fused[:recall]
    ranked = rerank(rank_query, [c["text"] for c in cands], top_n=k)
    if ranked is None:  # 降级
        return [dict(c, rerank=None) for c in cands[:k]]
    return [dict(cands[i], rerank=s) for i, s in ranked]


MIN_RERANK = 0.25  # 重排相关度过低的片段不交给研究员，避免"硬凑"本地资料


def kb_search(query: str, k: int = 4) -> list[dict]:
    """给 Researcher 用的工具：返回与网页来源同结构的 source（type=local）"""
    if _kb.is_empty():
        return []
    hits = hybrid_search(query, k=k)
    out = []
    for h in hits:
        if h.get("rerank") is not None and h["rerank"] < MIN_RERANK:
            continue
        out.append({
            "title": f"{h['file']} 第{h['page']}页",
            "url": f"/api/kb/docs/{h['doc_id']}/file#page={h['page']}",
            "content": h["text"],
            "score": 1.0 if h.get("rerank") is None else max(0.3, h["rerank"]),
            "type": "local", "file": h["file"], "page": h["page"], "doc_id": h["doc_id"],
        })
    return out
