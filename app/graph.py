import re
import operator
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from langchain_core.messages import SystemMessage, HumanMessage
from app.llm import get_llm
from app.planner import plan_research
from app.researcher import research
from app.reviewer import review_report
from app.utils import today_hint

MAX_ROUNDS = 2  # 最多补查两轮，防止无限循环
MAX_CONCURRENCY = 6  # 并行检索 Agent 上限，避免触发模型 API 并发限制


class ResearchState(TypedDict):
    question: str
    sub_questions: list[str]
    notes: Annotated[list[dict], operator.add]   # 并行结果自动合并
    report: str
    round: int
    reviews: Annotated[list[dict], operator.add]
    pending: list[str]  # 本轮待检索的子问题


class WorkerState(TypedDict):
    sub_question: str


def planner_node(state: ResearchState):
    plan = plan_research(state["question"])
    qs = [sq.question for sq in plan.sub_questions]
    return {"sub_questions": qs, "pending": qs, "round": 0}


def dispatch(state: ResearchState):
    # 每个子问题派一个检索 Agent，并行执行
    return [Send("researcher", {"sub_question": q}) for q in state["pending"]]


def researcher_node(state: WorkerState):
    note, sources = research(state["sub_question"])
    # 子问题是卡片与 checkpoint 的身份；不要用模型改写后的文本替换它。
    return {"notes": [{"question": state["sub_question"],
                       "findings": [f.model_dump() for f in note.findings],
                       "sources": sources}]}


WRITER_PROMPT = """你是一名专业研究报告撰写人。根据研究笔记写一份结构清晰的中文 Markdown 报告。
结构：
1. 开头"摘要"：3到5句话直接回答原问题；
2. 正文按逻辑分章节，围绕原问题组织，而不是机械罗列子问题；
3. 结尾"结论与建议"：给出明确、可操作的结论（如适用，给出按场景/条件的选择建议）；
4. 文末"参考资料"，格式：[n] 标题 - URL（照抄笔记中的标题和链接，本地文档保留"📄本地："前缀和 /api/kb/ 链接）。
规则：
- 每个论断后用 [n] 标注引用；只使用笔记中的信息；
- 某个子问题标注为"资料不足"时，如实说明缺口，绝不用无关内容填充；
- 同一指标在不同来源中数字不同时，必须说明差异和可能的口径/时间原因，并给出你采信的数字及理由；
- 区分已发生的事实和预测，预测要写明是谁的预测；
- 全文保持前后一致，不要自相矛盾。"""


_KB_LINK = re.compile(r"/api/kb/docs/(\w+)/file(?:#page=(\d+))?")


def fix_local_links(report: str, local_sources: list[dict]) -> str:
    """LLM 抄写长 ID 时偶尔会改错字符，导致本地原文链接 404。
    逐行校验 /api/kb/docs/{id} 链接：ID 不存在时，按同一行出现的文件名（+页码）换回真实链接。"""
    valid = {s["url"].split("#")[0] for s in local_sources}
    out = []
    for line in report.splitlines():
        for m in list(_KB_LINK.finditer(line)):
            if m.group(0).split("#")[0] in valid:
                continue
            cands = [s for s in local_sources if s.get("file") and s["file"] in line]
            page = m.group(2)
            best = next((s for s in cands if page and str(s.get("page")) == page), cands[0] if cands else None)
            if best:
                line = line.replace(m.group(0), best["url"])
        out.append(line)
    return "\n".join(out)


def writer_node(state: ResearchState):
    # 给所有来源统一重新编号，避免不同子问题的 [1] 冲突
    url_to_id, refs, blocks = {}, [], []
    for note in state["notes"]:
        local = {}
        for s in note["sources"]:
            if s["url"] not in url_to_id:
                url_to_id[s["url"]] = len(url_to_id) + 1
                tag = "📄本地：" if s.get("type") == "local" else ""
                refs.append(f"[{url_to_id[s['url']]}] {tag}{s['title']} - {s['url']}")
            local[s["id"]] = url_to_id[s["url"]]
        if not note["findings"]:
            blocks.append(f"## {note['question']}\n（资料不足：未检索到相关可靠资料）")
            continue
        lines = [f"- {f['point']} " + "".join(f"[{local[i]}]" for i in f["source_ids"] if i in local)
                 for f in note["findings"]]
        blocks.append(f"## {note['question']}\n" + "\n".join(lines))
    content = "\n\n".join(blocks) + "\n\n# 参考资料\n" + "\n".join(refs)
    feedback = ""
    if state.get("reviews"):  # 修订轮：把上一轮审稿意见交给写手
        last = state["reviews"][-1]
        feedback = ("\n\n上一版报告的审稿意见（本次必须逐条修正）：\n"
                    + "\n".join(f"- {i}" for i in last["issues"]))
    report = get_llm(temperature=0.3).invoke([
        SystemMessage(WRITER_PROMPT + "\n" + today_hint()),
        HumanMessage(f"研究问题：{state['question']}\n\n研究笔记：\n{content}{feedback}"),
    ]).content
    return {"report": report}


def reviewer_node(state: ResearchState):
    r = review_report(state["question"], state["report"], state["sub_questions"])
    rnd = state["round"] + 1
    # 只有真正要补查时才把补充问题并入子问题列表；通过时作为"可选深入方向"保留在审稿记录里
    will_research = (not r.passed) and rnd < MAX_ROUNDS + 1 and bool(r.follow_up_questions)
    seen = set(state["sub_questions"])
    new_qs = [q for q in r.follow_up_questions[:3] if q not in seen] if will_research else []
    return {"reviews": [r.model_dump()],
            "pending": new_qs,
            "sub_questions": state["sub_questions"] + new_qs,
            "round": rnd}


def after_review(state: ResearchState):
    if not state["pending"] or state["round"] >= MAX_ROUNDS + 1:
        return END
    return dispatch(state)  # 打回：只对补充子问题并行检索


def build_graph(checkpointer=None):
    g = StateGraph(ResearchState)
    g.add_node("planner", planner_node)
    g.add_node("researcher", researcher_node)
    g.add_node("writer", writer_node)
    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", dispatch, ["researcher"])
    g.add_edge("researcher", "writer")
    g.add_node("reviewer", reviewer_node)
    g.add_edge("writer", "reviewer")
    g.add_conditional_edges("reviewer", after_review, ["researcher", END])
    return g.compile(checkpointer=checkpointer).with_config({"max_concurrency": MAX_CONCURRENCY})


if __name__ == "__main__":
    import time
    graph = build_graph()
    t = time.time()
    from app import observability
    q = "LangGraph、CrewAI、AutoGen 三个多智能体框架怎么选？"
    for event in graph.stream({"question": q}, observability.build_config("cli-" + str(int(t)), q, ["cli"]),
                              stream_mode="updates"):
        for node in event:
            print(f"[{time.time() - t:5.1f}s] {node} 完成")
            if node == "writer":
                report = event[node]["report"]
            if node == "reviewer":
                r = event[node]["reviews"][-1]
                print(f"         评分 {r['score']}/10，通过={r['passed']}")
                for i in r["issues"]:
                    print(f"         问题: {i}")
                for q in r["follow_up_questions"]:
                    print(f"         补查: {q}")
    with open("report.md", "w", encoding="utf-8") as f:
        f.write(report)
    print("\n报告已保存到 report.md，前 800 字：\n")
    print(report[:800])


