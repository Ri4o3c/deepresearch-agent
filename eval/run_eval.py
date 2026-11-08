"""评测脚本：多问题跑完整流程，记录耗时、评分、轮数、token、引用质量"""
import json, re, sys, time
from pathlib import Path
from langchain_core.callbacks import get_usage_metadata_callback
from app.graph import build_graph

QUESTIONS = [
    ("技术对比", "LangGraph、CrewAI、AutoGen 三个多智能体框架怎么选？"),
    ("行业趋势", "2026年中国新能源汽车出海的主要市场、挑战和趋势是什么？"),
    ("概念解释", "什么是 MCP（Model Context Protocol）协议，它解决了什么问题，生态现状如何？"),
]


def check_citations(report: str) -> dict:
    parts = re.split(r"#+\s*参考资料", report)
    body = parts[0]
    refs = re.findall(r"^\s*[-*]?\s*\[(\d+)\]", parts[1], re.M) if len(parts) > 1 else []
    ref_ids = {int(x) for x in refs}
    cited = [int(x) for x in re.findall(r"\[(\d+)\]", body)]
    invalid = sorted({c for c in cited if c not in ref_ids})
    return {"citations": len(cited), "refs": len(ref_ids), "invalid_citation_ids": invalid,
            "unused_refs": len(ref_ids - set(cited))}


def run_one(tag, q, graph, outdir):
    t0, timeline, reviews, filters = time.time(), [], [], []
    report = ""
    with get_usage_metadata_callback() as cb:
        for ev in graph.stream({"question": q}, stream_mode="updates"):
            for node, upd in ev.items():
                timeline.append((node, round(time.time() - t0, 1)))
                if node == "writer":
                    report = upd["report"]
                if node == "reviewer":
                    reviews.append(upd["reviews"][-1])
                if node == "researcher":
                    filters.append(len(upd["notes"][0]["sources"]))
    tokens = {m: {"in": u["input_tokens"], "out": u["output_tokens"]} for m, u in cb.usage_metadata.items()}
    (outdir / f"{tag}.md").write_text(report, encoding="utf-8")
    return {"tag": tag, "question": q, "seconds": round(time.time() - t0, 1),
            "rounds": len(reviews), "scores": [r["score"] for r in reviews],
            "passed": reviews[-1]["passed"] if reviews else None,
            "issues": reviews[-1]["issues"] if reviews else [],
            "researchers": len(filters), "kept_sources_per_researcher": filters,
            "report_chars": len(report), "tokens": tokens, **check_citations(report),
            "timeline": timeline}


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    only = sys.argv[2:]
    if only:
        QUESTIONS[:] = [x for x in QUESTIONS if x[0] in only]
    outdir = Path("eval") / label
    outdir.mkdir(parents=True, exist_ok=True)
    graph = build_graph()
    results = []
    for tag, q in QUESTIONS:
        print(f"\n===== {tag}: {q}")
        try:
            r = run_one(tag, q, graph, outdir)
        except Exception as e:
            r = {"tag": tag, "question": q, "error": repr(e)}
        results.append(r)
        print(json.dumps({k: v for k, v in r.items() if k not in ("timeline", "issues")}, ensure_ascii=False))
    (outdir / f"results{'_' + '_'.join(only) if only else ''}.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n结果已保存到", outdir / "results.json")
