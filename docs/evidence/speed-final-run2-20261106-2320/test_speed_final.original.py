"""One fail-fast offline configuration comparison and real narrow graph/resume run.

No production database, network, real model/client, or previous test suite is used.
"""
import copy
from contextlib import ExitStack, redirect_stdout, redirect_stderr
import datetime
import io
import json
import logging
import os
from pathlib import Path
import re
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import traceback
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence/speed-final-20261106"
CHECKS, COMPARISON, CONTEXTS = [], [], []
LLM_CALLS = {}
VIOLATIONS = dict(network=0, external_client=0, forbidden_file=0)
CHAIN = {}
LOCK = threading.Lock()


def check(label, condition):
    CHECKS.append(dict(type=label, ok=bool(condition)))
    if not condition:
        raise AssertionError(label)


def deny_network(*args, **kwargs):
    VIOLATIONS["network"] += 1
    raise RuntimeError("network forbidden in offline round")


def deny_client(*args, **kwargs):
    VIOLATIONS["external_client"] += 1
    raise RuntimeError("external client forbidden in offline round")


def audit(event, args):
    if event != "open" or not isinstance(args[0], (str, bytes, os.PathLike)):
        return
    path = Path(os.fsdecode(args[0])).resolve()
    if path.name == ".env" or path.name == "settings.runtime.yml" or path.is_relative_to(ROOT / "data"):
        VIOLATIONS["forbidden_file"] += 1
        raise RuntimeError("credentials or production data forbidden")
    mode, flags = args[1], args[2]
    writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
    if writing and path.is_relative_to(ROOT) and not path.is_relative_to(EVIDENCE):
        VIOLATIONS["forbidden_file"] += 1
        raise RuntimeError("repository write forbidden during tests")


WEB = [dict(title=f"纯合成网页{i}", url=f"https://example.invalid/synthetic/{i}",
            content="合成摘要" * 10, score=0.9) for i in range(4)]
WEB.append(dict(WEB[0], url=WEB[0]["url"] + "#duplicate"))
LOCAL = [dict(title=f"纯合成手册第{page}页", url=f"/api/kb/docs/syntheticdoc/file#page={page}",
              type="local", file="synthetic-manual.txt", page=page, doc_id="syntheticdoc",
              content="合成分页证据" * 300, score=0.9) for page in (1, 2)]


class Provider:
    name = "offline-synthetic"

    def __init__(self):
        self.calls = 0
        self.fetches = 0

    def search(self, query, max_results):
        with LOCK:
            self.calls += 1
        return copy.deepcopy(WEB[:max_results])

    def fetch(self, url):
        with LOCK:
            self.fetches += 1
        return "合成网页正文" * 300


class FakeLLM:
    """Only the model boundary is replaced; all prompts are built by real app code."""
    def __init__(self, *args, schema=None, **kwargs):
        self.schema = schema

    def with_structured_output(self, schema, **kwargs):
        return FakeLLM(schema=schema)

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        kind = self.schema.__name__ if self.schema else "writer"
        with LOCK:
            LLM_CALLS[kind] = LLM_CALLS.get(kind, 0) + 1
        text = messages[-1].content
        if kind == "ResearchPlan":
            return self.schema(topic="纯合成离线主题", sub_questions=[
                dict(question=q, reason="纯合成夹具") for q in ("纯合成子问题甲", "纯合成子问题乙")])
        if kind == "Relevance":
            return self.schema(relevant_ids=[int(i) for i in re.findall(r"^\[(\d+)\]", text, re.M)])
        if kind == "ResearchNote":
            from app.search.session import current_session
            context = text.split("检索资料：\n", 1)[1]
            ids = [int(i) for i in re.findall(r"^\[(\d+)\]", context, re.M)]
            question = text.split("子问题：", 1)[1].split("\n", 1)[0]
            with LOCK:
                CONTEXTS.append(dict(question=question, ids=ids, chars=len(context),
                                     search_metrics=dict(current_session.get().metrics)))
            return self.schema(question=question, findings=[
                dict(point=f"纯合成证据{i}", source_ids=[i]) for i in ids])
        if kind == "Review":
            return self.schema(score=8, passed=True, issues=[], follow_up_questions=[])
        check("writer_boundary_called_with_real_notes", kind == "writer")
        reference_text = text.split("# 参考资料\n", 1)[1]
        refs = re.findall(r"^\[(\d+)\] (.+) - (.+)$", reference_text, re.M)
        check("writer_real_global_ids", [int(row[0]) for row in refs] == [1, 2])
        check("writer_real_local_page_urls", {row[2] for row in refs} == {r["url"] for r in LOCAL})
        check("writer_dropped_web_absent", "example.invalid" not in text and "纯合成网页" not in text)
        note_text = text.split("# 参考资料", 1)[0]
        check("writer_findings_remapped", set(re.findall(r"\[(\d+)\]", note_text)) == {"1", "2"})
        # The fake model echoes the actual real-writer references. This tests plumbing,
        # not a real model's citation fidelity or report quality.
        return AIMessage(content="# 纯合成报告\n离线证据[1][2]。\n\n# 参考资料\n" + reference_text)


