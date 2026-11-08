"""端到端：经 Vite 代理(5173/api) 上传文档 → 流式研究 → 检查报告引用了本地知识库 → 清理
运行前需启动 uvicorn(8000) 与 vite(5173)。用法：python -m eval.e2e_rag round1"""
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://localhost:5173/api"
CORPUS = Path(__file__).resolve().parent / "rag" / "corpus"
FILES = ["xinghe_ops_handbook.txt", "langgraph_notes.md", "arxiv_2307.03172.pdf"]
Q = "星河智能 Agent 平台的超时与告警策略是怎样的？结合 LangGraph 的 checkpointer 断点续跑，应如何设计故障恢复？"


def main(tag: str):
    res = {"tag": tag}
    c = httpx.Client(timeout=300, trust_env=False)  # 绕过 Windows 系统代理(127.0.0.1:1088)，直连本机
    t = time.time()
    files = [("files", (f, open(CORPUS / f, "rb"))) for f in FILES]
    r = c.post(f"{BASE}/kb/upload", files=files)
    r.raise_for_status()
    ids = [x["doc"]["id"] for x in r.json()["results"] if x["ok"]]
    res["upload_ok"] = len(ids)
    res["upload_s"] = round(time.time() - t, 1)
    res["chunks"] = c.get(f"{BASE}/kb/docs").json()["total_chunks"]
    t = time.time()
    nodes, report, local_counts, score = [], "", [], []
    with c.stream("POST", f"{BASE}/research/stream", json={"question": Q}) as s:
        ev = None
        for line in s.iter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:") and ev in ("node", "done", "error"):
                d = json.loads(line[5:])
                if ev == "error":
                    raise RuntimeError(d["error"])
                if ev == "node":
                    nodes.append(d["node"])
                    if d["node"] == "researcher":
                        local_counts.append(d.get("local", 0))
                    if d["node"] == "reviewer":
                        score.append(d["score"])
                if ev == "done":
                    report = d["report"]
    res.update(seconds=round(time.time() - t, 1), researchers=nodes.count("researcher"),
               local_per_researcher=local_counts, review_scores=score,
               report_chars=len(report), local_refs=report.count("/api/kb/docs/"),
               mentions_120s="120" in report, mentions_8812="8812" in report)
    # 本地引用链接可打开
    import re
    links = sorted(set(re.findall(r"/api/kb/docs/\w+/file", report)))
    bad = [u for u in links if c.get(f"http://localhost:5173{u}").status_code != 200]
    res["local_links"], res["local_links_bad"] = len(links), len(bad)
    res["local_link_ok"] = bool(links) and not bad
    for i in ids:
        c.delete(f"{BASE}/kb/docs/{i}")
    res["kb_after_cleanup"] = len(c.get(f"{BASE}/kb/docs").json()["docs"])
    res["pass"] = bool(res["upload_ok"] == 3 and res["local_refs"] and res["local_link_ok"] and sum(local_counts))
    print(json.dumps(res, ensure_ascii=False, indent=1))
    out = Path(__file__).resolve().parent / "rag" / "results"
    (out / f"e2e_{tag}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"e2e_{tag}_report.md").write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "round")
