"""网页搜索提供方抽象：SearXNG（主，自部署）→ ddgs（备）→ Tavily（可选）。
统一返回 researcher 使用的结构：{id, title, url, content, score(0~1), type="web", provider}。
按 SEARCH_PROVIDERS（默认 "searxng,ddgs"）顺序尝试，报错或结果为空时自动切换下一个。"""
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol
from app.search.session import SearchSession, current_session, unique_sources

import httpx

from app import observability

log = logging.getLogger("search")
CONTENT_LIMIT = max(1, int(os.environ.get("SEARCH_CONTENT_LIMIT", "1500")))
FETCH_TOP = max(0, int(os.environ.get("SEARCH_FETCH_TOP", "2")))      # 只给前几条抓全文，控制耗时
FETCH_TIMEOUT = float(os.environ.get("SEARCH_FETCH_TIMEOUT", "6"))
FETCH_WORKERS = 4


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, max_results: int) -> list[dict]: ...


def _rank_score(i: int, n: int) -> float:
    """按名次给分：第 1 名 1.0，最后一名约 0.5，保证与原 >=0.3 的粗筛兼容"""
    return round(1.0 - 0.5 * i / max(n, 1), 3)


class SearXNGProvider:
    name = "searxng"

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.environ.get("SEARXNG_URL", "http://127.0.0.1:8080")).rstrip("/")

    def search(self, query: str, max_results: int) -> list[dict]:
        # 本机服务不走系统代理（127.0.0.1:1088）
        with httpx.Client(trust_env=False, timeout=15) as c:
            r = c.get(f"{self.base_url}/search", params={"q": query, "format": "json"})
            r.raise_for_status()
            rows = [x for x in r.json().get("results", []) if x.get("url")][:max_results]
        top = max((x.get("score") or 0 for x in rows), default=0) or 1
        n = len(rows)
        # SearXNG 的 score 是多引擎合并分，量纲不固定：归一化后与名次分各占一半
        return [{"title": x.get("title", ""), "url": x["url"], "content": x.get("content", ""),
                 "score": round(0.5 * (x.get("score") or 0) / top + 0.5 * _rank_score(i, n), 3)}
                for i, x in enumerate(rows)]


class DDGSProvider:
    name = "ddgs"

    def search(self, query: str, max_results: int) -> list[dict]:
        from ddgs import DDGS
        rows = list(DDGS(timeout=10).text(query, max_results=max_results) or [])
        return [{"title": x.get("title", ""), "url": x.get("href", ""), "content": x.get("body", ""),
                 "score": _rank_score(i, len(rows))} for i, x in enumerate(rows) if x.get("href")]


class TavilyProvider:
    name = "tavily"

    def search(self, query: str, max_results: int) -> list[dict]:
        from tavily import TavilyClient
        resp = TavilyClient(os.environ["TAVILY_API_KEY"]).search(query, max_results=max_results, search_depth="advanced")
        return [{"title": r["title"], "url": r["url"], "content": r["content"], "score": r.get("score", 1.0),
                 "full": True} for r in resp["results"]]


PROVIDERS = {"searxng": SearXNGProvider, "ddgs": DDGSProvider, "tavily": TavilyProvider}


def get_providers(order: str | None = None) -> list:
    names = (order or os.environ.get("SEARCH_PROVIDERS", "searxng,ddgs")).split(",")
    return [PROVIDERS[n.strip()]() for n in names if n.strip() in PROVIDERS]


def fetch_text(url: str) -> str:
    """抓正文：trafilatura 提取，失败返回空串（保留搜索摘要）"""
    try:
        import trafilatura
        with httpx.Client(trust_env=True, timeout=FETCH_TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 deep-research"}) as c:
            r = c.get(url)
            if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
                return ""
        return (trafilatura.extract(r.text, include_comments=False) or "")[:CONTENT_LIMIT]
    except Exception as e:  # noqa: BLE001
        log.info("抓取正文失败 %s: %s", url, e)
        return ""


def _enrich(rows: list[dict], session: SearchSession | None = None) -> None:
    todo = [r for r in rows[:FETCH_TOP] if not r.get("full")]
    if not todo:
        return
    if session is not None:
        session.metrics["fetch_calls"] += len(todo)
    with ThreadPoolExecutor(FETCH_WORKERS) as ex:
        for r, text in zip(todo, ex.map(lambda x: fetch_text(x["url"]), todo)):
            if len(text) > len(r["content"]):
                r["content"] = text


def web_search(query: str, max_results: int = 5, providers: list | None = None, fetch: bool = True,
               *, session: SearchSession | None = None, bypass_cache: bool = False) -> list[dict]:
    """缓存仅在显式 session / 当前 research() 内生效；fresh 调用可绕过。"""
    session = session if session is not None else current_session.get()
    selected = providers if providers is not None else get_providers()
    # 自定义 provider 没有稳定配置契约，按实例隔离；内置 key 覆盖端点及所有搜索/抓取参数。
    identity = tuple((p.name, type(p).__module__, type(p).__qualname__,
                      p.base_url if isinstance(p, SearXNGProvider) else
                      (None if type(p) in (DDGSProvider, TavilyProvider) else id(p))) for p in selected)
    key = (query, max_results, identity, fetch, FETCH_TOP, FETCH_TIMEOUT, CONTENT_LIMIT)
    run = lambda: _web_search(query, max_results, selected, fetch, session)
    if session is None:
        return run()
    return session.search(key, run, bypass=bypass_cache)


def _web_search(query, max_results, providers, fetch, session):
    """依次尝试各 provider；全部失败时抛出最后一个异常，交给 researcher 的降级逻辑"""
    last_err = None
    for p in providers if providers is not None else get_providers():
        t = time.monotonic()
        if session is not None:
            session.metrics["search_calls"] += 1
        with observability.span("web_search", as_type="tool", query=query, provider=p.name, max_results=max_results):
            try:
                rows = p.search(query, max_results)
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("搜索 %s 失败，切换下一个：%s", p.name, str(e)[:120])
                continue
        rows = unique_sources(rows)[:max(0, max_results)]
        if not rows:
            log.warning("搜索 %s 无结果，切换下一个", p.name)
            continue
        if fetch:
            _enrich(rows, session)
        log.info("搜索 %s 返回 %d 条，用时 %.1fs", p.name, len(rows), time.monotonic() - t)
        return [{"id": i + 1, "title": r["title"], "url": r["url"], "content": (r["content"] or "")[:CONTENT_LIMIT],
                 "score": r["score"], "type": "web", "provider": p.name} for i, r in enumerate(rows)]
    if last_err:
        raise last_err
    return []