def compare(R, S):
    from app.search.session import SearchSession
    for phase, context_limit, fetch_top in (("config_6000_fetch2", 6000, 2), ("config_1800_fetch1", 1800, 1)):
        provider = Provider()
        session = SearchSession(capacity=32, ttl=120, clock=lambda: 0.0)
        with mock.patch.object(R, "CONTEXT_LIMIT", context_limit), mock.patch.object(R, "SOURCE_LIMIT", 1500), \
             mock.patch.object(S, "FETCH_TOP", fetch_top), mock.patch.object(S, "CONTENT_LIMIT", 1500), \
             mock.patch.object(S, "fetch_text", side_effect=provider.fetch):
            for mode in ("cold", "warm", "bypass", "after_bypass", "independent_session"):
                selected = SearchSession(capacity=32, ttl=120, clock=lambda: 0.0) if mode == "independent_session" else session
                before = dict(selected.metrics)
                calls, fetches = provider.calls, provider.fetches
                tick = time.perf_counter()
                web = S.web_search("纯合成固定查询", max_results=8, providers=[provider], session=selected,
                                   bypass_cache=mode == "bypass")
                sources = [dict(row, id=i+1) for i, row in enumerate(web + copy.deepcopy(LOCAL))]
                context, kept = R.build_context(sources)
                delta = {key: selected.metrics[key] - before[key] for key in before}
                row = dict(phase=phase, call=mode, context_limit=context_limit, fetch_top=fetch_top,
                           search_calls_delta=provider.calls-calls, fetch_calls_delta=provider.fetches-fetches,
                           cache_hits_delta=delta["cache_hits"], cache_misses_delta=delta["cache_misses"],
                           cache_bypass_delta=delta["cache_bypass"], input_sources=len(sources), kept_sources=len(kept),
                           kept_ids=[r["id"] for r in kept], context_chars=len(context), tokens=None,
                           elapsed_s=time.perf_counter()-tick)
                COMPARISON.append(row)
                check(phase+":"+mode+":url_dedupe", len(web) == 4)
                check(phase+":"+mode+":budget", len(context) <= context_limit)
                check(phase+":"+mode+":local_fragments_preserved",
                      {r["url"] for r in kept if r.get("type") == "local"} == {r["url"] for r in LOCAL})
                expected = 0 if mode == "warm" else 1
                check(phase+":"+mode+":request_delta", row["search_calls_delta"] == expected)
                check(phase+":"+mode+":fetch_delta", row["fetch_calls_delta"] == fetch_top*expected)
                check(phase+":"+mode+":cache_delta",
                      (row["cache_hits_delta"], row["cache_misses_delta"], row["cache_bypass_delta"])
                      == ((1, 0, 0) if mode == "warm" else (0, 0, 1) if mode == "bypass" else (0, 1, 0)))
        check(phase+":stable_context", len({r["context_chars"] for r in COMPARISON if r["phase"] == phase}) == 1)
    check("configuration_context_difference", COMPARISON[5]["context_chars"] < COMPARISON[0]["context_chars"])
    check("configuration_budget_drops_source", COMPARISON[5]["kept_sources"] < COMPARISON[5]["input_sources"])


