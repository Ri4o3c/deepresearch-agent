"""RAG 知识库测试：单元（切块/分词/RRF）+ 存储（临时库）+ API（TestClient）+ Researcher 集成
运行：python -m eval.test_rag  → 结果写到 eval/rag/results/test_roundN.json"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "eval" / "rag" / "corpus"
results = []


def check(name, fn):
    t = time.time()
    try:
        fn()
        results.append({"name": name, "ok": True, "s": round(time.time() - t, 2)})
        print(f"  ✓ {name}")
    except Exception as e:  # noqa: BLE001
        results.append({"name": name, "ok": False, "err": repr(e)[:300]})
        print(f"  ✗ {name}: {e!r}"[:400])


# ---------------- 单元 ----------------
from app.rag.ingest import chunk_text, chunk_document, load_pages  # noqa: E402
from app.rag.store import tokenize, KnowledgeBase  # noqa: E402
from app.rag.retrieve import rrf, hybrid_search  # noqa: E402


def t_chunk_size():
    text = ("这是一个用于测试切块的句子，长度适中。" * 120)
    cs = chunk_text(text, 500, 80)
    assert len(cs) >= 4 and all(len(c) <= 500 + 80 for c in cs), [len(c) for c in cs]


def t_chunk_overlap():
    text = "。".join(f"第{i}句话包含一些独特的编号{i}" for i in range(200))
    cs = chunk_text(text, 300, 60)
    assert any(cs[i][-20:] in cs[i + 1] for i in range(len(cs) - 1)), "相邻块应有重叠"


def t_chunk_short_and_empty():
    assert chunk_text("") == [] and chunk_text("短") == []
    assert chunk_text("一段足够长的文字，超过二十个字符的最小块长度要求。") != []


def t_pdf_pages():
    cs = chunk_document(CORPUS / "arxiv_2307.03172.pdf")
    pages = {c["page"] for c in cs}
    assert min(pages) == 1 and max(pages) > 10, pages


def t_gbk_txt():
    d = Path(tempfile.mkdtemp())
    (d / "g.txt").write_bytes("这是GBK编码的中文文本".encode("gbk"))
    assert "GBK" in load_pages(d / "g.txt")[0][1]
    shutil.rmtree(d)


def t_tokenize():
    toks = tokenize("LangGraph 的 checkpointer 支持断点续跑。")
    assert "langgraph" in toks and "checkpointer" in toks and "的" not in toks and "。" not in toks, toks


def t_rrf():
    a = [{"cid": "x"}, {"cid": "y"}, {"cid": "z"}]
    b = [{"cid": "y"}, {"cid": "w"}]
    f = rrf(a, b)
    assert f[0]["cid"] == "y" and {r["cid"] for r in f} == {"x", "y", "z", "w"}


# ---------------- 存储（临时库，调用 DashScope） ----------------
tmp_root = Path(tempfile.mkdtemp(prefix="kbtest_"))
store = KnowledgeBase(root=str(tmp_root))
state = {}


def t_add():
    d1 = store.add_file(CORPUS / "xinghe_ops_handbook.txt")
    d2 = store.add_file(CORPUS / "langgraph_notes.md")
    state["d1"], state["d2"] = d1, d2
    assert d1["chunks"] >= 1 and len(store.docs()) == 2 and not store.is_empty()


def t_bad_ext():
    p = tmp_root / "x.docx"
    p.write_text("abc")
    try:
        store.add_file(p)
        raise AssertionError("应拒绝 docx")
    except ValueError:
        pass
    assert len(store.docs()) == 2


def t_modes():
    for mode in ["vector", "bm25", "hybrid", "hybrid_rerank"]:
        hits = hybrid_search("合同审查任务超时时间", k=3, mode=mode, store=store)
        assert hits and hits[0]["file"] == "xinghe_ops_handbook.txt", (mode, hits[:1])


def t_bm25_refresh_after_delete():
    store.bm25_search("值班热线")  # 先建索引
    store.delete(state["d1"]["id"])
    hits = store.bm25_search("值班热线 8812")
    assert all(h["file"] != "xinghe_ops_handbook.txt" for h in hits)
    assert not store.delete("nope")


# ---------------- API ----------------
from fastapi.testclient import TestClient  # noqa: E402
import app.server as server  # noqa: E402
import app.rag.retrieve as retrieve  # noqa: E402

server.kb = store            # API 测试指向临时库，不污染用户知识库
retrieve._kb = store
client = TestClient(server.app)


def t_api_upload_list():
    with open(CORPUS / "xinghe_ops_handbook.txt", "rb") as f:
        r = client.post("/kb/upload", files=[("files", ("运维手册.txt", f, "text/plain"))])
    assert r.status_code == 200 and r.json()["results"][0]["ok"], r.text
    state["api_id"] = r.json()["results"][0]["doc"]["id"]
    r = client.get("/kb/docs")
    assert r.status_code == 200 and any(d["filename"] == "运维手册.txt" for d in r.json()["docs"])
    assert r.json()["total_chunks"] > 0


def t_api_mixed_upload():
    r = client.post("/kb/upload", files=[("files", ("a.exe", b"MZ", "application/octet-stream")),
                                         ("files", ("b.md", "# 标题\n这是一个混合上传测试文档，内容足够长。".encode(), "text/markdown"))])
    res = r.json()["results"]
    assert r.status_code == 200 and not res[0]["ok"] and res[1]["ok"], r.text
    client.delete(f"/kb/docs/{res[1]['doc']['id']}")


def t_api_reject():
    assert client.post("/kb/upload", files=[("files", ("a.exe", b"MZ", "x/y"))]).status_code == 400
    assert client.post("/kb/upload", files=[("files", ("e.txt", b"", "text/plain"))]).status_code == 400
    assert client.post("/kb/upload").status_code == 422


def t_api_file_and_search():
    r = client.get(f"/kb/docs/{state['api_id']}/file")
    assert r.status_code == 200 and "8812" in r.text
    r = client.get("/kb/search", params={"q": "值班热线是多少"})
    assert r.status_code == 200 and "8812" in " ".join(h["text"] for h in r.json()["hits"])
    assert client.get("/kb/search", params={"q": "x", "mode": "bad"}).status_code == 422


def t_api_delete():
    assert client.delete(f"/kb/docs/{state['api_id']}").status_code == 200
    assert client.delete(f"/kb/docs/{state['api_id']}").status_code == 404
    assert client.get(f"/kb/docs/{state['api_id']}/file").status_code == 404


def t_kb_search_tool():
    out = retrieve.kb_search("LangGraph 断点续跑需要什么")
    assert out and all(s["type"] == "local" and s["url"].startswith("/api/kb/docs/") and "page" in s for s in out), out


def t_researcher_local():
    from app import researcher
    store.add_file(CORPUS / "xinghe_ops_handbook.txt")
    note, sources = researcher.research("星河智能 Agent 平台的合同审查任务超时时间和值班热线是什么？")
    local = [s for s in sources if s.get("type") == "local"]
    assert local, "研究员应用到本地知识库来源"
    text = " ".join(f.point for f in note.findings)
    assert "120" in text or "8812" in text, text


def t_fix_local_links():
    from app.graph import fix_local_links
    src = [{"url": "/api/kb/docs/abc1234567/file#page=3", "file": "a.pdf", "page": 3, "type": "local"},
           {"url": "/api/kb/docs/abc1234567/file#page=5", "file": "a.pdf", "page": 5, "type": "local"}]
    bad = "[1] 📄本地：a.pdf 第5页 - /api/kb/docs/abc1234576/file#page=5\n[2] web - https://x.com/a"
    out = fix_local_links(bad, src)
    assert "/api/kb/docs/abc1234567/file#page=5" in out and "https://x.com/a" in out, out
    ok = "[1] 📄本地：a.pdf 第3页 - /api/kb/docs/abc1234567/file#page=3"
    assert fix_local_links(ok, src) == ok


def t_web_down_fallback():
    """Tavily 限额/断网时研究不崩溃，只用本地来源作答"""
    import app.researcher as R
    fake = [{"id": 1, "title": "运维手册.txt 第1页", "url": "/api/kb/docs/abc/file#page=1", "type": "local",
             "content": "单次 Agent 任务的默认超时时间为 45 秒，合同审查任务超时放宽到 120 秒。", "score": 0.9,
             "file": "运维手册.txt", "page": 1, "doc_id": "abc"}]
    def boom(*a, **k): raise RuntimeError("usage limit")
    old = R.search, R.search_local
    R.search, R.search_local = boom, (lambda q: fake)
    try:
        note, sources = R.research("合同审查任务的超时时间是多少？")
    finally:
        R.search, R.search_local = old
    assert sources and all(s["type"] == "local" for s in sources), sources


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "round"
    for name, fn in list(globals().items()):
        if name.startswith("t_") and callable(fn):
            check(name, fn)
    ok = sum(r["ok"] for r in results)
    print(f"\n{ok}/{len(results)} 通过")
    out = ROOT / "eval" / "rag" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"test_{tag}.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(tmp_root, ignore_errors=True)
