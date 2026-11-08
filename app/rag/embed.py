"""DashScope 云端 Embedding / Rerank。
选择理由：本机无 GPU，bge-m3 + bge-reranker 本地模型要下载 ~3GB 且 CPU 推理慢；
DashScope 的 text-embedding-v3（中英文效果好、1024 维）和 gte-rerank-v2 走 HTTP，开箱即用。
重排接口失败时降级为 RRF 原始顺序，保证检索不中断。"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
import httpx

EMBED_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
RERANK_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
EMBED_MODEL = "text-embedding-v3"
RERANK_MODEL = os.environ.get("RERANK_MODEL", "qwen3-rerank")
DIM = 1024
BATCH = 10  # text-embedding-v3 单次最多 10 条


def _key() -> str:
    k = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY")
    if not k:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY 环境变量")
    return k


def _post(url: str, payload: dict, tries: int = 4) -> dict:
    for i in range(tries):
        try:
            r = httpx.post(url, headers={"Authorization": f"Bearer {_key()}"}, json=payload, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                raise httpx.HTTPStatusError(r.text[:200], request=r.request, response=r)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPStatusError, httpx.TransportError):
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("unreachable")


def _embed_batch(batch: list[str]) -> list[list[float]]:
    data = _post(EMBED_URL, {"model": EMBED_MODEL, "input": [t[:8000] or " " for t in batch], "dimensions": DIM})["data"]
    return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]


def embed(texts: list[str], workers: int = 4) -> list[list[float]]:
    """分批（每批 10 条）并发请求，保持原顺序"""
    batches = [texts[i:i + BATCH] for i in range(0, len(texts), BATCH)]
    if len(batches) == 1:
        return _embed_batch(batches[0])
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return [v for vecs in ex.map(_embed_batch, batches) for v in vecs]


def rerank(query: str, docs: list[str], top_n: int) -> list[tuple[int, float]] | None:
    """返回 [(原下标, 相关度0~1)]；失败返回 None 由调用方降级"""
    if not docs:
        return []
    try:
        res = _post(RERANK_URL, {"model": RERANK_MODEL,
                                 "input": {"query": query, "documents": docs},
                                 "parameters": {"top_n": top_n, "return_documents": False}}, tries=2)
        return [(r["index"], r["relevance_score"]) for r in res["output"]["results"]]
    except Exception as e:  # noqa: BLE001
        print(f"    [rerank 降级] {e}")
        return None