def chain(R, S, G, server):
    from sse_starlette.event import ServerSentEvent
    provider = Provider()
    with mock.patch.object(R, "CONTEXT_LIMIT", 1800), mock.patch.object(R, "SOURCE_LIMIT", 1500), \
         mock.patch.object(S, "FETCH_TOP", 1), mock.patch.object(S, "CONTENT_LIMIT", 1500), \
         mock.patch.object(S, "get_providers", return_value=[provider]), \
         mock.patch.object(S, "fetch_text", side_effect=provider.fetch), \
         mock.patch.object(R, "kb_search", side_effect=lambda *a, **k: copy.deepcopy(LOCAL)):
        cfg = server._cfg("synthetic-resume")
        prefix = list(server.graph.stream({"question": "纯合成离线主题"}, cfg,
                                         stream_mode="updates", interrupt_after=["researcher"]))
        snapshot = server.graph.get_state(cfg)
        notes = snapshot.values["notes"]
        check("checkpoint_writer_pending", "writer" in snapshot.next)
        check("checkpoint_two_research_notes", len(notes) == 2)
        check("research_real_budget_applied", len(CONTEXTS) == 2 and all(c["chars"] <= 1800 for c in CONTEXTS))
        for note in notes:
            check("note_budget_removed_web:"+note["question"], len(note["sources"]) == 2 and
                  all(s["type"] == "local" for s in note["sources"]))
            check("note_local_page_identity:"+note["question"], {s["url"] for s in note["sources"]} == {s["url"] for s in LOCAL})
            check("note_original_ids_retained:"+note["question"], [s["id"] for s in note["sources"]] == [5, 6])
            check("note_findings_source_ids_valid:"+note["question"], {i for f in note["findings"] for i in f["source_ids"]} == {5, 6})
        summaries = [server._summarize("researcher", update["researcher"]) for update in prefix if "researcher" in update]
        check("researcher_event_summary_contract", len(summaries) == 2 and all(s["sources"] == 2 and s["local"] == 2 for s in summaries))
        before = dict(LLM_CALLS)
        search_before, fetch_before = provider.calls, provider.fetches
        # Rebuild a REAL compiled graph on the same isolated in-memory SqliteSaver.
        server.graph = G.build_graph(server.checkpointer)
        response = server.resume_research("synthetic-resume")
        check("resume_route_returns_sse", response.media_type == "text/event-stream")
        # No HTTP/ASGI loop. Consume the same real resume generator directly, with None input.
        events = list(server._event_stream("synthetic-resume", None, ["resume", "offline"]))
        check("resume_real_event_order", [e["event"] for e in events] == ["start", "node", "node", "done"])
        nodes = [json.loads(e["data"])["node"] for e in events if e["event"] == "node"]
        check("resume_only_writer_reviewer", nodes == ["writer", "reviewer"])
        check("resume_no_search_fetch_repeat", provider.calls == search_before and provider.fetches == fetch_before)
        check("resume_no_completed_model_repeat", all(LLM_CALLS.get(k, 0) == before.get(k, 0)
              for k in ("ResearchPlan", "Relevance", "ResearchNote")))
        reports = [json.loads(e["data"])["report"] for e in events if e["event"] == "done" or
                   e["event"] == "node" and json.loads(e["data"])["node"] == "writer"]
        check("writer_done_reports_match", len(reports) == 2 and reports[0] == reports[1])
        for i, report in enumerate(reports):
            refs = {int(n): url for n, url in re.findall(r"^\[(\d+)\] .+ - (.+)$", report, re.M)}
            body = report.split("# 参考资料", 1)[0]
            check("stream_resolvable_citations:"+str(i), set(map(int, re.findall(r"\[(\d+)\]", body))) <= refs.keys() and len(refs) == 2)
            check("stream_preserves_page_links:"+str(i), set(refs.values()) == {s["url"] for s in LOCAL})
        for i, event in enumerate(events):
            wire = ServerSentEvent(**event).encode().decode("utf-8")
            check("real_sse_encoding:"+str(i), wire.endswith("\r\n\r\n") and "data: " in wire)
        final = server.get_research("synthetic-resume")
        check("resume_completed_state", final["finished"] and final["notes"] == 2)
        CHAIN.update(dict(research_input_sources_per_note=6, kept_sources_per_note=2,
                          local_ids_before_writer=[5, 6], writer_global_ids=[1, 2],
                          context_measurements=copy.deepcopy(CONTEXTS),
                          initial_search_calls=search_before, initial_fetch_calls=fetch_before,
                          resume_search_calls_delta=provider.calls-search_before,
                          resume_fetch_calls_delta=provider.fetches-fetch_before,
                          model_calls_before_resume=before,
                          model_calls_resume_delta={k: LLM_CALLS.get(k, 0)-before.get(k, 0) for k in LLM_CALLS},
                          events=[e["event"] for e in events], nodes=nodes,
                          tokens=None, checkpoint="real SqliteSaver, isolated in-memory SQLite",
                          asgi_transport_consumed=False, process_crash_tested=False))


