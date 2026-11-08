"""检索评测：vector / bm25 / hybrid(RRF) / hybrid+rerank 的 Hit@k 与 MRR
用独立的临时知识库 data/eval_kb，不污染用户的知识库。
运行：python -m eval.rag.eval_retrieval [--rebuild]"""
import json
import sys
import time
from pathlib import Path

from app.rag.store import KnowledgeBase
from app.rag.retrieve import hybrid_search

HERE = Path(__file__).parent
K = 5


def build(rebuild: bool) -> KnowledgeBase:
    store = KnowledgeBase(root="data/eval_kb")
    if rebuild:
        for d in store.docs():
            store.delete(d["id"])
    if store.is_empty():
        for f in sorted((HERE / "corpus").iterdir()):
            doc = store.add_file(f)
            print(f"  入库 {doc['filename']}: {doc['pages']} 页 {doc['chunks']} 块 {doc['ingest_seconds']}s")
    return store


def relevant(hit: dict, item: dict) -> bool:
    return hit["file"] == item["file"] and any(k.lower() in hit["text"].lower() for k in item["kw"])


def main():
    store = build("--rebuild" in sys.argv)
    qa = json.loads((HERE / "qa.json").read_text(encoding="utf-8"))
    rows, detail = {}, []
    for mode in ["vector", "bm25", "hybrid", "hybrid_rerank"]:
        h1 = h3 = h5 = rr = 0.0
        t = time.time()
        for item in qa:
            hits = hybrid_search(item["q"], k=K, mode=mode, store=store)
            rank = next((i + 1 for i, h in enumerate(hits) if relevant(h, item)), None)
            h1 += rank == 1
            h3 += bool(rank and rank <= 3)
            h5 += bool(rank)
            rr += 1 / rank if rank else 0
            detail.append({"mode": mode, "q": item["q"], "rank": rank})
        n = len(qa)
        rows[mode] = {"hit@1": round(h1 / n, 3), "hit@3": round(h3 / n, 3), "hit@5": round(h5 / n, 3),
                      "mrr": round(rr / n, 3), "avg_ms": round((time.time() - t) / n * 1000)}
    print(f"\n{'模式':<15}{'Hit@1':>8}{'Hit@3':>8}{'Hit@5':>8}{'MRR':>8}{'耗时ms':>9}")
    for m, r in rows.items():
        print(f"{m:<15}{r['hit@1']:>8}{r['hit@3']:>8}{r['hit@5']:>8}{r['mrr']:>8}{r['avg_ms']:>9}")
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / "retrieval.json").write_text(json.dumps({"k": K, "n": len(qa), "summary": rows, "detail": detail},
                                                   ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
