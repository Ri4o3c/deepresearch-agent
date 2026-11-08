import os
from pydantic import BaseModel, Field
from app import observability
from langchain_core.messages import SystemMessage, HumanMessage
from app.llm import get_llm
from app.utils import today_hint
from app.rag import kb_search
from app.search import web_search
from app.search.session import SearchSession, unique_sources, url_key



class Finding(BaseModel):
    point: str = Field(description="从资料中提炼的一条关键事实或观点")
    source_ids: list[int] = Field(description="支撑这条要点的资料编号")


class ResearchNote(BaseModel):
    question: str
    findings: list[Finding] = Field(description="3到6条要点")


SYSTEM_PROMPT = """你是一名严谨的研究员。根据给定的检索资料回答子问题。
要求：
1. 只使用资料中出现的信息，不要编造；
2. 每条要点都必须标注来源编号；
3. 资料不足时如实说明；
4. 标注为"本地文档"的资料来自用户上传的知识库，属于一手材料，与网页冲突时优先采信并指出差异。"""


def search(query: str, max_results: int = 5) -> list[dict]:
    """网页检索：SearXNG → ddgs 自动降级（见 app/search）"""
    return web_search(query, max_results=max_results)


class Queries(BaseModel):
    queries: list[str] = Field(description="2个更精准的搜索关键词组合")


MIN_SOURCES = 3
CONTEXT_LIMIT = max(1, int(os.environ.get("RESEARCH_CONTEXT_LIMIT", "6000")))
SOURCE_LIMIT = max(1, int(os.environ.get("SEARCH_CONTENT_LIMIT", "1500")))


def build_context(sources):
    """Bound the complete retrieval context including headers; retain only supplied evidence/IDs."""
    kept, blocks, used = [], [], 0
    # Preserve local evidence first, including per-page URL/metadata.
    ordered = [s for s in sources if s.get("type") == "local"] + [s for s in sources if s.get("type") != "local"]
    for source in unique_sources(ordered):
        tag = "本地文档" if source.get("type") == "local" else "网页"
        header = f"[{source['id']}]（{tag}）{source['title']}\n"
        available = CONTEXT_LIMIT - used - (2 if blocks else 0) - len(header)
        if available <= 0:
            continue
        content = (source.get("content") or "")[:min(SOURCE_LIMIT, available)]
        if not content:
            continue
        blocks.append(header + content)
        used += len(header) + len(content) + (2 if len(blocks) > 1 else 0)
        kept.append(dict(source, content=content))
    return "\n\n".join(blocks), kept


def rewrite_queries(question: str) -> list[str]:
    llm = get_llm(temperature=0.5).with_structured_output(Queries, method="function_calling")
    return llm.invoke([
        SystemMessage("把子问题改写成2个适合搜索引擎的简短关键词查询（中英文各一个），聚焦核心实体。"),
        HumanMessage(question),
    ]).queries[:2]


class Relevance(BaseModel):
    relevant_ids: list[int] = Field(description="与子问题真正相关的资料编号")


def filter_sources(question: str, sources: list[dict]) -> list[dict]:
    """两道过滤：搜索相关度分数（已归一化到 0~1）粗筛 + LLM 判断主题是否相关"""
    sources = [s for s in sources if s["score"] >= 0.3]
    if not sources:
        return []
    # Keep each candidate represented; bound titles as well as content.
    lines, candidates, used = [], [], 0
    for source in sources:
        line = f"[{source['id']}] {source['title'][:200]}：{source['content'][:200]}"
        if used + len(line) + bool(lines) > CONTEXT_LIMIT:
            continue
        used += len(line) + bool(lines)
        lines.append(line)
        candidates.append(source)
    sources = candidates
    if not sources:
        return []
    brief = "\n".join(lines)
    llm = get_llm(temperature=0).with_structured_output(Relevance, method="function_calling")
    rel = llm.invoke([
        SystemMessage("判断每条资料是否与子问题主题直接相关。只要沾边的广告、同名无关内容一律剔除。"),
        HumanMessage(f"子问题：{question}\n\n资料：\n{brief}"),
    ])
    keep = set(rel.relevant_ids)
    return [s for s in sources if s["id"] in keep]


def search_web(query: str, max_results: int) -> list[dict]:
    """网页检索降级：搜索服务全部不可用/网络故障时返回空，让本地知识库仍能支撑回答，而不是整条研究崩溃"""
    try:
        return search(query, max_results=max_results)
    except Exception as e:  # noqa: BLE001
        print(f"    [网页检索失败] {type(e).__name__}: {str(e)[:80]}")
        return []


def search_local(question: str) -> list[dict]:
    """本地知识库检索（库为空时直接返回空，出错不影响网页检索）"""
    try:
        with observability.span("kb_search", as_type="retriever", query=question, k=4):
            return kb_search(question, k=4)
    except Exception as e:  # noqa: BLE001
        print(f"    [本地库检索失败] {e}")
        return []


def research(question: str) -> tuple[ResearchNote, list[dict]]:
    with SearchSession() as session:
        return _research(question, session)


def _research(question, session):
    local = search_local(question)
    raw = [dict(s, id=i + 1) for i, s in enumerate(unique_sources(search_web(question, 8) + local))]
    sources = filter_sources(question, raw)
    if len(sources) < MIN_SOURCES:  # 检索落空：改写查询再搜，合并去重
        seen = {url_key(s) for s in sources}
        extra = []
        for q in rewrite_queries(question):
            for source in search_web(q, 5):
                key = url_key(source)
                if key not in seen:
                    seen.add(key)
                    extra.append(dict(source, id=len(raw) + len(extra) + 1))
        sources += filter_sources(question, extra)
        sources = [dict(s, id=i + 1) for i, s in enumerate(sources)]
        print(f"    [重试] {question[:20]}… 改写查询后共 {len(sources)} 条")
    if not sources:
        return ResearchNote(question=question, findings=[]), []
    n_local = sum(s.get("type") == "local" for s in sources)
    print(f"    [过滤] {question[:20]}… 保留 {len(sources)}/{len(raw)} 条（本地 {n_local}）")
    context, sources = build_context(sources)
    session.metrics["context_chars"] = len(context)
    if not sources:
        return ResearchNote(question=question, findings=[]), []
    llm = get_llm(temperature=0).with_structured_output(ResearchNote, method="function_calling")
    note = llm.invoke([
        SystemMessage(SYSTEM_PROMPT + "\n" + today_hint()),
        HumanMessage(f"子问题：{question}\n\n检索资料：\n{context}"),
    ])
    return note, sources


if __name__ == "__main__":
    note, sources = research("LangGraph 和 CrewAI 在多智能体编排上有什么区别？")
    print("子问题:", note.question)
    for f in note.findings:
        print(f"- {f.point} {f.source_ids}")
    print("\n来源:")
    for s in sources:
        print(f"[{s['id']}] {s['title']} {s['url']}")