def run():
    temporary_root = tempfile.gettempdir()
    # Remove inherited credentials without inspecting or serializing their values.
    os.environ.clear()
    os.environ.update(SystemRoot="C:/Windows", TEMP=temporary_root, TMP=temporary_root,
                      LANGCHAIN_TRACING_V2="false", ANONYMIZED_TELEMETRY="False")
    # This fake value is only for the real get_llm factory's lookup; its constructor is replaced.
    os.environ["DEEPSEEK_API_KEY"] = "offline-placeholder"
    sys.path.insert(0, str(ROOT))
    sys.addaudithook(audit)
    logging.disable(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix="deep-research-final-offline-", dir=temporary_root) as temporary, ExitStack() as stack:
        stack.enter_context(mock.patch.object(socket.socket, "connect", side_effect=deny_network))
        stack.enter_context(mock.patch.object(socket.socket, "connect_ex", side_effect=deny_network))
        stack.enter_context(mock.patch.object(socket, "create_connection", side_effect=deny_network))
        stack.enter_context(mock.patch.object(socket, "getaddrinfo", side_effect=deny_network))
        import httpx
        stack.enter_context(mock.patch.object(httpx.Client, "__init__", side_effect=deny_client))
        stack.enter_context(mock.patch.object(httpx.AsyncClient, "__init__", side_effect=deny_client))
        import openai
        stack.enter_context(mock.patch.object(openai.OpenAI, "__init__", side_effect=deny_client))
        stack.enter_context(mock.patch.object(openai.AsyncOpenAI, "__init__", side_effect=deny_client))
        import chromadb
        # Block persistent external storage BEFORE any app modules can instantiate it.
        client = mock.Mock()
        client.get_or_create_collection.return_value.count.return_value = 0
        stack.enter_context(mock.patch.object(chromadb, "PersistentClient", return_value=client))
        import langchain_openai
        stack.enter_context(mock.patch.object(langchain_openai, "ChatOpenAI", FakeLLM))
        real_connect = sqlite3.connect
        connections = []

        def connect_memory(*args, **kwargs):
            connection = real_connect(":memory:", check_same_thread=False)
            connections.append(connection)
            return connection

        stack.enter_context(mock.patch.object(sqlite3, "connect", side_effect=connect_memory))
        original = Path.cwd()
        os.chdir(temporary)
        try:
            from app import researcher as R, search as S, graph as G, server
            compare(R, S)
            chain(R, S, G, server)
            check("mock_model_call_counts", LLM_CALLS == dict(ResearchPlan=1, Relevance=2, ResearchNote=2, writer=1, Review=1))
            check("all_forbidden_boundaries_unused", all(v == 0 for v in VIOLATIONS.values()))
            check("temporary_checkpoint_not_disk_database", len(connections) == 1)
        finally:
            for connection in connections:
                connection.close()
            os.chdir(original)


if __name__ == "__main__":
    started = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()
    tick = time.perf_counter()
    failure = None
    output = io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(output):
            run()
    except Exception as error:
        failure = dict(type=type(error).__name__,
                       failed_assertion=next((c["type"] for c in reversed(CHECKS) if not c["ok"]), None),
                       frames=[dict(file=Path(f.filename).name, line=f.lineno, function=f.name)
                               for f in traceback.extract_tb(error.__traceback__)][-8:])
    # Do not export raw imports/model/exception output or local values.
    summary = dict(started=started,
                   ended=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(),
                   elapsed_s=time.perf_counter()-tick, comparison_type="current_implementation_configuration_comparison",
                   passed=sum(c["ok"] for c in CHECKS), failed=sum(not c["ok"] for c in CHECKS),
                   exit_code=1 if failure else 0, failure=failure, checks=CHECKS,
                   per_call_comparison=COMPARISON, chain=CHAIN, mock_llm_calls=LLM_CALLS,
                   guards=VIOLATIONS, tokens=None, real_llm_calls=0, real_research_requests=0,
                   not_covered=["live model quality", "real search/embedding/rerank", "HTTP/ASGI transport",
                                "process crash/restart persistence", "reflection follow-up round", "production latency/tokens"])
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (EVIDENCE / "exit.txt").write_text(str(summary["exit_code"])+"\n", encoding="utf-8")
    log = dict(started=started, ended=summary["ended"], elapsed_s=summary["elapsed_s"], passed=summary["passed"],
               failed=summary["failed"], exit_code=summary["exit_code"], failure=failure, guards=VIOLATIONS,
               fail_fast=True, rerun=False)
    (EVIDENCE / "round.log").write_text(json.dumps(log, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(log, ensure_ascii=False))
    raise SystemExit(summary["exit_code"])
