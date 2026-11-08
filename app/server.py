"""FastAPI 服务：流式推送每个 Agent 的进度，支持基于 checkpoint 的断点续跑"""
import json
import logging
import traceback
import sqlite3
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.concurrency import run_in_threadpool
import shutil
import tempfile
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel, Field, field_validator
from sse_starlette.sse import EventSourceResponse

from contextlib import asynccontextmanager

from app import observability
from app.graph import build_graph
from app.rag import kb
from app.rag.ingest import SUPPORTED

Path("data").mkdir(exist_ok=True)
_conn = sqlite3.connect("data/checkpoints.db", check_same_thread=False)
checkpointer = SqliteSaver(_conn)
graph = build_graph(checkpointer)

@asynccontextmanager
async def lifespan(_app):
    yield
    observability.flush()  # 关闭前把未发送的 trace 发到 Langfuse


app = FastAPI(title="DeepResearch API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ResearchRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500, description="研究问题，2到500字")

    @field_validator("question", mode="before")
    @classmethod
    def strip_question(cls, v):
        return v.strip() if isinstance(v, str) else v


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _summarize(node: str, upd: dict) -> dict:
    """把节点输出压缩成前端需要的事件，避免把整份笔记推给前端"""
    if node == "planner":
        return {"sub_questions": upd["sub_questions"]}
    if node == "researcher":
        n = upd["notes"][0]
        return {"question": n["question"], "findings": len(n["findings"]), "sources": len(n["sources"]),
                "local": sum(s.get("type") == "local" for s in n["sources"])}
    if node == "writer":
        return {"report": upd["report"]}
    if node == "reviewer":
        r = upd["reviews"][-1]
        return {"score": r["score"], "passed": r["passed"], "issues": r["issues"],
                "follow_up": r["follow_up_questions"]}
    return {}


def _event_stream(thread_id: str, inputs, tags: list[str] | None = None):
    yield {"event": "start", "data": json.dumps({"thread_id": thread_id})}
    try:
        for ev in graph.stream(inputs, observability.build_config(thread_id, (inputs or {}).get("question"), tags), stream_mode="updates"):
            for node, upd in ev.items():
                if node.startswith("__"):  # 跳过 LangGraph 内部事件（如 __metadata__）
                    continue
                yield {"event": "node", "data": json.dumps({"node": node, **_summarize(node, upd)},
                                                           ensure_ascii=False)}
        report = graph.get_state(_cfg(thread_id)).values.get("report", "")
        yield {"event": "done", "data": json.dumps({"thread_id": thread_id, "report": report},
                                                   ensure_ascii=False)}
    except Exception as e:  # 出错时告诉前端可以续跑
        logging.getLogger("uvicorn.error").error("research %s failed:\n%s", thread_id, traceback.format_exc())
        yield {"event": "error", "data": json.dumps({"thread_id": thread_id, "error": str(e),
                                                    "resumable": True}, ensure_ascii=False)}


@app.post("/research/stream")
def start_research(req: ResearchRequest):
    thread_id = uuid.uuid4().hex[:12]
    return EventSourceResponse(_event_stream(thread_id, {"question": req.question}, ["api", "start"]))


@app.post("/research/{thread_id}/resume")
def resume_research(thread_id: str):
    state = graph.get_state(_cfg(thread_id))
    if not state.values:
        raise HTTPException(404, "任务不存在")
    if not state.next:
        raise HTTPException(400, "任务已完成，无需续跑")
    return EventSourceResponse(_event_stream(thread_id, None, ["api", "resume"]))  # None = 从最近的 checkpoint 继续


@app.get("/research/{thread_id}")
def get_research(thread_id: str):
    state = graph.get_state(_cfg(thread_id))
    if not state.values:
        raise HTTPException(404, "任务不存在")
    v = state.values
    return {"thread_id": thread_id, "question": v.get("question"), "finished": not state.next,
            "next": list(state.next), "sub_questions": v.get("sub_questions", []),
            "notes": len(v.get("notes", [])), "reviews": v.get("reviews", []),
            "research_notes": [_summarize("researcher", {"notes": [note]}) for note in v.get("notes", [])],
            "report": v.get("report", "")}


MAX_UPLOAD_MB = 20


@app.post("/kb/upload")
async def kb_upload(files: list[UploadFile] = File(...)):
    """上传并入库（解析→切块→Embedding→Chroma），返回每个文件的结果"""
    results = []
    for f in files:
        name = Path(f.filename or "file").name
        ext = Path(name).suffix.lower()
        if ext not in SUPPORTED:
            results.append({"filename": name, "ok": False, "error": f"不支持的类型 {ext}，仅支持 PDF/MD/TXT"})
            continue
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            shutil.copyfileobj(f.file, tmp)
            tmp_path = Path(tmp.name)
        try:
            if tmp_path.stat().st_size > MAX_UPLOAD_MB * 1024 * 1024:
                raise ValueError(f"文件超过 {MAX_UPLOAD_MB}MB")
            if tmp_path.stat().st_size == 0:
                raise ValueError("文件为空")
            doc = await run_in_threadpool(kb.add_file, tmp_path, name)
            results.append({"filename": name, "ok": True, "doc": doc})
        except Exception as e:  # noqa: BLE001
            results.append({"filename": name, "ok": False, "error": str(e)})
        finally:
            tmp_path.unlink(missing_ok=True)
    if not any(r["ok"] for r in results):
        raise HTTPException(400, "；".join(f"{r['filename']}：{r['error']}" for r in results))
    return {"results": results}


@app.get("/kb/docs")
def kb_docs():
    docs = kb.docs()
    return {"docs": docs, "total_chunks": sum(d["chunks"] for d in docs)}


@app.delete("/kb/docs/{doc_id}")
def kb_delete(doc_id: str):
    if not kb.delete(doc_id):
        raise HTTPException(404, "文档不存在")
    return {"ok": True}


@app.get("/kb/docs/{doc_id}/file")
def kb_file(doc_id: str):
    doc, path = kb.get_doc(doc_id), kb.file_path(doc_id)
    if not doc or not path.exists():
        raise HTTPException(404, "文档不存在")
    media = {".pdf": "application/pdf"}.get(doc["ext"], "text/plain; charset=utf-8")
    return FileResponse(path, media_type=media, filename=doc["filename"], content_disposition_type="inline")


@app.get("/kb/search")
def kb_search_api(q: str, k: int = 5, mode: str = "hybrid_rerank"):
    """调试用：直接查看某个问题的本地检索结果"""
    from app.rag import hybrid_search
    if mode not in {"vector", "bm25", "hybrid", "hybrid_rerank"}:
        raise HTTPException(422, "mode 取值错误")
    return {"hits": [{k_: v for k_, v in h.items() if k_ != "text"} | {"text": h["text"][:300]}
                     for h in hybrid_search(q, k=min(k, 20), mode=mode)]}


@app.get("/health")
def health():
    return {"ok": True}

