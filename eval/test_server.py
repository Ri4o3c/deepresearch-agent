"""服务接口测试：正常流程 / 异常输入 / 并发 / 客户端断开 / 进程崩溃续跑"""
import json, subprocess, sys, time, threading
import httpx

B = "http://127.0.0.1:8000"
PY = r".venv\Scripts\python.exe"
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail else ""), flush=True)


def start_server():
    try:  # 防止旧进程占着 8000 端口，导致测到的是旧代码/旧环境
        httpx.get(B + "/health", timeout=1)
        raise RuntimeError("端口 8000 已被其他服务占用，请先关闭")
    except httpx.TransportError:
        pass
    p = subprocess.Popen([PY, "-m", "uvicorn", "app.server:app", "--port", "8000"],
                         stdout=open("data/server.out.log", "a"), stderr=open("data/server.err.log", "a"))
    for _ in range(40):
        try:
            if httpx.get(B + "/health", timeout=1).status_code == 200:
                return p
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("server not up")


def sse(method, url, **kw):
    with httpx.stream(method, url, timeout=httpx.Timeout(300, connect=5), **kw) as r:
        if r.status_code != 200:
            r.read()
            yield "http_error", {"status": r.status_code, "body": r.text}
            return
        ev = None
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                yield ev, json.loads(line[5:].strip())


def run_full(q):
    events = list(sse("POST", B + "/research/stream", json={"question": q}))
    return events


def main(round_no):
    srv = start_server()
    try:
        # 1. 正常流程
        t0 = time.time()
        evs = run_full("RAG 和微调各适合什么场景？")
        names = [e for e, _ in evs]
        nodes = [d.get("node") for e, d in evs if e == "node"]
        check("正常流程: 以 start 开头 done 结尾", names[:1] == ["start"] and names[-1:] == ["done"], f"{names[:1]}..{names[-1:]}")
        check("正常流程: 节点顺序 planner→researcher→writer→reviewer",
              nodes[:1] == ["planner"] and "writer" in nodes and nodes[-1] == "reviewer", ",".join(nodes))
        check("正常流程: 无内部事件泄露", not any(n and n.startswith("__") for n in nodes))
        done = evs[-1][1] if names[-1:] == ["done"] else {}
        tid = evs[0][1]["thread_id"]
        check("正常流程: done 含报告", len(done.get("report", "")) > 500, f"{len(done.get('report',''))} 字, {time.time()-t0:.0f}s")
        st = httpx.get(f"{B}/research/{tid}").json()
        check("正常流程: GET 显示已完成", st.get("finished") is True)

        # 2. 异常输入
        r = httpx.post(B + "/research/stream", timeout=10, json={"question": ""})
        check("空问题被拒绝(4xx)", 400 <= r.status_code < 500, f"status={r.status_code}")
        r = httpx.post(B + "/research/stream", timeout=10, json={"question": "   "})
        check("纯空格问题被拒绝(4xx)", 400 <= r.status_code < 500, f"status={r.status_code}")
        r = httpx.post(B + "/research/stream", timeout=10, json={"question": "a" * 5000})
        check("超长问题被拒绝(4xx)", 400 <= r.status_code < 500, f"status={r.status_code}")
        r = httpx.post(B + "/research/stream", timeout=10, json={})
        check("缺少字段返回 422", r.status_code == 422, f"status={r.status_code}")
        check("查询不存在任务返回 404", httpx.get(B + "/research/nope").status_code == 404)
        check("续跑不存在任务返回 404", httpx.post(B + "/research/nope/resume").status_code == 404)
        check("续跑已完成任务返回 400", httpx.post(f"{B}/research/{tid}/resume").status_code == 400)

        # 3. 并发两个任务
        out = {}
        def worker(k, q): out[k] = run_full(q)
        ts = [threading.Thread(target=worker, args=(k, q)) for k, q in
              [("a", "向量数据库 Milvus 和 Chroma 怎么选？"), ("b", "什么是 Agentic RAG？")]]
        t0 = time.time(); [t.start() for t in ts]; [t.join() for t in ts]
        ok = all(out[k] and out[k][-1][0] == "done" for k in out)
        tids = {out[k][0][1]["thread_id"] for k in out}
        check("并发 2 任务均完成", ok, f"{time.time()-t0:.0f}s, 结尾事件={[out[k][-1][0] for k in out]}")
        check("并发任务 thread_id 不同", len(tids) == 2)

        # 4. 客户端中途断开：任务应被取消或可续跑，状态一致
        tid2 = None
        for e, d in sse("POST", B + "/research/stream", json={"question": "LLM 推理加速有哪些主流技术？"}):
            if e == "start": tid2 = d["thread_id"]
            if e == "node" and d["node"] == "planner": break
        time.sleep(3)
        st = httpx.get(f"{B}/research/{tid2}").json()
        check("客户端断开后可查询状态", "finished" in st, f"finished={st.get('finished')} next={st.get('next')}")
        if not st["finished"]:
            evs = list(sse("POST", f"{B}/research/{tid2}/resume"))
            check("客户端断开后可续跑完成", evs and evs[-1][0] == "done", evs[-1][0] if evs else "无事件")
        else:
            check("客户端断开后可续跑完成", True, "断开前已完成")

        # 5. 进程崩溃续跑
        tid3, seen = None, 0
        for e, d in sse("POST", B + "/research/stream", json={"question": "Prompt Caching 是什么，能省多少钱？"}):
            if e == "start": tid3 = d["thread_id"]
            if e == "node" and d["node"] == "researcher":
                seen += 1
                if seen == 2: break
        srv.kill(); srv.wait(); time.sleep(1)
        srv = start_server()
        st = httpx.get(f"{B}/research/{tid3}").json()
        check("崩溃后状态保留", st.get("notes", 0) >= 2 and not st["finished"], f"notes={st.get('notes')} next={len(st.get('next',[]))}")
        evs = list(sse("POST", f"{B}/research/{tid3}/resume"))
        st = httpx.get(f"{B}/research/{tid3}").json()
        check("崩溃续跑完成", evs and evs[-1][0] == "done", evs[-1][0] if evs else "无事件")
        check("崩溃续跑后笔记无重复且与子问题一一对应", st["notes"] == len(st["sub_questions"]) == len(set(st["sub_questions"])),
              f"notes={st['notes']} sub_questions={len(st['sub_questions'])}")
    finally:
        srv.kill()
    fails = [r for r in results if not r[1]]
    print(f"\n第 {round_no} 轮: {len(results)-len(fails)}/{len(results)} 通过")
    json.dump([{"name": n, "ok": o, "detail": d} for n, o, d in results],
              open(f"eval/server_round{round_no}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "1")

